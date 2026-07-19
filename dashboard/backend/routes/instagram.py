"""Instagram Graph API routes — webhooks, publish, comments, DMs."""

import hashlib
import hmac
import json
import logging
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import requests as http
from flask import Blueprint, jsonify, request, current_app

log = logging.getLogger(__name__)

bp = Blueprint("instagram_api", __name__)

WORKSPACE = Path(__file__).resolve().parent.parent.parent.parent
ENV_PATH = WORKSPACE / ".env"

# ── Helpers ──────────────────────────────────────────────────────────────────

GRAPH_BASE = "https://graph.facebook.com/v25.0"


def _read_env() -> dict:
    env = {}
    if not ENV_PATH.exists():
        return env
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"')
    return env


def _get_ig_accounts() -> list[dict]:
    """Return all configured Instagram accounts from env."""
    env = _read_env()
    accounts = []
    # Find all SOCIAL_INSTAGRAM_N_ indices
    import re
    pattern = re.compile(r"^SOCIAL_INSTAGRAM_(\d+)_")
    indices = set()
    for key in env:
        m = pattern.match(key)
        if m:
            indices.add(int(m.group(1)))
    for idx in sorted(indices):
        prefix = f"SOCIAL_INSTAGRAM_{idx}"
        token = env.get(f"{prefix}_ACCESS_TOKEN", "")
        account_id = env.get(f"{prefix}_ACCOUNT_ID", "")
        label = env.get(f"{prefix}_LABEL", f"Conta {idx}")
        if token:
            accounts.append({
                "index": idx,
                "label": label,
                "access_token": token,
                "account_id": account_id,
            })
    return accounts


def _graph_get(path: str, params: dict, token: str) -> dict:
    """Make a GET request to Graph API."""
    params["access_token"] = token
    url = f"{GRAPH_BASE}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


def _graph_post(path: str, data: dict, token: str) -> dict:
    """Make a POST request to Graph API."""
    data["access_token"] = token
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(f"{GRAPH_BASE}/{path}", data=body, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _graph_post_json(path: str, payload: dict, token: str) -> dict:
    """Make a POST request with JSON body to Graph API."""
    payload["access_token"] = token
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{GRAPH_BASE}/{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


# ── Webhook Verification (GET) ──────────────────────────────────────────────

@bp.route("/api/instagram/webhook", methods=["GET"])
def instagram_webhook_verify():
    """Verify Instagram/Meta webhook subscription.

    Meta sends a GET request with:
      hub.mode=subscribe
      hub.challenge=<random_string>
      hub.verify_token=<VERIFY_TOKEN>
    """
    mode = request.args.get("hub.mode", "")
    challenge = request.args.get("hub.challenge", "")
    verify_token = request.args.get("hub.verify_token", "")

    env = _read_env()
    expected_token = env.get("INSTAGRAM_WEBHOOK_VERIFY_TOKEN", "")

    if mode == "subscribe" and verify_token == expected_token:
        log.info("Instagram webhook verified successfully")
        return challenge, 200

    log.warning("Instagram webhook verification failed: mode=%s token_match=%s", mode, verify_token == expected_token)
    return jsonify({"error": "Verification failed"}), 403


# ── Webhook Receiver (POST) ─────────────────────────────────────────────────

@bp.route("/api/instagram/webhook", methods=["POST"])
def instagram_webhook_receive():
    """Receive Instagram webhook events (comments, messages).

    Meta sends POST with JSON body containing entry changes.
    We process asynchronously and return 200 immediately.
    """
    # Validate signature if app secret is set
    env = _read_env()
    app_secret = env.get("META_APP_SECRET", "")

    if app_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_signature(request.data, signature, app_secret):
            log.warning("Instagram webhook: invalid signature")
            return jsonify({"status": "ok"}), 200  # Don't reveal invalid sig

    try:
        data = request.get_json(silent=True) or {}
    except Exception:
        log.warning("Instagram webhook: failed to parse JSON body")
        return jsonify({"status": "ok"}), 200

    log.info("Instagram webhook received: object=%s entries=%d",
             data.get("object"), len(data.get("entry", [])))

    # Process asynchronously
    app = current_app._get_current_object()

    def _process():
        with app.app_context():
            _handle_webhook_entries(data, app)

    thread = threading.Thread(target=_process, daemon=True)
    thread.start()

    return jsonify({"status": "ok"}), 200


def _verify_signature(payload: bytes, signature: str, app_secret: str) -> bool:
    """Verify X-Hub-Signature-256 from Meta."""
    if not signature.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


def _handle_webhook_entries(data: dict, app):
    """Process webhook entries — comments and messages."""
    from models import db  # Import here to avoid circular deps

    for entry in data.get("entry", []):
        entry_id = entry.get("id", "")
        changes = entry.get("changes", [])

        for change in changes:
            field = change.get("field", "")
            value = change.get("value", {})

            if field == "comments":
                _handle_comment(value, entry_id, app)
            elif field == "messages":
                _handle_message(value, entry_id, app)
            elif field == "mentions":
                _handle_mention(value, entry_id, app)
            else:
                log.info("Instagram webhook: unhandled field=%s", field)


def _handle_comment(value: dict, page_id: str, app):
    """Handle a new comment on an Instagram media."""
    comment_id = value.get("id", "")
    text = value.get("text", "")
    media_id = value.get("media", {}).get("id", "")
    from_user = value.get("from", {}).get("username", "")

    log.info("Instagram comment: id=%s from=%s media=%s text=%s",
             comment_id, from_user, media_id, text[:80])

    # Store in activity log
    try:
        from models import ActivityLog
        log_entry = ActivityLog(
            source="instagram",
            event_type="comment",
            external_id=comment_id,
            from_user=from_user,
            content=text,
            media_id=media_id,
            page_id=page_id,
            created_at=datetime.now(timezone.utc),
        )
        from models import db
        db.session.add(log_entry)
        db.session.commit()
    except Exception as e:
        log.warning("Failed to log Instagram comment: %s", e)

    # Auto-reply logic: if comment contains certain keywords, reply
    # This can be extended with AI agent integration
    _maybe_auto_reply_comment(comment_id, text, from_user, app)


def _handle_message(value: dict, page_id: str, app):
    """Handle a new DM on Instagram."""
    message = value.get("message", {})
    message_id = message.get("id", "")
    text = message.get("text", "")
    from_user = value.get("from", {}).get("username", "")

    log.info("Instagram DM: id=%s from=%s text=%s", message_id, from_user, text[:80])

    # Store in activity log
    try:
        from models import ActivityLog
        log_entry = ActivityLog(
            source="instagram",
            event_type="dm",
            external_id=message_id,
            from_user=from_user,
            content=text,
            page_id=page_id,
            created_at=datetime.now(timezone.utc),
        )
        from models import db
        db.session.add(log_entry)
        db.session.commit()
    except Exception as e:
        log.warning("Failed to log Instagram DM: %s", e)

    # Auto-reply with link
    _maybe_auto_reply_dm(message_id, text, from_user, app)


def _handle_mention(value: dict, page_id: str, app):
    """Handle Instagram mention."""
    media_id = value.get("media_id", "")
    log.info("Instagram mention: media_id=%s", media_id)


def _maybe_auto_reply_comment(comment_id: str, text: str, from_user: str, app):
    """Auto-reply to a comment if it matches rules."""
    env = _read_env()
    auto_reply_enabled = env.get("INSTAGRAM_AUTO_REPLY_COMMENTS", "false").lower() == "true"
    if not auto_reply_enabled:
        return

    reply_text = env.get("INSTAGRAM_COMMENT_REPLY_TEXT", "")
    if not reply_text:
        return

    accounts = _get_ig_accounts()
    if not accounts:
        return

    token = accounts[0]["access_token"]

    try:
        result = _graph_post(
            f"{comment_id}/replies",
            {"message": reply_text},
            token,
        )
        log.info("Instagram comment reply sent: %s", result.get("id"))
    except Exception as e:
        log.warning("Failed to reply to comment %s: %s", comment_id, e)


def _maybe_auto_reply_dm(message_id: str, text: str, from_user: str, app):
    """Auto-reply to a DM with configured link."""
    env = _read_env()
    auto_reply_enabled = env.get("INSTAGRAM_AUTO_REPLY_DM", "false").lower() == "true"
    if not auto_reply_enabled:
        return

    reply_text = env.get("INSTAGRAM_DM_REPLY_TEXT", "")
    if not reply_text:
        return

    accounts = _get_ig_accounts()
    if not accounts:
        return

    token = accounts[0]["access_token"]
    ig_account_id = accounts[0]["account_id"]

    try:
        # Send DM reply via Instagram Messaging API
        payload = {
            "recipient": {"id": from_user},
            "message": {"text": reply_text},
        }
        result = _graph_post_json(
            f"{ig_account_id}/messages",
            payload,
            token,
        )
        log.info("Instagram DM reply sent: %s", result.get("id"))
    except Exception as e:
        log.warning("Failed to reply to DM from %s: %s", from_user, e)


# ── API: Account Info ───────────────────────────────────────────────────────

@bp.route("/api/instagram/accounts")
def instagram_accounts():
    """List configured Instagram accounts."""
    accounts = _get_ig_accounts()
    # Don't expose tokens
    return jsonify({
        "accounts": [
            {"index": a["index"], "label": a["label"], "account_id": a["account_id"]}
            for a in accounts
        ]
    })


@bp.route("/api/instagram/profile/<int:account_idx>")
def instagram_profile(account_idx):
    """Get Instagram profile info."""
    accounts = _get_ig_accounts()
    account = next((a for a in accounts if a["index"] == account_idx), None)
    if not account:
        return jsonify({"error": "Account not found"}), 404

    try:
        result = _graph_get(
            account["account_id"],
            {"fields": "id,username,name,biography,followers_count,follows_count,media_count,profile_picture_url,website"},
            account["access_token"],
        )
        return jsonify(result)
    except urllib.error.HTTPError as e:
        return jsonify({"error": e.read().decode()}), e.code


# ── API: Publish ────────────────────────────────────────────────────────────

@bp.route("/api/instagram/publish", methods=["POST"])
def instagram_publish():
    """Publish a post to Instagram.

    Expects JSON:
    {
        "account_idx": 1,
        "image_url": "https://...",   // or video_url
        "caption": "Post caption",
        "is_reel": false,
        "share_to_feed": true  // for reels
    }
    """
    data = request.get_json(silent=True) or {}
    account_idx = data.get("account_idx", 1)
    image_url = data.get("image_url", "")
    video_url = data.get("video_url", "")
    caption = data.get("caption", "")
    is_reel = data.get("is_reel", False)
    share_to_feed = data.get("share_to_feed", True)

    if not image_url and not video_url:
        return jsonify({"error": "image_url or video_url is required"}), 400

    accounts = _get_ig_accounts()
    account = next((a for a in accounts if a["index"] == account_idx), None)
    if not account:
        return jsonify({"error": "Account not found"}), 404

    token = account["access_token"]
    ig_id = account["account_id"]

    try:
        if is_reel or video_url:
            # Publish video/reel
            media_url = video_url or image_url
            # Step 1: Create video container
            container = _graph_post(
                f"{ig_id}/media",
                {
                    "media_type": "REELS" if is_reel else "VIDEO",
                    "video_url": media_url,
                    "caption": caption,
                    "share_to_feed": str(share_to_feed).lower(),
                },
                token,
            )
            creation_id = container.get("id")

            # Step 2: Wait and check status
            import time
            for _ in range(30):
                status = _graph_get(
                    creation_id,
                    {"fields": "status_code"},
                    token,
                )
                if status.get("status_code") == "FINISHED":
                    break
                if status.get("status_code") == "ERROR":
                    return jsonify({"error": "Video processing failed", "status": status}), 500
                time.sleep(2)

            # Step 3: Publish
            result = _graph_post(
                f"{ig_id}/media_publish",
                {"creation_id": creation_id},
                token,
            )
        else:
            # Publish image
            # Step 1: Create media container
            container = _graph_post(
                f"{ig_id}/media",
                {
                    "image_url": image_url,
                    "caption": caption,
                },
                token,
            )
            creation_id = container.get("id")

            # Step 2: Publish
            result = _graph_post(
                f"{ig_id}/media_publish",
                {"creation_id": creation_id},
                token,
            )

        log.info("Instagram post published: %s", result.get("id"))
        return jsonify({"id": result.get("id"), "status": "published"})

    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        log.error("Instagram publish failed: %s", error_body)
        return jsonify({"error": error_body}), e.code
    except Exception as e:
        log.error("Instagram publish failed: %s", e)
        return jsonify({"error": str(e)}), 500


# ── API: Comments ───────────────────────────────────────────────────────────

@bp.route("/api/instagram/comments/<int:account_idx>/<media_id>")
def instagram_comments(account_idx, media_id):
    """Get comments on a media."""
    accounts = _get_ig_accounts()
    account = next((a for a in accounts if a["index"] == account_idx), None)
    if not account:
        return jsonify({"error": "Account not found"}), 404

    try:
        result = _graph_get(
            f"{media_id}/comments",
            {"fields": "id,text,username,timestamp,like_count,replies{id,text,username,timestamp}", "limit": 50},
            account["access_token"],
        )
        return jsonify(result)
    except urllib.error.HTTPError as e:
        return jsonify({"error": e.read().decode()}), e.code


@bp.route("/api/instagram/comments/<int:account_idx>/reply", methods=["POST"])
def instagram_reply_comment(account_idx):
    """Reply to a comment.

    Expects JSON:
    {
        "comment_id": "...",
        "message": "Reply text"
    }
    """
    data = request.get_json(silent=True) or {}
    comment_id = data.get("comment_id", "")
    message = data.get("message", "")

    if not comment_id or not message:
        return jsonify({"error": "comment_id and message are required"}), 400

    accounts = _get_ig_accounts()
    account = next((a for a in accounts if a["index"] == account_idx), None)
    if not account:
        return jsonify({"error": "Account not found"}), 404

    try:
        result = _graph_post(
            f"{comment_id}/replies",
            {"message": message},
            account["access_token"],
        )
        return jsonify({"id": result.get("id"), "status": "replied"})
    except urllib.error.HTTPError as e:
        return jsonify({"error": e.read().decode()}), e.code


@bp.route("/api/instagram/comments/<int:account_idx>/hide", methods=["POST"])
def instagram_hide_comment(account_idx):
    """Hide a comment.

    Expects JSON: {"comment_id": "..."}
    """
    data = request.get_json(silent=True) or {}
    comment_id = data.get("comment_id", "")

    accounts = _get_ig_accounts()
    account = next((a for a in accounts if a["index"] == account_idx), None)
    if not account:
        return jsonify({"error": "Account not found"}), 404

    try:
        result = _graph_post(
            comment_id,
            {"hidden": "true"},
            account["access_token"],
        )
        return jsonify(result)
    except urllib.error.HTTPError as e:
        return jsonify({"error": e.read().decode()}), e.code


# ── API: DMs ────────────────────────────────────────────────────────────────

@bp.route("/api/instagram/dm/<int:account_idx>/send", methods=["POST"])
def instagram_send_dm(account_idx):
    """Send a DM to a user.

    Expects JSON:
    {
        "recipient_username": "username",
        "message": "Hello! Check out: https://..."
    }
    """
    data = request.get_json(silent=True) or {}
    recipient = data.get("recipient_username", "")
    message = data.get("message", "")

    if not recipient or not message:
        return jsonify({"error": "recipient_username and message are required"}), 400

    accounts = _get_ig_accounts()
    account = next((a for a in accounts if a["index"] == account_idx), None)
    if not account:
        return jsonify({"error": "Account not found"}), 404

    token = account["access_token"]
    ig_id = account["account_id"]

    try:
        # First, get the user's IG ID from username
        user_search = _graph_get(
            "ig_users",
            {"username": recipient},
            token,
        )
        user_id = user_search.get("id")

        if not user_id:
            # Try searching via the user's IG ID
            return jsonify({"error": f"User '{recipient}' not found"}), 404

        # Send the message
        payload = {
            "recipient": {"id": user_id},
            "message": {"text": message},
        }
        result = _graph_post_json(
            f"{ig_id}/messages",
            payload,
            token,
        )
        return jsonify({"id": result.get("id"), "status": "sent"})
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        # If user search fails, try sending directly with username
        if "ig_users" in str(e.url):
            try:
                payload = {
                    "recipient": {"username": recipient},
                    "message": {"text": message},
                }
                result = _graph_post_json(
                    f"{ig_id}/messages",
                    payload,
                    token,
                )
                return jsonify({"id": result.get("id"), "status": "sent"})
            except Exception as e2:
                return jsonify({"error": str(e2)}), 500
        return jsonify({"error": error_body}), e.code


@bp.route("/api/instagram/dm/<int:account_idx>/conversations")
def instagram_conversations(account_idx):
    """Get DM conversations."""
    accounts = _get_ig_accounts()
    account = next((a for a in accounts if a["index"] == account_idx), None)
    if not account:
        return jsonify({"error": "Account not found"}), 404

    try:
        result = _graph_get(
            f"{account['account_id']}/conversations",
            {"fields": "id,participants,messages{id,text,from,to,timestamp}", "limit": 25},
            account["access_token"],
        )
        return jsonify(result)
    except urllib.error.HTTPError as e:
        return jsonify({"error": e.read().decode()}), e.code


# ── API: Media ──────────────────────────────────────────────────────────────

@bp.route("/api/instagram/media/<int:account_idx>")
def instagram_media(account_idx):
    """Get recent media for an account."""
    accounts = _get_ig_accounts()
    account = next((a for a in accounts if a["index"] == account_idx), None)
    if not account:
        return jsonify({"error": "Account not found"}), 404

    limit = request.args.get("limit", 25, type=int)

    try:
        result = _graph_get(
            f"{account['account_id']}/media",
            {
                "fields": "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count",
                "limit": limit,
            },
            account["access_token"],
        )
        return jsonify(result)
    except urllib.error.HTTPError as e:
        return jsonify({"error": e.read().decode()}), e.code


# ── API: Insights ───────────────────────────────────────────────────────────

@bp.route("/api/instagram/insights/<int:account_idx>")
def instagram_insights(account_idx):
    """Get account insights."""
    accounts = _get_ig_accounts()
    account = next((a for a in accounts if a["index"] == account_idx), None)
    if not account:
        return jsonify({"error": "Account not found"}), 404

    try:
        result = _graph_get(
            f"{account['account_id']}/insights",
            {
                "metric": "impressions,reach,profile_views,follower_count",
                "period": "day",
            },
            account["access_token"],
        )
        return jsonify(result)
    except urllib.error.HTTPError as e:
        return jsonify({"error": e.read().decode()}), e.code
