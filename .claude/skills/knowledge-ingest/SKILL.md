---
name: knowledge-ingest
description: "Upload a file (PDF, DOCX, PPTX, XLSX, HTML, EPUB, image) or URL to the Knowledge base. Triggers Marker parsing, chunking, embedding, and async classification. Use when the user says 'index this PDF', 'add this URL to the knowledge base', 'upload these files to Academy', or pastes a file path/URL with ingestion intent."
---

# knowledge-ingest

Group: **Ingestion**. Upload + automatic classification via pipeline parse → chunk → embed → enqueue classify.

## When to trigger

- "Index this PDF"
- "Add this URL to the knowledge base"
- "Upload these files to Academy"
- User passes a file path with ingestion intent

## Arguments

| Name | Type | Required | Description |
|---|---|---|---|
| `file_path` | str | one of two | Local path |
| `url` | str | one of two | URL to download first |
| `connection` | str | no | Defaults to first ready |
| `space` | str | yes | Destination space slug |
| `unit_id` | str | no | Associated unit |
| `title` | str | no | Derived from filename if absent |
| `tags` | list[str] | no | User-defined tags |

## Workflow

### Step 1 — Validate connection + space

If `connection` not provided, use first `ready`. If none: error ("Run `knowledge-admin action=connect`").

Validate space via `GET /spaces`. If not found: list spaces + ask for confirmation.

### Step 2 — Resolve file

If `url`:

```python
import requests, tempfile
from pathlib import Path
from urllib.parse import urlparse

parsed = urlparse(url)
filename = Path(parsed.path).name or "downloaded"
tmp = Path(tempfile.gettempdir()) / filename
with requests.get(url, stream=True, timeout=60) as r:
    r.raise_for_status()
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
file_path = str(tmp)
```

If `file_path`: validate existence.

### Step 3 — Multipart upload

```python
from dashboard.backend.sdk_client import evo

with open(file_path, "rb") as f:
    result = evo.post(
        "/api/knowledge/v1/documents",
        files={"file": f},
        data={
            "space": space,
            "unit_id": unit_id,
            "title": title or Path(file_path).stem,
            "tags": ",".join(tags or []),
        },
        headers={"X-Knowledge-Connection": connection},
    )

document_id = result["document_id"]
```

Endpoint returns 202 Accepted + document_id. Async worker.

### Step 4 — Poll status

Interval 2s, timeout 10min:

```python
import time
deadline = time.time() + 600
while time.time() < deadline:
    status = evo.get(
        f"/api/knowledge/v1/documents/{document_id}/status",
        headers={"X-Knowledge-Connection": connection},
    )
    phase = status.get("phase")
    if phase in ("done", "ready"):
        break
    if phase == "error":
        raise RuntimeError(status.get("error"))
    time.sleep(2)
```

### Step 5 — Fetch classification (non-blocking)

Classification is asynchronous. 1 extra GET on `/documents/{id}`:

- `content_type != null`: show full classification
- Else: "Classification pending — will appear in seconds via async worker"

## Output

```
✓ Document uploaded: {title}
  document_id: {uuid}
  space: {connection}/{space}
  unit: {unit_title or "none"}
  status: ready
  chunks: {N}
  classification:
    content_type: {lesson|tutorial|faq|...}
    difficulty: {...}
    topics: [...]
  elapsed: {X}s
```

## Actionable failures

- File not found → "File does not exist: `{path}`"
- URL fetch failed → "Download failed: {status_code}"
- Space not found → list available spaces
- Marker models missing → "Run `knowledge-admin action=install-parser`"
- Timeout → "Timeout after 10min. Status: `{phase}`. Check `knowledge-browse`."
