# Phase 4 — CLI Handoff

**Branch:** `feat/brain-repo-onboarding`
**Date:** 2026-04-23

## What was done

### 1. `setup.py` — Brain Repo prompts in CLI

- Added 3 new helper functions:
  - `ask_password(prompt)` — reads a token/password via `getpass` (no echo)
  - `ask_choice(prompt, options, default)` — presents a numbered list and returns the chosen option string
  - `_ensure_brain_master_key()` — generates `BRAIN_REPO_MASTER_KEY` in `.env` if not present
  - `_save_brain_repo_pat(pat)` — encrypts and stores the PAT in `brain_repo_configs` SQLite table (or queues it in a `.enc` file if the DB doesn't exist yet)

- Added Brain Repo messages to all 3 language bundles in `MESSAGES` (`en-US`, `pt-BR`, `es`):
  - `brain_repo_enable_prompt`, `brain_repo_auth_method`, `brain_repo_defer_to_web`
  - `brain_repo_pat_instructions`, `brain_repo_pat_prompt`, `brain_repo_pat_saved`
  - `brain_repo_pat_skipped`, `brain_repo_configure_later`

- In `main()`, after `choose_provider()` (local mode only), added the Brain Repo wizard block:
  - Asks if Brain Repo should be enabled (`ask_bool`)
  - If yes: offers PAT or "configure later" (`ask_choice`)
  - If PAT: reads the token silently, generates master key, saves encrypted PAT

### 2. `backup.py` — GitHub target

- Added `backup_to_github(config)` function that calls `brain_repo.git_ops` (commit_all, push, create_tag) with tag `manual-backup-YYYY-MM-DD`
- Added `github` as a valid `--target` choice in argparse
- Added `elif args.target == "github":` branch in `main()`
- Usage: `python backup.py backup --target github`

### 3. i18n — 14 new Brain Repo keys

Added `brainRepo` namespace to all 3 locale files:
- `dashboard/frontend/src/i18n/locales/en-US/index.ts`
- `dashboard/frontend/src/i18n/locales/pt-BR/index.ts`
- `dashboard/frontend/src/i18n/locales/es/index.ts`

Keys: `brain_repo_enable_prompt`, `brain_repo_auth_method`, `brain_repo_defer_to_web`, `brain_repo_pat_instructions`, `brain_repo_pat_prompt`, `brain_repo_pat_saved`, `brain_repo_pat_skipped`, `brain_repo_configure_later`, `brain_repo_connect`, `brain_repo_disconnect`, `brain_repo_sync_now`, `brain_repo_create_milestone`, `brain_repo_status_connected`, `brain_repo_status_disconnected`

Key parity verified: `en - pt = set()`, `en - es = set()`, `pt - en = set()`.

### 4. Skill `/salve`

Created `.claude/skills/salve/SKILL.md` — flushes session to memory:
1. Reviews session (decisions, pending items, people, projects, metrics, deadlines)
2. Updates memory files (decisoes, pendencias, people, projects)
3. Creates session log at `memory/sessions/YYYY-MM-DD.md`
4. Informs user that Brain Repo file watcher will commit+push within 30 seconds

### 5. `.gitignore`

Added `memory/raw-transcripts/` line.

## Verification

- `python -c "import ast; ast.parse(open('setup.py', encoding='utf-8').read())"` — OK
- `python -c "import ast; ast.parse(open('backup.py', encoding='utf-8').read())"` — OK
- Key parity across all 3 locale files — OK (14 keys each, sets all empty)
- `.claude/skills/salve/SKILL.md` — created

## Next steps

- The Brain Repo web wizard (Settings > Integrations) should import `brain_repo_pat_pending.enc` if present after first boot
- `brain_repo.git_ops` module needs to be implemented for `backup --target github` to fully work
