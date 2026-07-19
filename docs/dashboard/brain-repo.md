# Brain Repo — GitHub Backup

The **Brain Repo** backs up the knowledge layers of the workspace (`memory/`, `workspace/`, `customizations/`, `config-safe/`) to a private GitHub repository — with secrets scanning before every push. It lives in the dashboard sidebar and gives you versioned, off-machine snapshots of everything the agents have learned.

## What Gets Synced

On every sync the pipeline runs: **mirror → secrets scan → commit → (tag) → push**.

- Watched folders: `memory/`, `workspace/`, `customizations/`, `config-safe/`
- `workspace/projects/` is **excluded** — user-cloned git repos have their own GitHub
- Any file containing a detected secret is dropped from the mirror before the commit — never pushed

## Connecting

1. Open **Brain Repo** in the dashboard sidebar
2. Provide a GitHub token (encrypted at rest with `BRAIN_REPO_MASTER_KEY`)
3. Create a new repo or connect an existing one — the local working clone lives under `dashboard/data/brain-repos/<repo-name>`

## Syncing

- **Sync now** — enqueues a mirror+commit+push (async; the UI polls `sync_in_progress`)
- **Milestone** — same pipeline with a named tag (`milestone/<name>`), for marking known-good states
- Snapshots/tags can be browsed and restored from the UI

## Portability (local ↔ VPS)

The local clone path is stored in the DB, but it is resolved **per machine** at sync time:

1. If the stored path has a valid clone, it's used as-is.
2. If not (e.g. the `dashboard.db` was restored from a backup made on another machine), the canonical path `{workspace}/dashboard/data/brain-repos/{repo_name}` is tried and persisted back.
3. If no clone exists anywhere — fresh volume, new VPS — the sync pipeline **re-clones automatically** from GitHub using the stored token and URL, then proceeds. The first sync after a migration just takes a bit longer.

"Re-connect" is only required when the stored credentials themselves are missing or can't be decrypted (e.g. `BRAIN_REPO_MASTER_KEY` changed).

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `Local brain repo … is missing or corrupt — re-connect` | No valid clone and no `repo_url`/`repo_name` stored | Reconnect the repo from the UI |
| `Could not decrypt stored token — re-connect` | `BRAIN_REPO_MASTER_KEY` missing or changed | Restore the key in `.env`, or reconnect with a fresh token |
| `SYNC_IN_PROGRESS` (409) | Another sync is running | Wait for it, or cancel from the UI |
| `git push failed` | Token expired / repo permissions | Regenerate the GitHub token and reconnect |

## Related

- Source: `dashboard/backend/routes/brain_repo.py`, `dashboard/backend/brain_repo/job_runner.py`
- [Environment Variables](../reference/env-variables.md) — `BRAIN_REPO_MASTER_KEY`
