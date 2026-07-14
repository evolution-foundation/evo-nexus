#!/usr/bin/env python3
"""Host media on the Nexus S3 bucket and return a public presigned URL.

Instagram fetches media server-side, so it needs a public https URL. Instead of
making the bucket public, we upload the file and hand out a short-lived presigned
GET URL (default 1h) — enough for the Graph API to download during publishing.

Reuses the same S3 config as backups (BACKUP_S3_BUCKET + AWS_* / AWS_ENDPOINT_URL).

Usage:
  python3 scripts/media_host.py <local_file> [expires_seconds]
"""

from __future__ import annotations

import mimetypes
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PREFIX = "social-media/"
DEFAULT_EXPIRES = 3600


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


def _s3_client():
    import boto3  # imported lazily so non-upload paths don't require it
    endpoint = os.environ.get("AWS_ENDPOINT_URL")
    return boto3.client("s3", endpoint_url=endpoint) if endpoint else boto3.client("s3")


def is_configured() -> bool:
    return bool(os.environ.get("BACKUP_S3_BUCKET"))


def upload_and_presign(local_path: str | Path, *, prefix: str = DEFAULT_PREFIX,
                       expires: int = DEFAULT_EXPIRES) -> str:
    """Upload a local file to S3 and return a presigned GET URL."""
    bucket = os.environ.get("BACKUP_S3_BUCKET")
    if not bucket:
        raise RuntimeError("BACKUP_S3_BUCKET not configured in .env")
    p = Path(local_path)
    if not p.is_file():
        raise FileNotFoundError(f"media file not found: {p}")

    s3 = _s3_client()
    key = f"{prefix}{int(time.time())}-{p.name}"
    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    s3.upload_file(str(p), bucket, key, ExtraArgs={"ContentType": ctype})
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires,
    )


def main() -> int:
    _load_dotenv()
    if len(sys.argv) < 2:
        print("Usage: media_host.py <local_file> [expires_seconds]")
        return 1
    expires = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_EXPIRES
    try:
        url = upload_and_presign(sys.argv[1], expires=expires)
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
