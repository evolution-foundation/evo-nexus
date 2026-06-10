#!/usr/bin/env python3
"""YouTube Data API v3 Client — multi-account support."""

import json
import os
import sys
import urllib.request
import urllib.error
import urllib.parse
import re
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

BASE_URL = "https://www.googleapis.com/youtube/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"


# ── OAuth refresh ────────────────────────────────────

def _env_path() -> Path:
    return Path(__file__).resolve().parents[4] / ".env"


def _set_env_var(key: str, value: str):
    """Upsert a key in .env (mirror of social-auth/env_manager.set_env)."""
    path = _env_path()
    lines = []
    found = False
    if path.exists():
        with open(path) as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    k = stripped.split("=", 1)[0].strip()
                    if k == key:
                        lines.append(f"{key}={value}\n")
                        found = True
                        continue
                lines.append(line if line.endswith("\n") else line + "\n")
    if not found:
        lines.append(f"{key}={value}\n")
    with open(path, "w") as f:
        f.writelines(lines)


def _refresh_access_token(account: dict) -> str:
    """Exchange refresh_token for a new access_token. Persists to .env and updates account in place. Returns new token or empty string."""
    refresh_token = account.get("refresh_token", "")
    client_id = os.environ.get("YOUTUBE_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("YOUTUBE_OAUTH_CLIENT_SECRET", "")
    if not (refresh_token and client_id and client_secret):
        return ""

    data = urllib.parse.urlencode({
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
    }).encode()

    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            tok = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"[youtube] refresh failed: {e.read().decode('utf-8', 'replace')[:300]}\n")
        return ""
    except Exception as e:
        sys.stderr.write(f"[youtube] refresh error: {e}\n")
        return ""

    new_token = tok.get("access_token", "")
    if not new_token:
        return ""

    idx = account.get("index", "")
    env_key = f"SOCIAL_YOUTUBE_{idx}_ACCESS_TOKEN" if idx else "YOUTUBE_ACCESS_TOKEN"
    _set_env_var(env_key, new_token)
    os.environ[env_key] = new_token
    account["access_token"] = new_token
    return new_token


# ── Account discovery ────────────────────────────────

def _get_accounts() -> list[dict]:
    """Find all SOCIAL_YOUTUBE_N accounts from env."""
    accounts = []
    pattern = re.compile(r"^SOCIAL_YOUTUBE_(\d+)_LABEL$")
    for key, value in sorted(os.environ.items()):
        m = pattern.match(key)
        if m:
            idx = m.group(1)
            accounts.append({
                "index": idx,
                "label": value,
                "api_key": os.environ.get(f"SOCIAL_YOUTUBE_{idx}_API_KEY", ""),
                "access_token": os.environ.get(f"SOCIAL_YOUTUBE_{idx}_ACCESS_TOKEN", ""),
                "refresh_token": os.environ.get(f"SOCIAL_YOUTUBE_{idx}_REFRESH_TOKEN", ""),
                "channel_id": os.environ.get(f"SOCIAL_YOUTUBE_{idx}_CHANNEL_ID", ""),
            })
    # Fallback: single-account legacy keys
    if not accounts:
        api_key = os.environ.get("YOUTUBE_API_KEY", "")
        access_token = os.environ.get("YOUTUBE_ACCESS_TOKEN", "")
        channel_id = os.environ.get("YOUTUBE_CHANNEL_ID", "")
        if api_key or access_token:
            accounts.append({
                "index": "0",
                "label": "default",
                "api_key": api_key,
                "access_token": access_token,
                "refresh_token": os.environ.get("YOUTUBE_REFRESH_TOKEN", ""),
                "channel_id": channel_id,
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


# ── API calls ────────────────────────────────────────

def _api_get(path: str, params: dict, account: dict, _retried: bool = False) -> dict:
    """Make authenticated GET request. Auto-refreshes expired OAuth tokens once on 401."""
    params = dict(params)  # don't mutate caller's dict
    if account.get("access_token"):
        params["access_token"] = account["access_token"]
    elif account.get("api_key"):
        params["key"] = account["api_key"]
    else:
        return {"error": "No API key or access token configured"}

    query = urllib.parse.urlencode(params)
    url = f"{BASE_URL}/{path}?{query}"

    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 401 Unauthorized — try to refresh access_token once
        if e.code == 401 and not _retried and account.get("refresh_token"):
            new_token = _refresh_access_token(account)
            if new_token:
                return _api_get(path, {k: v for k, v in params.items() if k != "access_token"}, account, _retried=True)
        body = e.read().decode("utf-8", errors="replace")
        return {"error": f"HTTP {e.code}", "detail": body[:500]}
    except Exception as e:
        return {"error": str(e)}


# ── Commands ─────────────────────────────────────────

def channel_stats(account: dict) -> dict:
    """Get channel statistics."""
    channel_id = account.get("channel_id", "")
    params = {"part": "snippet,statistics,contentDetails"}

    if channel_id:
        params["id"] = channel_id
    elif account.get("access_token"):
        params["mine"] = "true"
    else:
        return {"error": "No channel_id and no access_token for mine=true"}

    data = _api_get("channels", params, account)
    if "error" in data:
        return data

    items = data.get("items", [])
    if not items:
        return {"error": "Channel not found"}

    ch = items[0]
    stats = ch.get("statistics", {})
    snippet = ch.get("snippet", {})
    content = ch.get("contentDetails", {})

    return {
        "account": account.get("label", ""),
        "channel_id": ch["id"],
        "title": snippet.get("title", ""),
        "description": snippet.get("description", "")[:200],
        "subscribers": int(stats.get("subscriberCount", 0)),
        "total_views": int(stats.get("viewCount", 0)),
        "video_count": int(stats.get("videoCount", 0)),
        "uploads_playlist": content.get("relatedPlaylists", {}).get("uploads", ""),
        "custom_url": snippet.get("customUrl", ""),
    }


def recent_videos(account: dict, max_results: int = 10) -> dict:
    """Get recent videos via playlistItems (1 unit vs 100 for search)."""
    # First get uploads playlist ID
    ch = channel_stats(account)
    if "error" in ch:
        return ch

    playlist_id = ch.get("uploads_playlist", "")
    if not playlist_id:
        return {"error": "No uploads playlist found"}

    # Get playlist items
    data = _api_get("playlistItems", {
        "part": "snippet",
        "playlistId": playlist_id,
        "maxResults": max_results,
    }, account)

    if "error" in data:
        return data

    video_ids = []
    for item in data.get("items", []):
        vid = item.get("snippet", {}).get("resourceId", {}).get("videoId", "")
        if vid:
            video_ids.append(vid)

    if not video_ids:
        return {"account": ch["title"], "videos": [], "total": 0}

    # Get video statistics
    stats_data = _api_get("videos", {
        "part": "statistics,snippet,contentDetails",
        "id": ",".join(video_ids),
    }, account)

    if "error" in stats_data:
        return stats_data

    videos = []
    for v in stats_data.get("items", []):
        s = v.get("statistics", {})
        sn = v.get("snippet", {})
        cd = v.get("contentDetails", {})
        views = int(s.get("viewCount", 0))
        likes = int(s.get("likeCount", 0))
        comments = int(s.get("commentCount", 0))
        engagement = round((likes + comments) / views * 100, 2) if views > 0 else 0

        videos.append({
            "id": v["id"],
            "title": sn.get("title", ""),
            "published": sn.get("publishedAt", ""),
            "duration": cd.get("duration", ""),
            "views": views,
            "likes": likes,
            "comments": comments,
            "engagement_rate": engagement,
            "url": f"https://youtube.com/watch?v={v['id']}",
        })

    return {
        "account": ch["title"],
        "channel_id": ch["channel_id"],
        "subscribers": ch["subscribers"],
        "videos": videos,
        "total": len(videos),
    }


def video_stats(account: dict, video_ids: list) -> dict:
    """Get stats for specific videos."""
    data = _api_get("videos", {
        "part": "statistics,snippet,contentDetails",
        "id": ",".join(video_ids),
    }, account)

    if "error" in data:
        return data

    videos = []
    for v in data.get("items", []):
        s = v.get("statistics", {})
        sn = v.get("snippet", {})
        views = int(s.get("viewCount", 0))
        likes = int(s.get("likeCount", 0))
        comments = int(s.get("commentCount", 0))

        videos.append({
            "id": v["id"],
            "title": sn.get("title", ""),
            "views": views,
            "likes": likes,
            "comments": comments,
            "engagement_rate": round((likes + comments) / views * 100, 2) if views > 0 else 0,
        })

    return {"videos": videos, "total": len(videos)}


def top_videos(account: dict, max_results: int = 10) -> dict:
    """Get top videos by views (from recent 30)."""
    result = recent_videos(account, max_results=30)
    if "error" in result:
        return result

    sorted_videos = sorted(result["videos"], key=lambda v: v["views"], reverse=True)[:max_results]
    return {
        "account": result.get("account", ""),
        "videos": sorted_videos,
        "total": len(sorted_videos),
    }


def comments(account: dict, video_id: str, max_results: int = 20) -> dict:
    """Get recent comments on a video."""
    data = _api_get("commentThreads", {
        "part": "snippet",
        "videoId": video_id,
        "maxResults": max_results,
        "order": "time",
    }, account)

    if "error" in data:
        return data

    result = []
    for item in data.get("items", []):
        c = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
        result.append({
            "author": c.get("authorDisplayName", ""),
            "text": c.get("textDisplay", "")[:300],
            "likes": c.get("likeCount", 0),
            "published": c.get("publishedAt", ""),
        })

    return {"video_id": video_id, "comments": result, "total": len(result)}


def all_accounts_summary() -> dict:
    """Get summary for all configured YouTube accounts."""
    accounts = _get_accounts()
    summaries = []
    for acc in accounts:
        ch = channel_stats(acc)
        if "error" not in ch:
            summaries.append(ch)
    return {"accounts": summaries, "total": len(summaries)}


# ── CLI ──────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: youtube_client.py <command> [args]")
        print("\nCommands:")
        print("  accounts                              # List all configured accounts")
        print("  channel_stats [account]               # Channel stats (subscribers, views, videos)")
        print("  recent_videos [account] [N]           # Last N videos with stats")
        print("  top_videos [account] [N]              # Top N videos by views")
        print("  video_stats VIDEO_ID [VIDEO_ID...]    # Stats for specific videos")
        print("  comments VIDEO_ID [N]                 # Recent comments on a video")
        print("  summary                               # Summary of all accounts")
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    try:
        if cmd == "accounts":
            result = {"accounts": [{"index": a["index"], "label": a["label"],
                                     "channel_id": a["channel_id"],
                                     "has_token": bool(a["access_token"]),
                                     "has_key": bool(a["api_key"])} for a in _get_accounts()]}
        elif cmd == "channel_stats":
            acc = _get_account(args[0] if args else None)
            result = channel_stats(acc)
        elif cmd == "recent_videos":
            acc = _get_account(args[0] if args else None)
            n = int(args[1]) if len(args) > 1 else 10
            result = recent_videos(acc, n)
        elif cmd == "top_videos":
            acc = _get_account(args[0] if args else None)
            n = int(args[1]) if len(args) > 1 else 10
            result = top_videos(acc, n)
        elif cmd == "video_stats":
            acc = _get_account()
            result = video_stats(acc, args)
        elif cmd == "comments":
            acc = _get_account()
            vid_id = args[0] if args else ""
            n = int(args[1]) if len(args) > 1 else 20
            result = comments(acc, vid_id, n)
        elif cmd == "summary":
            result = all_accounts_summary()
        elif cmd == "smoke":
            import time as _time
            _t0 = _time.time()
            _steps = []
            _overall = "PASS"

            # step 1: auth — load accounts
            _ts = _time.time()
            try:
                _accounts = _get_accounts()
                _steps.append({"step": "auth", "status": "PASS", "duration_ms": round((_time.time() - _ts) * 1000)})
            except Exception as _e:
                _steps.append({"step": "auth", "status": "FAIL", "error": str(_e)[:300], "duration_ms": round((_time.time() - _ts) * 1000)})
                _overall = "FAIL"
                _accounts = []

            # step 2: channel_stats read (cheap: 1 quota unit)
            _ts = _time.time()
            if not _accounts:
                _steps.append({"step": "channel_stats", "status": "SKIP", "duration_ms": 0})
            else:
                try:
                    _r = channel_stats(_accounts[0])
                    if "error" in _r:
                        _steps.append({"step": "channel_stats", "status": "FAIL", "error": str(_r["error"])[:300], "duration_ms": round((_time.time() - _ts) * 1000)})
                        _overall = "FAIL"
                    else:
                        _steps.append({"step": "channel_stats", "status": "PASS", "duration_ms": round((_time.time() - _ts) * 1000)})
                except Exception as _e:
                    _steps.append({"step": "channel_stats", "status": "FAIL", "error": str(_e)[:300], "duration_ms": round((_time.time() - _ts) * 1000)})
                    _overall = "FAIL"

            print(json.dumps({"overall": _overall, "steps": _steps, "duration_ms": round((_time.time() - _t0) * 1000)}, indent=2))
            sys.exit(0)
        else:
            print(f"Unknown command: {cmd}")
            sys.exit(1)

        print(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": str(e)}, indent=2))
        sys.exit(1)
