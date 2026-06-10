"""EvoNexus Licensing — register-only, no heartbeat or monitoring.

Privacy-first: only records who installed. No continuous monitoring.

Protocol:
  POST /v1/register/direct  — register with email/name, receive api_key
  POST /v1/register/auto    — headless register by email (must exist server-side)
  POST /v1/activate         — validate existing api_key on startup
  GET  /api/geo             — geo-lookup from client IP
"""

import hashlib
import hmac as hmac_mod
import os
import socket
import uuid
import logging
from datetime import datetime, timezone

import requests

logger = logging.getLogger("licensing")

LICENSING_SERVER = "https://license.evolutionfoundation.com.br"
PRODUCT = "evo-nexus"
TIER = "evo-nexus"
TIMEOUT = 10


def _get_version() -> str:
    """Read version from pyproject.toml."""
    try:
        from pathlib import Path
        pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
        for line in pyproject.read_text().splitlines():
            if line.startswith("version"):
                return line.split('"')[1]
    except Exception:
        pass
    return "unknown"


VERSION = _get_version()


# ── Instance ID (hardware-based) ─────────────

def generate_instance_id() -> str:
    """Generate unique ID based on hardware (hostname + MAC). Deterministic per machine."""
    hostname = socket.gethostname()
    mac = uuid.getnode()
    raw = f"{hostname}-{mac}-{PRODUCT}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ── HMAC Signing ─────────────────────────────

def _hmac_sign(api_key: str, body: str) -> str:
    """HMAC-SHA256 signature for authenticated requests."""
    return hmac_mod.new(api_key.encode(), body.encode(), hashlib.sha256).hexdigest()


# ── RuntimeConfig persistence (DB) ───────────

def get_runtime_config(key: str) -> str | None:
    from models import RuntimeConfig
    try:
        row = RuntimeConfig.query.filter_by(key=key).first()
        return row.value if row else None
    except Exception:
        return None


def set_runtime_config(key: str, value: str):
    from models import db, RuntimeConfig
    try:
        row = RuntimeConfig.query.filter_by(key=key).first()
        if row:
            row.value = value
            row.updated_at = datetime.now(timezone.utc)
        else:
            row = RuntimeConfig(key=key, value=value)
            db.session.add(row)
        db.session.commit()
    except Exception as e:
        logger.warning(f"Failed to save config {key}: {e}")


# ── Transport (HTTP client) ──────────────────

def _post(path: str, payload: dict, api_key: str | None = None) -> dict:
    """POST to licensing server. If api_key provided, signs with HMAC."""
    import json
    url = f"{LICENSING_SERVER}{path}"
    body = json.dumps(payload)

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"EvoNexus/{VERSION}",
    }

    if api_key:
        signature = _hmac_sign(api_key, body)
        headers["Authorization"] = f"HMAC {signature}"

    resp = requests.post(url, data=body, headers=headers, timeout=TIMEOUT)
    if not resp.ok:
        try:
            detail = resp.json().get("detail", resp.text[:200])
        except Exception:
            detail = resp.text[:200]
        raise requests.HTTPError(f"{resp.status_code} {resp.reason}: {detail}", response=resp)
    return resp.json()


def _get(path: str, headers: dict | None = None) -> dict:
    """GET from licensing server."""
    url = f"{LICENSING_SERVER}{path}"
    h = {"Accept": "application/json", "User-Agent": f"EvoNexus/{VERSION}"}
    if headers:
        h.update(headers)
    resp = requests.get(url, headers=h, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# ── Geo Lookup ───────────────────────────────

def geo_lookup(client_ip: str | None = None) -> dict:
    """Get geo data from licensing server based on client IP."""
    if not client_ip:
        return {}
    try:
        return _get("/api/geo", headers={"X-Forwarded-For": client_ip})
    except Exception:
        return {}


# ── Direct Registration ──────────────────────

def direct_register(email: str, name: str, instance_id: str,
                    country: str | None = None, city: str | None = None) -> dict:
    """Register directly with the licensing server. Returns {api_key, tier, customer_id}."""
    payload = {
        "tier": TIER,
        "email": email,
        "name": name,
        "instance_id": instance_id,
        "version": VERSION,
    }
    if country:
        payload["country"] = country
    if city:
        payload["city"] = city

    return _post("/v1/register/direct", payload)


# ── Auto Registration (email-only, headless) ──

def auto_register(email: str, instance_id: str) -> dict:
    """Headless registration using only the operator email.

    The customer must already exist on the licensing server (one prior manual
    registration). Used by the EVOLUTION_OPERATOR_EMAIL env-var flow.

    Returns {api_key, customer_id, tier, status}.
    """
    return _post("/v1/register/auto", {
        "email": email,
        "tier": TIER,
        "instance_id": instance_id,
        "version": VERSION,
    })


# ── Activation (startup with existing api_key) ──

def activate(instance_id: str, api_key: str) -> bool:
    """Validate existing api_key with licensing server. Returns True if active."""
    try:
        result = _post("/v1/activate", {
            "instance_id": instance_id,
            "version": VERSION,
        }, api_key=api_key)
        return result.get("status") == "active"
    except Exception:
        return False


# ── Runtime Context (in-memory state) ────────

class RuntimeContext:
    """Thread-safe singleton holding license state."""

    def __init__(self):
        self.instance_id: str | None = None
        self.api_key: str | None = None
        self.tier: str = TIER

    @property
    def active(self) -> bool:
        return self.api_key is not None


# Global singleton
_context = RuntimeContext()


def get_context() -> RuntimeContext:
    return _context


# ── Setup (orchestrates direct registration) ─

def setup_perform(email: str, name: str, client_ip: str | None = None):
    """Full setup flow: geo lookup → direct register → save."""
    ctx = get_context()

    # 1. Load or create instance_id
    instance_id = get_runtime_config("instance_id")
    if not instance_id:
        instance_id = generate_instance_id()
        set_runtime_config("instance_id", instance_id)

    # 2. Geo lookup
    geo = geo_lookup(client_ip)

    # 3. Direct register
    try:
        result = direct_register(
            email=email,
            name=name,
            instance_id=instance_id,
            country=geo.get("country"),
            city=geo.get("city"),
        )
    except Exception as e:
        logger.warning(f"License registration failed (non-blocking): {e}")
        set_runtime_config("version", VERSION)
        set_runtime_config("tier", TIER)
        set_runtime_config("registered_at", datetime.now(timezone.utc).isoformat())
        return

    # 4. Save to DB
    api_key = result.get("api_key", "")
    if api_key:
        set_runtime_config("api_key", api_key)
    set_runtime_config("tier", result.get("tier", TIER))
    if result.get("customer_id"):
        set_runtime_config("customer_id", str(result["customer_id"]))
    set_runtime_config("version", VERSION)
    set_runtime_config("registered_at", datetime.now(timezone.utc).isoformat())

    # 5. Set context
    if api_key:
        ctx.api_key = api_key
        ctx.instance_id = instance_id
        logger.info(f"License registered: {instance_id[:8]}...")


# ── Initialize Runtime (startup) ─────────────

def initialize_runtime():
    """Called on app startup. Loads existing license if present."""
    ctx = get_context()

    instance_id = get_runtime_config("instance_id")
    if not instance_id:
        instance_id = generate_instance_id()
        set_runtime_config("instance_id", instance_id)

    ctx.instance_id = instance_id
    api_key = get_runtime_config("api_key")
    if api_key:
        ctx.api_key = api_key
        logger.info(f"License loaded: {instance_id[:8]}...")


# ── Auto-register for existing installs ──────

def try_auto_register_from_env(instance_id: str) -> bool:
    """Headless activation via EVOLUTION_OPERATOR_EMAIL env var.

    Requires the email to already exist on the licensing server (one prior
    manual registration). Returns True on success.

    Failures are silent — caller falls back to the existing admin-based or
    manual setup flow.
    """
    email = os.environ.get("EVOLUTION_OPERATOR_EMAIL", "").strip()
    if not email:
        return False

    try:
        result = auto_register(email=email, instance_id=instance_id)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        if status == 404:
            logger.info("Auto-activation skipped — email not registered yet (first time?).")
        else:
            logger.warning(f"Auto-activation rejected ({status}): falling back to manual flow.")
        return False
    except Exception as e:
        logger.warning(f"Auto-activation skipped — {e}")
        return False

    api_key = result.get("api_key")
    if not api_key:
        logger.warning("Auto-activation response missing api_key")
        return False

    set_runtime_config("api_key", api_key)
    set_runtime_config("tier", result.get("tier", TIER))
    if result.get("customer_id"):
        set_runtime_config("customer_id", str(result["customer_id"]))
    set_runtime_config("version", VERSION)
    set_runtime_config("registered_at", datetime.now(timezone.utc).isoformat())

    ctx = get_context()
    ctx.api_key = api_key
    ctx.instance_id = instance_id
    logger.info("License activated automatically via EVOLUTION_OPERATOR_EMAIL")
    return True


def auto_register_if_needed():
    """If no license yet, try EVOLUTION_OPERATOR_EMAIL first, then fall back to
    the admin-based retroactive flow."""
    try:
        instance_id = get_runtime_config("instance_id")
        api_key = get_runtime_config("api_key")

        if api_key:
            initialize_runtime()
            return

        if not instance_id:
            instance_id = generate_instance_id()
            set_runtime_config("instance_id", instance_id)

        # First-class path: silent activation from env var.
        if try_auto_register_from_env(instance_id):
            return

        # Fallback: if there's an admin user already, register retroactively.
        from models import User
        if User.query.count() == 0:
            return

        admin = User.query.filter_by(role="admin").first()
        if not admin or not admin.email:
            return

        setup_perform(
            email=admin.email or "",
            name=admin.display_name or admin.username,
        )
    except Exception as e:
        logger.debug(f"Auto-register skipped: {e}")


# ── License Status (for dashboard) ───────────

def get_license_status() -> dict:
    ctx = get_context()
    return {
        "registered": ctx.api_key is not None,
        "instance_id": ctx.instance_id or get_runtime_config("instance_id"),
        "tier": get_runtime_config("tier") or TIER,
        "version": VERSION,
        "registered_at": get_runtime_config("registered_at"),
        "product": PRODUCT,
    }
