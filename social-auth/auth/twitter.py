"""X/Twitter OAuth 2.0 PKCE flow — multi-account."""

from flask import Blueprint, request, redirect, session
import json
import urllib.request
import urllib.error
import urllib.parse
import secrets
import hashlib
import base64
from datetime import datetime, timezone

bp = Blueprint("twitter", __name__)


def _redirect_uri():
    from env_manager import read_env
    env = read_env()
    ngrok = env.get("NGROK_URL", "")
    if ngrok:
        return ngrok.rstrip("/") + "/callback/twitter"
    return request.host_url.rstrip("/") + "/callback/twitter"


@bp.route("/connect/twitter")
def connect():
    from env_manager import read_env
    env = read_env()
    client_id = env.get("TWITTER_CLIENT_ID", "")

    if not client_id:
        return """
        <html><body style="background:#0C111D;color:#F9FAFB;font-family:Inter,sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;">
        <div style="background:#182230;border:1px solid #344054;border-radius:12px;padding:40px;max-width:500px;width:100%;">
            <h2 style="color:#00FFA7;">X / Twitter — Adicionar Conta</h2>
            <p style="color:#D0D5DD;margin-bottom:20px;">Cole um Bearer Token para leitura ou configure TWITTER_CLIENT_ID no .env para OAuth com escrita:</p>
            <form action="/save/twitter" method="POST">
                <input name="label" type="text" placeholder="Account label (e.g. Main)" style="width:100%;padding:12px;background:#0C111D;border:1px solid #344054;border-radius:8px;color:#F9FAFB;font-size:14px;margin-bottom:12px;">
                <input name="bearer_token" type="text" placeholder="Bearer Token" style="width:100%;padding:12px;background:#0C111D;border:1px solid #344054;border-radius:8px;color:#F9FAFB;font-size:14px;margin-bottom:20px;">
                <button type="submit" style="width:100%;padding:12px;background:#00FFA7;color:#0C111D;border:none;border-radius:8px;font-weight:700;cursor:pointer;">Salvar</button>
            </form>
            <a href="/" style="color:#667085;font-size:12px;display:block;text-align:center;margin-top:16px;">Voltar</a>
        </div></body></html>
        """

    # PKCE OAuth flow
    code_verifier = secrets.token_urlsafe(96)
    code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)

    session["twitter_code_verifier"] = code_verifier
    session["oauth_state_twitter"] = state

    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": _redirect_uri(),
        "scope": "tweet.read tweet.write users.read follows.read offline.access",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    return redirect(f"https://twitter.com/i/oauth2/authorize?{params}")


@bp.route("/save/twitter", methods=["POST"])
def save():
    from env_manager import save_account, next_index
    label = request.form.get("label", "").strip() or "X/Twitter"
    bearer = request.form.get("bearer_token", "").strip()
    if bearer:
        idx = next_index("twitter")
        save_account("twitter", idx, label, {"BEARER_TOKEN": bearer})
    return redirect("/?saved=X/Twitter")


@bp.route("/callback/twitter")
def callback():
    from env_manager import read_env, save_account, next_index
    env = read_env()

    state = request.args.get("state", "")
    if state != session.get("oauth_state_twitter"):
        return "Invalid state — CSRF protection", 403

    code = request.args.get("code", "")
    if not code:
        return f"Error: {request.args.get('error', 'no code')}", 400

    token_payload = {
        "code": code,
        "grant_type": "authorization_code",
        "client_id": env.get("TWITTER_CLIENT_ID", ""),
        "redirect_uri": _redirect_uri(),
        "code_verifier": session.get("twitter_code_verifier", ""),
    }
    client_secret = env.get("TWITTER_CLIENT_SECRET", "")
    if client_secret:
        token_payload["client_secret"] = client_secret
    data = urllib.parse.urlencode(token_payload).encode()

    req = urllib.request.Request("https://api.x.com/2/oauth2/token", data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req) as resp:
            token_data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return f"Token exchange failed: {e.read().decode()}", 500

    idx = next_index("twitter")
    fields = {
        "ACCESS_TOKEN": token_data.get("access_token", ""),
        "TOKEN_CREATED_AT": datetime.now(timezone.utc).isoformat(),
    }
    if token_data.get("refresh_token"):
        fields["REFRESH_TOKEN"] = token_data["refresh_token"]

    save_account("twitter", idx, "X/Twitter", fields)
    return redirect("/?saved=X/Twitter")
