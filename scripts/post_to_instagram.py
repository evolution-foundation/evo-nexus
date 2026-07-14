#!/usr/bin/env python3
"""Publish a post to Instagram via the Graph API.

Supports both Meta products:
  - "Instagram API com Login do Instagram" tokens (IGAA...) → graph.instagram.com
  - "Login do Facebook" tokens (EAA...) → graph.facebook.com (needs a linked Page)

IMPORTANT: Instagram fetches the media server-side, so the image/video must be at
a PUBLIC https URL. The API does not accept a direct binary upload of a local file.

Publishing is a 2-step (image) or 3-step (reels/carousel) flow:
  1. create a media container        POST /<ig-id>/media
  2. (video) poll until FINISHED     GET  /<container-id>?fields=status_code
  3. publish the container           POST /<ig-id>/media_publish

Usage:
  post_to_instagram.py --image-url https://host/foto.jpg --caption "texto" [--account 1]
  post_to_instagram.py --video-url https://host/reel.mp4 --caption "texto" --reels
  post_to_instagram.py --image-url URL1 --image-url URL2 --caption "..." --carousel
  add --dry-run to validate inputs without publishing
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FB_BASE_URL = "https://graph.facebook.com/v25.0"
IG_BASE_URL = "https://graph.instagram.com/v23.0"


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def _get_account(index: int | None) -> dict:
    """Resolve a SOCIAL_INSTAGRAM_<N> account from env (first one if index omitted)."""
    indices = sorted(
        int(m.group(1))
        for k in os.environ
        if (m := re.match(r"^SOCIAL_INSTAGRAM_(\d+)_LABEL$", k))
    )
    if not indices:
        return {}
    idx = index if index in indices else indices[0]
    return {
        "index": idx,
        "label": os.environ.get(f"SOCIAL_INSTAGRAM_{idx}_LABEL", f"Account {idx}"),
        "access_token": os.environ.get(f"SOCIAL_INSTAGRAM_{idx}_ACCESS_TOKEN", ""),
        "page_token": os.environ.get(f"SOCIAL_INSTAGRAM_{idx}_PAGE_TOKEN", ""),
        "account_id": os.environ.get(f"SOCIAL_INSTAGRAM_{idx}_ACCOUNT_ID", ""),
    }


def _token(account: dict) -> str:
    return account.get("page_token") or account.get("access_token", "")


def _resolve_media(ref: str) -> str:
    """Return a public https URL for a media reference.

    If `ref` is already an https URL, use it as-is. If it is a local file, upload
    it to S3 and return a presigned URL (requires BACKUP_S3_* configured).
    """
    if ref.startswith("https://"):
        return ref
    if ref.startswith("http://"):
        raise ValueError(f"media must be https (Instagram rejects http): {ref}")
    # treat as local file → host on S3
    import media_host
    if not media_host.is_configured():
        raise ValueError(
            f"'{ref}' is a local file but no S3 bucket is configured (BACKUP_S3_BUCKET). "
            "Pass a public https URL instead."
        )
    return media_host.upload_and_presign(ref)


def _is_ig_login_token(token: str) -> bool:
    return token.startswith("IG")


def _api_post(base: str, path: str, params: dict) -> dict:
    url = f"{base}/{path}"
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"error": f"HTTP {e.code}", "detail": body[:600]}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def _api_get(base: str, path: str, params: dict) -> dict:
    url = f"{base}/{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"error": f"HTTP {e.code}", "detail": body[:600]}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def _wait_for_container(base: str, container_id: str, token: str,
                        timeout_s: int = 300, interval_s: int = 5) -> dict:
    """Poll a video/reel container until it finishes processing."""
    deadline = time.time() + timeout_s
    last = {}
    while time.time() < deadline:
        last = _api_get(base, container_id, {"fields": "status_code,status", "access_token": token})
        if "error" in last:
            return last
        code = last.get("status_code", "")
        if code == "FINISHED":
            return last
        if code == "ERROR":
            return {"error": "container processing failed", "detail": last.get("status", "")}
        time.sleep(interval_s)
    return {"error": "timeout waiting for container to finish", "detail": json.dumps(last)}


def publish(account: dict, *, caption: str, image_urls: list[str],
            video_url: str | None, reels: bool, carousel: bool,
            dry_run: bool) -> dict:
    token = _token(account)
    base = IG_BASE_URL if _is_ig_login_token(token) else FB_BASE_URL
    ig_id = account.get("account_id", "") or ("me" if _is_ig_login_token(token) else "")

    if not token or not ig_id:
        return {"status": "error", "error": "missing access_token or account_id in .env"}
    if not image_urls and not video_url:
        return {"status": "error", "error": "provide --image-url and/or --video-url (https URL or local file)"}

    plan = {
        "account": account.get("label"),
        "ig_id": ig_id,
        "api": base,
        "type": "reels" if reels else "carousel" if carousel else "image",
        "media": video_url or (image_urls if carousel else image_urls[0]),
        "caption_len": len(caption),
    }
    if dry_run:
        return {"status": "dry_run", "plan": plan}

    # Resolve local files → public presigned S3 URLs (https URLs pass through)
    try:
        image_urls = [_resolve_media(u) for u in image_urls]
        if video_url:
            video_url = _resolve_media(video_url)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "step": "resolve_media", "error": str(e)}

    # ── Step 1: build container(s) ──
    if reels and video_url:
        cont = _api_post(base, f"{ig_id}/media", {
            "media_type": "REELS", "video_url": video_url,
            "caption": caption, "access_token": token,
        })
        if "error" in cont:
            return {"status": "error", "step": "create_container", **cont}
        creation_id = cont["id"]

    elif carousel:
        if len(image_urls) < 2:
            return {"status": "error", "error": "carousel needs at least 2 --image-url"}
        children = []
        for url in image_urls:
            item = _api_post(base, f"{ig_id}/media", {
                "image_url": url, "is_carousel_item": "true", "access_token": token,
            })
            if "error" in item:
                return {"status": "error", "step": "carousel_item", "url": url, **item}
            children.append(item["id"])
        cont = _api_post(base, f"{ig_id}/media", {
            "media_type": "CAROUSEL", "children": ",".join(children),
            "caption": caption, "access_token": token,
        })
        if "error" in cont:
            return {"status": "error", "step": "carousel_container", **cont}
        creation_id = cont["id"]

    else:  # single image
        cont = _api_post(base, f"{ig_id}/media", {
            "image_url": image_urls[0], "caption": caption, "access_token": token,
        })
        if "error" in cont:
            return {"status": "error", "step": "create_container", **cont}
        creation_id = cont["id"]

    # ── Step 2: wait until the container finished processing, then publish ──
    ready = _wait_for_container(base, creation_id, token)
    if "error" in ready:
        return {"status": "error", "step": "processing", "creation_id": creation_id, **ready}

    pub = _api_post(base, f"{ig_id}/media_publish", {
        "creation_id": creation_id, "access_token": token,
    })
    if "error" in pub:
        return {"status": "error", "step": "publish", "creation_id": creation_id, **pub}

    media_id = pub.get("id", "")
    permalink = ""
    perm = _api_get(base, media_id, {"fields": "permalink", "access_token": token})
    if "error" not in perm:
        permalink = perm.get("permalink", "")

    return {"status": "ok", "media_id": media_id, "permalink": permalink, "plan": plan}


def main() -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Publish a post to Instagram")
    parser.add_argument("--caption", default="", help="Post caption / text")
    parser.add_argument("--image-url", action="append", default=[],
                        help="Public https image URL (repeat for carousel)")
    parser.add_argument("--video-url", help="Public https video URL (use with --reels)")
    parser.add_argument("--reels", action="store_true", help="Publish video as a Reel")
    parser.add_argument("--carousel", action="store_true", help="Publish multiple images as a carousel")
    parser.add_argument("--account", type=int, help="SOCIAL_INSTAGRAM_<N> index (default: first)")
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs without publishing")
    args = parser.parse_args()

    account = _get_account(args.account)
    if not account:
        print(json.dumps({"status": "error", "error": "no SOCIAL_INSTAGRAM_* account in .env"}, indent=2))
        return 1

    result = publish(
        account,
        caption=args.caption,
        image_urls=args.image_url,
        video_url=args.video_url,
        reels=args.reels,
        carousel=args.carousel,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("status") in {"ok", "dry_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
