#!/usr/bin/env python3
"""Upload a local file to the MinIO/S3 bucket and print its public HTTPS URL.

Used by publishing agents (pixel/mako/pulse) in the goal-ticket-unification
publish flow: generate an image locally, upload it here to get a public URL,
then put that URL in the outcome's `publish_media` so the dashboard can hand
it to Postiz for the actual (human-approved) social post.

The returned URL's host must be listed in POSTIZ_ALLOWED_MEDIA_HOSTS on the
dashboard, otherwise the publish gate rejects it fail-closed.

Env (configure via the MinIO integration card in the dashboard, or Portainer):
  MINIO_ENDPOINT      S3 API endpoint, e.g. https://s3.workflowapi.com.br
  MINIO_ACCESS_KEY    access key
  MINIO_SECRET_KEY    secret key
  MINIO_BUCKET        target bucket (must allow public read), e.g. post
  MINIO_PUBLIC_BASE   base for the public URL (default: MINIO_ENDPOINT)

Usage:
  python upload.py /path/to/image.png
  python upload.py /path/to/image.png --key custom/name.png
Prints a JSON line {"url": "...", "key": "...", "bucket": "..."} on success,
and the bare URL on the last line for easy shell capture. Exits non-zero with
a message on stderr on failure.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import uuid
from pathlib import Path


def _env(name: str, *, required: bool = True, default: str | None = None) -> str:
    val = (os.environ.get(name) or "").strip()
    if not val:
        if default is not None:
            return default
        if required:
            print(f"error: env var {name} is not set (configure the MinIO integration)", file=sys.stderr)
            sys.exit(2)
        return ""
    return val


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload a file to MinIO/S3 and print its public URL")
    parser.add_argument("file", help="local path to the file to upload")
    parser.add_argument("--key", help="object key (default: media/<uuid><ext>)")
    parser.add_argument("--bucket", help="override MINIO_BUCKET")
    parser.add_argument("--content-type", help="override the guessed content type")
    args = parser.parse_args()

    src = Path(args.file).expanduser()
    if not src.is_file():
        print(f"error: file not found: {src}", file=sys.stderr)
        sys.exit(2)

    endpoint = _env("MINIO_ENDPOINT").rstrip("/")
    access_key = _env("MINIO_ACCESS_KEY")
    secret_key = _env("MINIO_SECRET_KEY")
    bucket = (args.bucket or "").strip() or _env("MINIO_BUCKET")
    public_base = _env("MINIO_PUBLIC_BASE", required=False, default=endpoint).rstrip("/")

    try:
        import boto3
        from botocore.client import Config
    except ImportError:
        print("error: boto3 is not installed in this runtime", file=sys.stderr)
        sys.exit(3)

    ext = src.suffix
    key = (args.key or "").strip() or f"media/{uuid.uuid4().hex}{ext}"
    content_type = args.content_type or mimetypes.guess_type(src.name)[0] or "application/octet-stream"

    # Path-style addressing — a custom domain (s3.workflowapi.com.br) can't do
    # virtual-host buckets, so force path style: {endpoint}/{bucket}/{key}.
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )

    extra = {"ContentType": content_type}
    # Only try to set a public-read ACL when explicitly asked — most MinIO
    # setups make the bucket public via a bucket POLICY (which the user already
    # did with "set public"), and object ACLs are often disabled, so setting
    # one would raise. Bucket policy already covers public read.
    if (os.environ.get("MINIO_SET_PUBLIC_ACL") or "").strip() in ("1", "true", "yes"):
        extra["ACL"] = "public-read"

    try:
        with src.open("rb") as fh:
            s3.upload_fileobj(fh, bucket, key, ExtraArgs=extra)
    except Exception as exc:  # noqa: BLE001
        print(f"error: upload failed: {exc}", file=sys.stderr)
        sys.exit(1)

    url = f"{public_base}/{bucket}/{key}"
    print(json.dumps({"url": url, "key": key, "bucket": bucket}, ensure_ascii=False))
    print(url)


if __name__ == "__main__":
    main()
