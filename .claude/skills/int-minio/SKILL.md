---
name: int-minio
displayName: "MinIO / S3 Media"
description: "Upload media to a MinIO (S3-compatible) public bucket and get a public HTTPS URL. Used by publishing agents to host images/videos before a social post goes through the Postiz publish gate. Triggers: 'upload to minio', 'upload media', 'host image', 'public url for image', 'subir imagem', 'mídia pública'."
category: storage
envKeys: ["MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_BUCKET", "MINIO_PUBLIC_BASE"]
---

# MinIO / S3 Media

Uploads a local file to an S3-compatible bucket (MinIO) and returns a public
HTTPS URL. This is the media-hosting half of the social publishing flow: the
Postiz publish gate (`_run_publish_action` in the dashboard) only accepts media
as an HTTPS URL whose host is in `POSTIZ_ALLOWED_MEDIA_HOSTS`. MinIO gives you
that URL.

## Setup

Configure via the **MinIO / S3 Media** card in the dashboard (Integrations),
or in the Portainer stack env. All values live server-side; agents receive the
credentials in their run env (Option A — agents upload directly).

```
MINIO_ENDPOINT     = https://s3.workflowapi.com.br
MINIO_ACCESS_KEY   = <access key>
MINIO_SECRET_KEY   = <secret key>
MINIO_BUCKET       = post
MINIO_PUBLIC_BASE  = https://s3.workflowapi.com.br   # optional, defaults to MINIO_ENDPOINT
```

The bucket must allow **public read** (set via a MinIO bucket policy — the
"set public" toggle). Object ACLs are not used unless you set
`MINIO_SET_PUBLIC_ACL=1`.

## Usage

```bash
python .claude/skills/int-minio/scripts/upload.py /path/to/image.png
```

Prints a JSON line and then the bare URL (last line, easy to capture):

```
{"url": "https://s3.workflowapi.com.br/post/media/ab12….png", "key": "media/ab12….png", "bucket": "post"}
https://s3.workflowapi.com.br/post/media/ab12….png
```

Capture it in a shell:

```bash
URL=$(python .claude/skills/int-minio/scripts/upload.py ./post.png | tail -1)
```

## In the publish flow (publishing agents only)

When you (pixel-social-media / mako-marketing / pulse-community) prepare a post
that needs an image or video:

1. Generate the media locally (e.g. via the `ai-image-creator` skill).
2. Upload it here and capture the returned URL.
3. Put that URL in your outcome's `publish_media` array (and the caption in
   `publish_content`). Set `publish_intent: true` and `publish_target`.
4. The dashboard, after your human approves on Telegram, hands the URL to
   Postiz. Instagram **requires** at least one media URL; LinkedIn text posts
   do not.

The host of the returned URL (`s3.workflowapi.com.br`) must be present in
`POSTIZ_ALLOWED_MEDIA_HOSTS` on the dashboard, or the gate rejects it
fail-closed. See `docs/postiz-portainer-swarm.md`.
