#!/usr/bin/env python3
"""Instagram Graph API Client — multi-account support."""

import json
import os
import sys
import re
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path


def _load_dotenv():
    """Load .env from project root."""
    env_path = Path(__file__).resolve().parents[4] / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:
                os.environ[key] = value

_load_dotenv()

# Two Meta products, two API hosts:
#   - "Login do Facebook" tokens (EAA...) → graph.facebook.com, need a linked FB Page
#   - "Instagram API com Login do Instagram" tokens (IGAA...) → graph.instagram.com, direct
FB_BASE_URL = "https://graph.facebook.com/v25.0"
IG_BASE_URL = "https://graph.instagram.com/v23.0"


def _is_ig_login_token(token: str) -> bool:
    """Instagram-Login tokens start with IGAA (vs Facebook EAA tokens)."""
    return token.startswith("IG")


def _base_for_token(token: str) -> str:
    return IG_BASE_URL if _is_ig_login_token(token) else FB_BASE_URL


def _node_id(account: dict, token: str) -> str:
    """Path for the IG user node. IG-Login accepts 'me'; Facebook needs the IG id."""
    return account.get("account_id", "") or ("me" if _is_ig_login_token(token) else "")


# ── Account discovery ────────────────────────────────

def _get_accounts() -> list[dict]:
    """Find all SOCIAL_INSTAGRAM_N accounts from env."""
    accounts = []
    pattern = re.compile(r"^SOCIAL_INSTAGRAM_(\d+)_LABEL$")
    for key, value in sorted(os.environ.items()):
        m = pattern.match(key)
        if m:
            idx = m.group(1)
            accounts.append({
                "index": idx,
                "label": value,
                "access_token": os.environ.get(f"SOCIAL_INSTAGRAM_{idx}_ACCESS_TOKEN", ""),
                "account_id": os.environ.get(f"SOCIAL_INSTAGRAM_{idx}_ACCOUNT_ID", ""),
                "page_token": os.environ.get(f"SOCIAL_INSTAGRAM_{idx}_PAGE_TOKEN", ""),
            })
    return accounts


def _get_account(label_or_index: str = None) -> dict:
    """Get a specific account by label or index, or first available."""
    accounts = _get_accounts()
    if not accounts:
        return {}
    if not label_or_index:
        return accounts[0]
    for a in accounts:
        if a["index"] == label_or_index or a["label"].lower() == label_or_index.lower():
            return a
    return accounts[0]


def _token(account: dict) -> str:
    """Get best token — page token (permanent) preferred over user token (60d)."""
    return account.get("page_token") or account.get("access_token", "")


# ── API calls ────────────────────────────────────────

def _api_get(path: str, params: dict = None, base: str = FB_BASE_URL) -> dict:
    """Make GET request to Graph API."""
    params = params or {}
    query = urllib.parse.urlencode(params)
    url = f"{base}/{path}"
    if query:
        url += f"?{query}"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"error": f"HTTP {e.code}", "detail": body[:500]}
    except Exception as e:
        return {"error": str(e)}


# ── Commands ─────────────────────────────────────────

def profile(account: dict) -> dict:
    """Get Instagram profile — followers, media count, username."""
    token = _token(account)
    base = _base_for_token(token)
    ig_id = _node_id(account, token)
    if not ig_id or not token:
        return {"error": "No account_id or token configured"}

    data = _api_get(ig_id, {
        "fields": "username,name,biography,followers_count,follows_count,media_count,profile_picture_url,website",
        "access_token": token,
    }, base=base)

    if "error" in data and "detail" not in data:
        return data

    return {
        "account": account.get("label", ""),
        "account_id": ig_id,
        "username": data.get("username", ""),
        "name": data.get("name", ""),
        "biography": data.get("biography", "")[:200],
        "followers": data.get("followers_count", 0),
        "following": data.get("follows_count", 0),
        "media_count": data.get("media_count", 0),
        "website": data.get("website", ""),
        "profile_picture": data.get("profile_picture_url", ""),
    }


def recent_posts(account: dict, limit: int = 10) -> dict:
    """Get recent posts with engagement metrics."""
    token = _token(account)
    base = _base_for_token(token)
    ig_id = _node_id(account, token)
    if not ig_id or not token:
        return {"error": "No account_id or token configured"}

    data = _api_get(f"{ig_id}/media", {
        "fields": "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count",
        "limit": limit,
        "access_token": token,
    }, base=base)

    if "error" in data and "detail" not in data:
        return data

    # Get profile for follower count (for engagement rate)
    prof = profile(account)
    followers = prof.get("followers", 1)

    posts = []
    for m in data.get("data", []):
        likes = m.get("like_count", 0)
        comments = m.get("comments_count", 0)
        engagement = round((likes + comments) / followers * 100, 2) if followers > 0 else 0
        caption = m.get("caption", "")

        posts.append({
            "id": m.get("id", ""),
            "caption": caption[:100] + ("..." if len(caption) > 100 else ""),
            "media_type": m.get("media_type", ""),
            "permalink": m.get("permalink", ""),
            "timestamp": m.get("timestamp", ""),
            "likes": likes,
            "comments": comments,
            "engagement_rate": engagement,
        })

    return {
        "account": account.get("label", ""),
        "followers": followers,
        "posts": posts,
        "total": len(posts),
    }


def post_insights(account: dict, post_id: str) -> dict:
    """Get insights for a specific post."""
    token = _token(account)
    base = _base_for_token(token)
    metric = "reach,likes,comments,saved,shares" if _is_ig_login_token(token) else "impressions,reach,engagement"
    data = _api_get(f"{post_id}/insights", {
        "metric": metric,
        "access_token": token,
    }, base=base)

    if "error" in data:
        return data

    metrics = {}
    for item in data.get("data", []):
        metrics[item["name"]] = item["values"][0]["value"] if item.get("values") else 0

    return {"post_id": post_id, "metrics": metrics}


def account_insights(account: dict) -> dict:
    """Get account-level insights (last 30 days)."""
    token = _token(account)
    base = _base_for_token(token)
    ig_id = _node_id(account, token)
    if not ig_id or not token:
        return {"error": "No account_id or token configured"}

    params = {"metric": "impressions,reach,profile_views", "period": "day", "access_token": token}
    if _is_ig_login_token(token):
        # IG-Login deprecated 'impressions'; reach is the reliable time-series metric
        params["metric"] = "reach"
    data = _api_get(f"{ig_id}/insights", params, base=base)

    if "error" in data:
        return data

    metrics = {}
    for item in data.get("data", []):
        name = item.get("name", "")
        values = item.get("values", [])
        # Sum last 30 days
        total = sum(v.get("value", 0) for v in values)
        daily = [{"date": v.get("end_time", "")[:10], "value": v.get("value", 0)} for v in values]
        metrics[name] = {"total": total, "daily": daily[-7:]}  # last 7 days detail

    return {
        "account": account.get("label", ""),
        "account_id": ig_id,
        "metrics": metrics,
    }


def top_posts(account: dict, limit: int = 5) -> dict:
    """Get top posts by engagement."""
    result = recent_posts(account, limit=30)
    if "error" in result:
        return result

    sorted_posts = sorted(result["posts"], key=lambda p: p["likes"] + p["comments"], reverse=True)[:limit]
    return {
        "account": result.get("account", ""),
        "followers": result.get("followers", 0),
        "posts": sorted_posts,
        "total": len(sorted_posts),
    }


def all_accounts_summary() -> dict:
    """Get summary for all configured Instagram accounts."""
    accounts = _get_accounts()
    summaries = []
    for acc in accounts:
        prof = profile(acc)
        if "error" not in prof:
            summaries.append(prof)
    return {"accounts": summaries, "total": len(summaries)}


# ── CLI ──────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: instagram_client.py <command> [args]")
        print("\nCommands:")
        print("  accounts                              # List all configured accounts")
        print("  profile [account]                     # Profile (followers, bio, media count)")
        print("  recent_posts [account] [N]            # Last N posts with engagement")
        print("  top_posts [account] [N]               # Top N posts by engagement")
        print("  post_insights POST_ID [account]       # Insights for a specific post")
        print("  account_insights [account]            # Account insights (30d)")
        print("  summary                               # Summary of all accounts")
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    try:
        if cmd == "accounts":
            result = {"accounts": [{"index": a["index"], "label": a["label"],
                                     "account_id": a["account_id"],
                                     "has_token": bool(a["access_token"]),
                                     "has_page_token": bool(a["page_token"])} for a in _get_accounts()]}
        elif cmd == "profile":
            acc = _get_account(args[0] if args else None)
            result = profile(acc)
        elif cmd == "recent_posts":
            acc = _get_account(args[0] if args else None)
            n = int(args[1]) if len(args) > 1 else 10
            result = recent_posts(acc, n)
        elif cmd == "top_posts":
            acc = _get_account(args[0] if args else None)
            n = int(args[1]) if len(args) > 1 else 5
            result = top_posts(acc, n)
        elif cmd == "post_insights":
            post_id = args[0] if args else ""
            acc = _get_account(args[1] if len(args) > 1 else None)
            result = post_insights(acc, post_id)
        elif cmd == "account_insights":
            acc = _get_account(args[0] if args else None)
            result = account_insights(acc)
        elif cmd == "summary":
            result = all_accounts_summary()
        else:
            print(f"Unknown command: {cmd}")
            sys.exit(1)

        print(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": str(e)}, indent=2))
        sys.exit(1)
