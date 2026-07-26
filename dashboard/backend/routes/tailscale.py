"""Tailscale VPN integration — connect/disconnect/status via Auth Key."""

import logging
import os
import re
import subprocess
from flask import Blueprint, jsonify, request, abort
from flask_login import current_user, login_required

from secret_redaction import redact_secrets

log = logging.getLogger(__name__)
bp = Blueprint("tailscale", __name__)

_TAILSCALE_BINARY = "/usr/bin/tailscale"
_AUTH_KEY_RE = re.compile(r"^tskey-auth-[a-zA-Z0-9_-]+$")
_HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
_AUTH_KEY_IN_TEXT_RE = re.compile(r"tskey-auth-[a-zA-Z0-9_-]+")


def _require_manage() -> None:
    from models import has_permission

    if not has_permission(current_user.role, "config", "manage"):
        abort(403)


def _safe_error(detail: str) -> str:
    return redact_secrets(_AUTH_KEY_IN_TEXT_RE.sub("[REDACTED]", detail), limit=500).strip()


def _hermes_remote_auth_configured() -> bool:
    return bool(
        os.environ.get("EVONEXUS_HERMES_USERNAME", "").strip()
        and os.environ.get("EVONEXUS_HERMES_PASSWORD", "").strip()
    )


def _enable_hermes_serve():
    if not _hermes_remote_auth_configured():
        return subprocess.CompletedProcess(
            args=[_TAILSCALE_BINARY, "serve"],
            returncode=1,
            stdout="",
            stderr="Hermes remote authentication is not configured",
        )
    return _tailscale(
        "serve",
        "--bg",
        "--tcp=9119",
        "tcp://127.0.0.1:9119",
    )


def _tailscale(*args, timeout=15) -> subprocess.CompletedProcess:
    """Run a tailscale command and return the result."""
    cmd = [_TAILSCALE_BINARY] + list(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "TAILSCALE_SOCKET": "/var/run/tailscale/tailscaled.sock"},
    )


def _get_status() -> dict:
    """Return current Tailscale connection status."""
    try:
        result = _tailscale("status", "--json")
        if result.returncode != 0:
            return {"connected": False, "error": _safe_error(result.stderr) or "not running"}
        import json

        data = json.loads(result.stdout)
        if data.get("BackendState") != "Running":
            return {"connected": False}
        # Find the current node (Self)
        self_node = data.get("Self", {})
        return {
            "connected": True,
            "ip": self_node.get("TailscaleIPs", [None])[0],
            "hostname": self_node.get("HostName", ""),
            "tailnet": data.get("MagicDNSSuffix", ""),
            "online": self_node.get("Online", False),
        }
    except subprocess.TimeoutExpired:
        return {"connected": False, "error": "command timeout"}
    except FileNotFoundError:
        return {"connected": False, "error": "tailscale binary not found"}
    except Exception as exc:
        safe_error = _safe_error(str(exc))
        log.warning("tailscale status error: %s", safe_error)
        return {"connected": False, "error": safe_error}


@bp.route("/api/tailscale/status")
@login_required
def tailscale_status():
    """Return the current Tailscale connection status."""
    return jsonify(_get_status())


@bp.route("/api/tailscale/connect", methods=["POST"])
@login_required
def tailscale_connect():
    """Connect to Tailscale using an Auth Key.

    Body: {auth_key: str}
    """
    _require_manage()
    if not _hermes_remote_auth_configured():
        abort(503, description="Hermes remote authentication is required for Tailscale access")
    data = request.get_json(silent=True) or {}
    auth_key = (data.get("auth_key") or "").strip()

    if not auth_key:
        abort(400, description="auth_key is required")

    if not _AUTH_KEY_RE.match(auth_key):
        abort(400, description="auth_key must start with 'tskey-auth-'")

    hostname = os.environ.get("EVONEXUS_TAILSCALE_HOSTNAME", "evonexus-hermes").strip()
    if not _HOSTNAME_RE.fullmatch(hostname):
        abort(500, description="EVONEXUS_TAILSCALE_HOSTNAME is invalid")

    # Check if already connected, but still repair the Serve mapping. This
    # supports upgrades from versions that connected the node without exposing
    # the loopback-only Hermes UI from userspace-networking mode.
    current = _get_status()
    if current.get("connected"):
        serve_result = _enable_hermes_serve()
        if serve_result.returncode != 0:
            abort(502, description=f"Failed to expose Hermes UI: {_safe_error(serve_result.stderr)}")
        return jsonify({"connected": True, "ip": current.get("ip"), "already": True})

    # Attempt connection.
    # Daemon runs with --tun=userspace-networking (without NET_ADMIN), so
    # --accept-routes is omitted: in userspace mode the node cannot
    # install kernel routes / act as a subnet router. --accept-dns keeps MagicDNS.
    result = _tailscale(
        "up",
        "--authkey=" + auth_key,
        "--accept-dns",
        "--hostname=" + hostname,
        "--reset",
    )

    if result.returncode != 0:
        log.error("tailscale up failed: %s", _safe_error(result.stderr))
        abort(502, description="Tailscale connection failed")

    # Verify connection
    new_status = _get_status()
    if not new_status.get("connected"):
        abort(502, description="Connection command succeeded but Tailscale is not connected")

    # Userspace networking has no kernel TUN interface. Publish the standalone
    # Hermes HTTP listener explicitly over the tailnet instead of claiming that
    # binding 0.0.0.0 alone makes the port reachable.
    serve = _enable_hermes_serve()
    if serve.returncode != 0:
        log.error("tailscale serve failed: %s", _safe_error(serve.stderr))
        _tailscale("down")
        abort(502, description="Tailscale connected but Hermes exposure failed")

    from models import audit

    audit(current_user, "tailscale.connect", resource="integrations", detail=f"hostname={hostname}")

    return jsonify(
        {
            "connected": True,
            "ip": new_status.get("ip"),
            "hostname": new_status.get("hostname"),
        }
    )


@bp.route("/api/tailscale/disconnect", methods=["POST"])
@login_required
def tailscale_disconnect():
    """Disconnect from Tailscale."""
    _require_manage()
    current = _get_status()
    if not current.get("connected"):
        return jsonify({"connected": False})

    result = _tailscale("down")
    if result.returncode != 0:
        log.error("tailscale down failed: %s", _safe_error(result.stderr))
        abort(502, description="Tailscale disconnect failed")

    from models import audit

    audit(current_user, "tailscale.disconnect", resource="integrations")

    return jsonify({"connected": False})


@bp.route("/api/tailscale/whoami")
@login_required
def tailscale_whoami():
    """Return Tailscale identity information."""
    try:
        result = _tailscale("whoami", "--json")
        if result.returncode != 0:
            abort(502, description=_safe_error(result.stderr))
        import json

        return jsonify(json.loads(result.stdout))
    except subprocess.TimeoutExpired:
        abort(504, description="command timeout")
    except FileNotFoundError:
        abort(503, description="tailscale binary not found")
