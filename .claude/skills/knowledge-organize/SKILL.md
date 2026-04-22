---
name: knowledge-organize
description: "Create, move, reorder, or link learning units and documents in the Knowledge base. Use when the user wants to structure content ('create unit Module 3', 'move these docs to lesson 2', 'reorder lessons by logical sequence', 'link doc X to unit Y')."
---

# knowledge-organize

Group: **Curation**. Units: create, move, reorder, link documents.

## When to trigger

- "Create unit 'Module 3'"
- "Reorder lessons by logical sequence"
- "Move these documents to unit X"
- "Link doc Y to lesson 2"

## Arguments

| Name | Type | Required | Description |
|---|---|---|---|
| `action` | str | yes | `create` \| `move` \| `reorder` \| `link` \| `update` |
| `connection` | str | no | Defaults to first ready |
| (action-specific args below) | | | |

## Actions

### `create`

Args: `space_id` (or `space` slug), `slug`, `title`, `sequence_idx` (optional), `description`, `prerequisites` (list uuids), `metadata` (dict).

```python
from dashboard.backend.sdk_client import evo

unit = evo.post(
    "/api/knowledge/v1/units",
    {"space_id": space_id, "slug": slug, "title": title, ...},
    headers={"X-Knowledge-Connection": connection},
)
```

### `move` (document → unit)

Args: `document_id`, `unit_id` (null to unlink).

```python
evo.patch(
    f"/api/knowledge/v1/documents/{document_id}",
    {"unit_id": unit_id},
    headers={"X-Knowledge-Connection": connection},
)
```

### `reorder` (units within space)

Args: `space_id`, `ordered_ids` (list of unit uuids in desired order).

```python
evo.post(
    "/api/knowledge/v1/units/reorder",
    {"space_id": space_id, "ordered_ids": ordered_ids},
    headers={"X-Knowledge-Connection": connection},
)
```

### `link` (N documents → 1 unit)

Args: `unit_id`, `document_ids` (list).

```python
for doc_id in document_ids:
    evo.patch(
        f"/api/knowledge/v1/documents/{doc_id}",
        {"unit_id": unit_id},
        headers={"X-Knowledge-Connection": connection},
    )
```

### `update` (unit metadata)

Args: `unit_id`, `title`, `description`, `sequence_idx`, `prerequisites`, `metadata`.

```python
evo.patch(
    f"/api/knowledge/v1/units/{unit_id}",
    {"title": title, "description": description, ...},
    headers={"X-Knowledge-Connection": connection},
)
```

## Output

Confirmation per action:

```
✓ Unit created: {title}
  slug: {slug}
  space: {space}
  sequence_idx: {N}
  id: {uuid}
```

For reorder:

```
✓ Reordered {N} units in space {space}:
  1. {title_1}
  2. {title_2}
  ...
```

## Actionable failures

- Invalid `action` → list valid actions
- `unit_id` not found → "Unit not found. Use `knowledge-browse scope=units`."
- Duplicate `sequence_idx` → suggest reorder
- Insufficient permission → "Token lacks `write` scope"
