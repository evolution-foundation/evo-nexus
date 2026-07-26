"""Proxy HTTP and WebSocket traffic to the local Hermes Agent UI.

Hermes Agent runs a web UI on port 9119. This proxy makes it accessible
through the dashboard on /hermes-ui/*, protected by @login_required.
By default Hermes binds to loopback and the dashboard login is the only
external gate. When Tailscale access is enabled with EvoNexus-managed Basic
Auth credentials, the proxy authenticates to Hermes server-side so the
embedded UI remains seamless without weakening direct-access protection.

The Hermes UI serves assets at /assets/* and talks to its backend over
both HTTP (/api/*) and WebSocket (/api/pty, /api/ws, /api/events). Behind
the /hermes-ui/ sub-path these absolute paths must be remapped. Two
mechanisms cooperate:

1. Static body rewrite (HTML/JS/CSS): literal `/api/`, `/assets/`, `/ws`
   occurrences are prefixed. Cheap, covers asset imports baked into the
   bundle, but blind to URLs the SPA builds dynamically at runtime
   (e.g. `new WebSocket(`${location.host}/api/pty`)`).
2. Runtime shim (injected into <head>): monkey-patches WebSocket, fetch,
   XMLHttpRequest, and the <script>.src / <link>.href setters so ANY
   same-origin /api//ws//assets//dashboard-plugins/ URL — however it was
   assembled — is rewritten to the /hermes-ui/ prefix. This is what makes
   the chat terminal's WebSocket reach the proxy instead of hitting the
   dashboard host at an unprefixed path (which 404s → close code 1006), and
   what makes the dashboard-plugins loader (kanban/achievements) fetch real
   plugin JS instead of the SPA's index.html.

HTTP follows terminal_proxy.py; the WebSocket bridge mirrors its
register_websocket_proxy() and is mounted on the same Sock instance.
"""

from __future__ import annotations

import base64
import logging
import os
import threading

import requests
from flask import Blueprint, Response, request, stream_with_context
from flask_login import current_user, login_required

log = logging.getLogger(__name__)

bp = Blueprint("hermes_proxy", __name__)

HERMES_UI_HOST = os.environ.get("HERMES_UI_HOST", "127.0.0.1")
HERMES_UI_PORT = int(os.environ.get("HERMES_UI_PORT", "9119"))
HERMES_UI_BASE = f"http://{HERMES_UI_HOST}:{HERMES_UI_PORT}"
HERMES_WS_BASE = f"ws://{HERMES_UI_HOST}:{HERMES_UI_PORT}"

# Sub-path the Hermes UI is mounted under in the dashboard.
PREFIX = "/hermes-ui"

# Upstream WebSocket paths exposed by the Hermes UI that the chat terminal
# connects to. Each is bridged 1:1 under the /hermes-ui/ prefix.
_WS_PATHS = ("api/pty", "api/ws", "api/events", "ws")

_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)

# Dashboard credentials authenticate the client to EvoNexus. They must never
# cross the trust boundary into the proxied Hermes process.
_DASHBOARD_CREDENTIAL_HEADERS = frozenset(
    {"authorization", "cookie", "x-csrf-token", "x-csrftoken"}
)

# Headers that block iframe embedding — strip from upstream responses
_IFRAME_BLOCK = frozenset(
    {
        "x-frame-options",
        "content-security-policy",
        "content-security-policy-report-only",
        "set-cookie",
    }
)

# Runtime shim injected into the Hermes HTML <head>. Runs before the SPA
# bundle so every WebSocket/fetch/XHR — plus the src/href of dynamically
# injected <script>/<link> nodes (the dashboard-plugins loader) — is
# transparently remapped to the /hermes-ui/ prefix on the current origin,
# covering URLs the bundle builds dynamically that a static string rewrite
# cannot catch.
_SHIM = """<script>(function(){
  var P="/hermes-ui";
  function rw(u){
    try{
      if(typeof u!=="string") return u;
      if(u.charAt(0)==="/" && u.indexOf(P+"/")!==0 && /^\\/(api|ws|assets|socket\\.io|dashboard-plugins)\\b/.test(u)) return P+u;
      var m=u.match(/^(wss?|https?):\\/\\/[^\\/]+(\\/.*)$/i);
      if(m && m[2].indexOf(P+"/")!==0 && /^\\/(api|ws|assets|socket\\.io|dashboard-plugins)\\b/.test(m[2]))
        return m[1]+"://"+location.host+P+m[2];
      return u;
    }catch(e){return u;}
  }
  var OW=window.WebSocket;
  if(OW){
    var NW=function(u,pr){return pr===undefined?new OW(rw(u)):new OW(rw(u),pr);};
    NW.prototype=OW.prototype; NW.CONNECTING=OW.CONNECTING; NW.OPEN=OW.OPEN;
    NW.CLOSING=OW.CLOSING; NW.CLOSED=OW.CLOSED;
    window.WebSocket=NW;
  }
  var OF=window.fetch;
  if(OF) window.fetch=function(i,init){
    if(typeof i==="string") return OF.call(this,rw(i),init);
    if(i && i.url) try{return OF.call(this,new Request(rw(i.url),i),init);}catch(e){}
    return OF.call(this,i,init);
  };
  var OX=window.XMLHttpRequest && window.XMLHttpRequest.prototype.open;
  if(OX) window.XMLHttpRequest.prototype.open=function(m,u){
    arguments[1]=rw(u); return OX.apply(this,arguments);
  };
  // The dashboard-plugins loader injects <script src> / <link href> nodes to
  // load each plugin's bundle. Those bypass fetch/XHR/WebSocket, so patch the
  // property setters to remap their URLs too — otherwise the browser fetches
  // /dashboard-plugins/* from the dashboard root and gets the SPA's index.html
  // back (Unexpected token '<' → the plugin never calls register()).
  ["HTMLScriptElement","HTMLLinkElement"].forEach(function(n){
    var pr=window[n]&&window[n].prototype, k=(n==="HTMLLinkElement")?"href":"src";
    if(!pr) return;
    var d=Object.getOwnPropertyDescriptor(pr,k);
    if(!d||!d.set) return;
    Object.defineProperty(pr,k,{configurable:true,enumerable:d.enumerable,
      get:function(){return d.get.call(this);},
      set:function(v){d.set.call(this,(typeof v==="string")?rw(v):v);}});
  });
})();</script>"""


def _forward_headers(src: dict[str, str]) -> dict[str, str]:
    return {
        k: v
        for k, v in src.items()
        if k.lower() not in _HOP_BY_HOP
        and k.lower() not in _DASHBOARD_CREDENTIAL_HEADERS
    }


# ---------------------------------------------------------------------------
# Hermes gated-auth session manager.
#
# When Hermes binds 0.0.0.0 with basic_auth enabled, it runs in "gated" mode:
# cookie-based session auth instead of the loopback session-token scheme.
# The proxy must POST credentials to /auth/password-login, cache the resulting
# session cookies, and replay them on every upstream request. When the session
# expires (upstream returns 302/401), the next request triggers a re-login.
# ---------------------------------------------------------------------------
_hermes_session_lock = threading.Lock()
_hermes_session_cookies: dict[str, str] = {}


def _hermes_auth_enabled() -> bool:
    return bool(os.environ.get("EVONEXUS_HERMES_PASSWORD", ""))


def _hermes_login() -> dict[str, str]:
    """POST credentials to Hermes /auth/password-login, return cookie jar."""
    user = os.environ.get("EVONEXUS_HERMES_USERNAME", "hermes-admin")
    pw = os.environ.get("EVONEXUS_HERMES_PASSWORD", "")
    try:
        resp = requests.post(
            f"{HERMES_UI_BASE}/auth/password-login",
            json={"provider": "basic", "username": user, "password": pw},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.cookies.get_dict()
        log.warning(
            "hermes_proxy: login failed (%s): %s",
            resp.status_code,
            resp.text[:200],
        )
    except Exception as exc:
        log.warning("hermes_proxy: login error: %s", exc)
    return {}


def _hermes_get_cookies() -> dict[str, str]:
    """Return cached session cookies, re-logging in if empty."""
    global _hermes_session_cookies
    with _hermes_session_lock:
        if not _hermes_session_cookies:
            _hermes_session_cookies = _hermes_login()
        return _hermes_session_cookies


def _hermes_invalidate_session() -> None:
    global _hermes_session_cookies
    with _hermes_session_lock:
        _hermes_session_cookies = {}


@bp.route(
    "/hermes-ui/<path:subpath>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
@bp.route("/hermes-ui", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@bp.route("/hermes-ui/", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@login_required
def proxy_http(subpath: str = ""):
    target = f"{HERMES_UI_BASE}/{subpath}"
    if request.query_string:
        target = f"{target}?{request.query_string.decode('latin-1')}"

    hermes_pass = os.environ.get("EVONEXUS_HERMES_PASSWORD", "")
    hermes_user = os.environ.get("EVONEXUS_HERMES_USERNAME", "hermes-admin")
    headers = _forward_headers(dict(request.headers))
    if hermes_pass:
        # Hermes gated auth uses cookie-based sessions, not Basic Auth.
        # Inject the cached session cookies instead of an Authorization header.
        cookies = _hermes_get_cookies()
        if cookies:
            # Strip incoming Cookie header and use our managed session cookies
            headers.pop("cookie", None)
            headers.pop("Cookie", None)
        else:
            # Fallback to Basic Auth if login failed (loopback / no-gate mode)
            creds = base64.b64encode(f"{hermes_user}:{hermes_pass}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"
            cookies = None
    else:
        cookies = None

    try:
        upstream = requests.request(
            method=request.method,
            url=target,
            headers=headers,
            cookies=cookies,
            data=request.get_data(),
            allow_redirects=False,
            stream=True,
            timeout=30,
        )
        # Session expired — re-login and retry once.
        if hermes_pass and upstream.status_code in (302, 401) and subpath != "auth/password-login":
            _hermes_invalidate_session()
            new_cookies = _hermes_get_cookies()
            if new_cookies:
                upstream = requests.request(
                    method=request.method,
                    url=target,
                    headers=headers,
                    cookies=new_cookies,
                    data=request.get_data(),
                    allow_redirects=False,
                    stream=True,
                    timeout=30,
                )
    except requests.exceptions.ConnectionError:
        return (
            "Hermes UI is not running. Ensure Hermes Agent is installed "
            "and the UI is started (hermes ui --port 9119).",
            503,
        )
    except requests.exceptions.Timeout:
        return "Hermes UI timed out.", 504

    content_type = upstream.headers.get("content-type", "")
    is_html = "text/html" in content_type
    is_js = "javascript" in content_type
    is_css = "text/css" in content_type

    if is_html or is_js or is_css:
        # Buffer text responses to remap absolute paths to the sub-path.
        body = upstream.content.decode("utf-8", errors="replace")
        if is_html:
            body = body.replace('src="/', 'src="/hermes-ui/')
            body = body.replace("src='/", "src='/hermes-ui/")
            body = body.replace('href="/', 'href="/hermes-ui/')
            body = body.replace("href='/", "href='/hermes-ui/")
            body = body.replace('action="/', 'action="/hermes-ui/')
            body = body.replace("action='/", "action='/hermes-ui/")
            # Inject the runtime shim as early as possible (before the bundle).
            if "<head>" in body:
                body = body.replace("<head>", "<head>" + _SHIM, 1)
            elif "<head " in body:
                idx = body.find("<head ")
                end = body.find(">", idx)
                if end != -1:
                    body = body[: end + 1] + _SHIM + body[end + 1 :]
            else:
                body = _SHIM + body
        if is_css:
            # Rewrite url(/assets/...) references so @font-face / background
            # assets resolve under the sub-path instead of the dashboard root.
            body = body.replace("url(/", "url(/hermes-ui/")
            body = body.replace('url("/', 'url("/hermes-ui/')
            body = body.replace("url('/", "url('/hermes-ui/")
        if is_js:
            # Static URL literals baked into the bundle (asset imports, fixed
            # endpoints). Dynamic URLs are handled by the injected shim.
            body = body.replace('"/api/', '"/hermes-ui/api/')
            body = body.replace("'/api/", "'/hermes-ui/api/")
            body = body.replace('`/api/', '`/hermes-ui/api/')
            body = body.replace('"/assets/', '"/hermes-ui/assets/')
            body = body.replace("'/assets/", "'/hermes-ui/assets/")
            body = body.replace('`/assets/', '`/hermes-ui/assets/')
            body = body.replace('"/ws', '"/hermes-ui/ws')
            body = body.replace("'/ws", "'/hermes-ui/ws")
            body = body.replace('`/ws', '`/hermes-ui/ws')
        response = Response(body, status=upstream.status_code, content_type=content_type)
    else:
        response = Response(
            stream_with_context(upstream.iter_content(chunk_size=8192)),
            status=upstream.status_code,
        )

    for key, value in upstream.headers.items():
        kl = key.lower()
        if kl not in _HOP_BY_HOP and kl not in _IFRAME_BLOCK:
            if (is_html or is_js or is_css) and kl == "content-length":
                continue
            response.headers[key] = value
    return response


# ---------------------------------------------------------------------------
# WebSocket proxy — Hermes chat terminal stream (pty / ws / events)
# ---------------------------------------------------------------------------
# Registered at app-creation time via `register_websocket_proxy(sock)` so we
# reuse the shared `flask_sock.Sock` instance created in app.py. Mirrors
# terminal_proxy.register_websocket_proxy(); without this the chat terminal's
# WebSocket has nothing to upgrade against and closes with code 1006.

def register_websocket_proxy(sock) -> None:
    """Register the Hermes WebSocket bridges on the given Sock instance."""
    try:
        from websocket import create_connection  # type: ignore
    except ImportError:
        log.warning(
            "hermes_proxy.register_websocket_proxy: websocket-client not "
            "installed; Hermes chat WebSocket disabled. Add `websocket-client` "
            "to dependencies."
        )
        return

    def _bridge(client_ws, upstream_path: str) -> None:
        """Bidirectional bridge: browser <-> Flask <-> Hermes UI :9119.

        Auth mirrors terminal_proxy: the global auth_middleware does not gate
        these paths, so without this explicit check anyone reaching the
        dashboard could open the Hermes chat. flask-login's session cookie is
        read from the WS upgrade request, same as @login_required on HTTP.
        """
        if not current_user.is_authenticated:
            try:
                client_ws.close(reason="auth required")
            except Exception:
                pass
            return

        target = f"{HERMES_WS_BASE}/{upstream_path}"
        qs_parts = []
        if request.query_string:
            qs_parts.append(request.query_string.decode("latin-1"))
        # ?internal= bypasses auth for child processes (Hermes v0.18.2+)
        qs_parts.append("internal=")
        target = f"{target}?{'&'.join(qs_parts)}"

        # When Hermes is in gated auth mode, pass session cookies.
        ws_headers = {}
        if _hermes_auth_enabled():
            cookies = _hermes_get_cookies()
            if cookies:
                cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
                ws_headers["Cookie"] = cookie_str

        try:
            upstream = create_connection(target, timeout=10, header=ws_headers)
        except Exception as exc:
            log.warning("hermes_proxy: upstream WS connect failed: %s", exc)
            try:
                client_ws.close(reason=f"upstream unreachable: {exc}")
            except Exception:
                pass
            return

        stop = threading.Event()

        def _pump_upstream_to_client():
            try:
                while not stop.is_set():
                    msg = upstream.recv()
                    if msg is None or msg == b"":
                        break
                    client_ws.send(msg)
            except Exception:
                pass
            finally:
                stop.set()
                try:
                    client_ws.close()
                except Exception:
                    pass

        t = threading.Thread(target=_pump_upstream_to_client, daemon=True)
        t.start()

        try:
            while not stop.is_set():
                msg = client_ws.receive(timeout=30)
                if msg is None:
                    # Idle timeout, NOT a close: simple_websocket.receive()
                    # returns None when no frame arrives within `timeout` while
                    # the connection is still alive (a real close raises
                    # ConnectionClosed, handled by the except below). Loop to
                    # re-check stop.is_set() and keep waiting. Breaking here
                    # tore down healthy sockets after a ~30s pause with no
                    # client frame, which the browser surfaced as
                    # `[session ended (code 1006)]` a few minutes into a chat.
                    continue
                upstream.send(msg)
        except Exception:
            pass
        finally:
            stop.set()
            try:
                upstream.close()
            except Exception:
                pass

    # Bind one route per upstream WS path. A closure captures the path so all
    # routes share the single _bridge implementation.
    def _make(path: str):
        def _handler(client_ws):
            _bridge(client_ws, path)
        return _handler

    for _path in _WS_PATHS:
        sock.route(f"{PREFIX}/{_path}", endpoint=f"hermes_ws_{_path.replace('/', '_')}")(
            _make(_path)
        )
