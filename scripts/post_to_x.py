#!/usr/bin/env python3
"""Post a text tweet using an X OAuth2 user-context token.

Requires SOCIAL_TWITTER_<N>_ACCESS_TOKEN with tweet.write scope.
App-only bearer tokens cannot publish tweets.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
TWEET_URL = "https://api.x.com/2/tweets"


def read_env() -> dict[str, str]:
    env: dict[str, str] = dict(os.environ)
    if not ENV_PATH.exists():
        return env
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return env


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


def post_tweet(text: str, access_token: str) -> dict:
    payload = json.dumps({"text": text}).encode("utf-8")
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Post a tweet to X using OAuth user context")
    parser.add_argument("text", nargs="?", help="Tweet text. Reads stdin when omitted.")
    parser.add_argument("--account", type=int, help="SOCIAL_TWITTER_<N> account index")
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
        print(json.dumps({"ok": True, "account": prefix, "chars": len(text)}, indent=2))
        return 0

    result = post_tweet(text, access_token)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
