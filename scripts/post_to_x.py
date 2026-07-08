#!/usr/bin/env python3
"""Post a tweet using an X OAuth2 user-context token.

Requires SOCIAL_TWITTER_<N>_ACCESS_TOKEN with tweet.write scope.
Media upload also requires media.write scope.
App-only bearer tokens cannot publish tweets.

Features:
  - Auto-refresh expired access tokens via refresh_token
  - Retry with exponential backoff on 429/rate-limit
  - Multi-account support via --account N
  - Dry-run mode for validation
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
# Token store persistente. X rotaciona o refresh_token a cada refresh, então os
# tokens precisam viver num arquivo gravável e persistente. Na VPS o .env da
# raiz não existe dentro do container e env vars de stack são estáticas —
# config/ é volume (evonexus_config), então config/social.env sobrevive a
# redeploys. Override via SOCIAL_ENV_PATH.
SOCIAL_ENV_PATH = Path(os.environ.get("SOCIAL_ENV_PATH") or (ROOT / "config" / "social.env"))
TWEET_URL = "https://api.x.com/2/tweets"
MEDIA_UPLOAD_URL = "https://api.x.com/2/media/upload"
TOKEN_REFRESH_URL = "https://api.x.com/2/oauth2/token"

# Retry configuration
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 15
RATE_LIMIT_BACKOFF_SECONDS = 900  # 15 min — X free tier resets every 15 min


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    parsed: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        parsed[key.strip()] = value.strip().strip('"').strip("'")
    return parsed


def read_env() -> dict[str, str]:
    """Merged env: processo < .env da raiz < social.env.

    social.env vence porque é onde o auto-refresh grava os tokens novos — o
    .env da raiz e as env vars de stack ficam desatualizados após o primeiro
    refresh (X invalida o refresh_token antigo a cada rotação). Exceção: uma
    reconexão manual via social-auth grava no .env; se o TOKEN_CREATED_AT de
    um prefixo for mais recente lá, as chaves desse prefixo no .env vencem.
    """
    root = _parse_env_file(ENV_PATH)
    social = _parse_env_file(SOCIAL_ENV_PATH)

    merged = dict(root)
    merged.update(social)
    for key, root_created in root.items():
        if not key.endswith("_TOKEN_CREATED_AT"):
            continue
        prefix = key[: -len("_TOKEN_CREATED_AT")]
        social_created = social.get(key, "")
        if root_created > social_created:
            for k, v in root.items():
                if k.startswith(prefix + "_"):
                    merged[k] = v

    env: dict[str, str] = dict(os.environ)
    for key, value in merged.items():
        # Tokens sociais rotacionam: o arquivo (social.env) é o source of
        # truth e vence env vars estáticas de stack. Demais chaves mantêm a
        # precedência clássica (env var do processo vence arquivo).
        if key.startswith("SOCIAL_"):
            env[key] = value
        else:
            env.setdefault(key, value)
    return env


def write_env(key: str, value: str):
    """Update a key in the persistent social token store (social.env)."""
    lines = []
    found = False
    if SOCIAL_ENV_PATH.exists():
        for line in SOCIAL_ENV_PATH.read_text(encoding="utf-8").splitlines():
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
    SOCIAL_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SOCIAL_ENV_PATH, "w") as f:
        f.writelines(lines)


def twitter_prefix(env: dict[str, str], index: int | None) -> str:
    if index is not None:
        return f"SOCIAL_TWITTER_{index}"
    indices = sorted(
        int(key.split("_")[2])
        for key in env
        if key.startswith("SOCIAL_TWITTER_") and key.endswith("_ACCESS_TOKEN")
    )
    if indices:
        return f"SOCIAL_TWITTER_{indices[0]}"
    bearer_indices = sorted(
        int(key.split("_")[2])
        for key in env
        if key.startswith("SOCIAL_TWITTER_") and key.endswith("_BEARER_TOKEN")
    )
    if bearer_indices:
        raise SystemExit(
            "Only SOCIAL_TWITTER bearer token found. X posting requires OAuth user context with tweet.write. "
            "Run: python3 social-auth/app.py and reconnect X/Twitter."
        )
    raise SystemExit("No SOCIAL_TWITTER account found. Run: python3 social-auth/app.py")


def refresh_access_token(env: dict[str, str], prefix: str) -> str | None:
    """Refresh an expired X access token using the refresh token.

    Returns the new access token on success, None on failure.
    Persists the new tokens in social.env (see SOCIAL_ENV_PATH).
    """
    refresh_token = env.get(f"{prefix}_REFRESH_TOKEN", "")
    client_id = env.get("TWITTER_CLIENT_ID", "")

    if not refresh_token:
        return None
    if not client_id:
        # Can't refresh without client_id
        return None

    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }).encode("utf-8")

    req = urllib.request.Request(
        TOKEN_REFRESH_URL,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, Exception) as exc:
        body = ""
        if isinstance(exc, urllib.error.HTTPError):
            body = exc.read().decode("utf-8", "ignore")
        print(f"  [warn] Token refresh failed: {body[:200]}", file=sys.stderr)
        return None

    new_access = result.get("access_token", "")
    new_refresh = result.get("refresh_token", "")

    if not new_access:
        return None

    # Save new tokens
    write_env(f"{prefix}_ACCESS_TOKEN", new_access)
    if new_refresh:
        write_env(f"{prefix}_REFRESH_TOKEN", new_refresh)
    from datetime import datetime, timezone
    write_env(f"{prefix}_TOKEN_CREATED_AT", datetime.now(timezone.utc).isoformat())

    print(f"  [info] Token refreshed for {prefix}", file=sys.stderr)
    return new_access


def _is_rate_limit_error(exc: urllib.error.HTTPError) -> bool:
    """Check if an HTTPError is a rate-limit (429) response."""
    if exc.code == 429:
        return True
    try:
        body = exc.read().decode("utf-8", "ignore").lower()
        return "rate limit" in body or "too many" in body
    except Exception:
        return False


def _get_retry_after(exc: urllib.error.HTTPError) -> int:
    """Extract Retry-After header value in seconds, or return default."""
    try:
        retry_after = exc.headers.get("Retry-After", "")
        if retry_after:
            return int(retry_after)
    except (ValueError, TypeError):
        pass
    return RATE_LIMIT_BACKOFF_SECONDS


def upload_media(path: Path, access_token: str) -> str:
    if not path.exists():
        raise SystemExit(f"Media file not found: {path}")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    payload = json.dumps(
        {
            "media": base64.b64encode(path.read_bytes()).decode("ascii"),
            "media_category": "tweet_image",
            "media_type": media_type,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        MEDIA_UPLOAD_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"X media upload HTTP {exc.code}: {body[:500]}") from exc
    media_id = result.get("data", {}).get("id")
    if not media_id:
        raise RuntimeError(f"X media upload returned no media id: {json.dumps(result)[:500]}")
    return media_id


def post_tweet(text: str, access_token: str, media_ids: list[str] | None = None) -> dict:
    body: dict[str, object] = {"text": text}
    if media_ids:
        body["media"] = {"media_ids": media_ids}
    payload = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        TWEET_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        raise RuntimeError(f"X API HTTP {exc.code}: {body[:500]}") from exc


def post_tweet_with_retry(
    text: str,
    access_token: str,
    media_ids: list[str] | None = None,
    prefix: str = "SOCIAL_TWITTER_1",
    env: dict[str, str] | None = None,
) -> dict:
    """Post a tweet with automatic retry + token refresh on 429/expiry.

    Strategy:
      1. Try posting. If 429 → backoff and retry.
      2. If token expired (401) → refresh token and retry.
      3. After MAX_RETRIES exhausted, raise RuntimeError.
    """
    if env is None:
        env = read_env()

    current_token = access_token

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return post_tweet(text, current_token, media_ids)
        except (urllib.error.HTTPError, RuntimeError) as exc:
            is_429 = False
            is_401 = False

            if isinstance(exc, urllib.error.HTTPError):
                is_429 = _is_rate_limit_error(exc)
                is_401 = exc.code == 401

            # Token expired — try refresh
            if is_401 and env:
                new_token = refresh_access_token(env, prefix)
                if new_token:
                    current_token = new_token
                    continue
                else:
                    raise RuntimeError(
                        "Token expired (401) and refresh failed. "
                        "Reconnect X/Twitter via python3 social-auth/app.py."
                    ) from exc

            # Rate limit — backoff
            if is_429 and attempt < MAX_RETRIES:
                wait = RATE_LIMIT_BACKOFF_SECONDS if attempt == 1 else BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                print(
                    f"  [rate-limit] Attempt {attempt}/{MAX_RETRIES} — "
                    f"waiting {wait}s before retry...",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue

            # Last attempt or non-retryable error
            if is_429:
                raise RuntimeError(
                    f"X rate limit exceeded after {MAX_RETRIES} attempts. "
                    f"Free tier resets every 15 minutes. Try again later or use --dry-run to validate."
                ) from exc
            raise

    raise RuntimeError(f"Failed after {MAX_RETRIES} attempts")


def main() -> int:
    parser = argparse.ArgumentParser(description="Post a tweet to X using OAuth user context")
    parser.add_argument("text", nargs="?", help="Tweet text. Reads stdin when omitted.")
    parser.add_argument("--account", type=int, help="SOCIAL_TWITTER_<N> account index")
    parser.add_argument("--media", type=Path, help="Image file to attach to the tweet")
    parser.add_argument("--dry-run", action="store_true", help="Validate token selection without posting")
    args = parser.parse_args()

    text = args.text if args.text is not None else sys.stdin.read()
    text = text.strip()
    if not text:
        raise SystemExit("Tweet text is required")
    if len(text) > 280:
        raise SystemExit(f"Tweet is {len(text)} characters; X limit is 280 for this script")

    env = read_env()
    prefix = twitter_prefix(env, args.account)
    access_token = env.get(f"{prefix}_ACCESS_TOKEN")
    if not access_token:
        raise SystemExit(
            f"{prefix}_ACCESS_TOKEN missing. Bearer/app-only auth cannot publish. "
            "Reconnect X/Twitter through python3 social-auth/app.py."
        )

    if args.dry_run:
        print(
            json.dumps(
                {
                    "ok": True,
                    "account": prefix,
                    "chars": len(text),
                    "media": str(args.media) if args.media else None,
                },
                indent=2,
            )
        )
        return 0

    media_ids = [upload_media(args.media, access_token)] if args.media else None
    result = post_tweet_with_retry(text, access_token, media_ids, prefix=prefix, env=env)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
