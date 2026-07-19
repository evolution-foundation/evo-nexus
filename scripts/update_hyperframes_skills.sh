#!/usr/bin/env bash
# scripts/update_hyperframes_skills.sh — refresh the official HyperFrames
# skill catalog inside .claude/skills-media/ (social-media-production,
# briefing Etapa 5).
#
# `hyperframes skills update` (bundled in the `hyperframes` npm package,
# pinned in media_worker/package.json) is the OFFICIAL, non-interactive
# updater — verified locally 2026-07-19: it does NOT accept a project-local
# target directory (its --dir flag only scopes prune/removed-detection, not
# the install itself); it always writes to the user's HOME-level skill
# directories (~/.claude/skills/, ~/.agents/skills/, and a few other AI-tool
# conventions it auto-detects). This script runs that official updater, then
# copies (never symlinks — the Docker build context can't follow symlinks
# outside the repo) exactly the skill folders it reports as "installed"
# into this repo's .claude/skills-media/, where the media-worker image
# bakes them in (Dockerfile.media-worker).
#
# Non-interactive. Never run this automatically at container/production
# startup — it is a deliberate, reviewed, human-triggered update:
#   1. Run it.
#   2. Read the diff it prints.
#   3. `git add .claude/skills-media && git commit` only if the diff looks right.
#
# Never touches .claude/skills-media/social-media-production/ (the
# EvoNexus-authored skill, not part of the official HyperFrames catalog).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="$REPO_ROOT/.claude/skills-media"
CUSTOM_SKILL="social-media-production"

# Prefer the pinned, lockfile-installed binary over a bare `npx hyperframes`
# (which would resolve @latest and defeat the whole point of pinning).
if [[ -x "$REPO_ROOT/media_worker/node_modules/.bin/hyperframes" ]]; then
  HF_BIN="$REPO_ROOT/media_worker/node_modules/.bin/hyperframes"
elif command -v hyperframes >/dev/null 2>&1; then
  HF_BIN="$(command -v hyperframes)"
else
  echo "error: hyperframes binary not found. Run 'npm ci' in media_worker/ first." >&2
  exit 1
fi

echo "==> Using $($HF_BIN --version 2>/dev/null || echo "$HF_BIN")"
echo "==> Running: $HF_BIN skills update (writes to ~/.claude/skills, ~/.agents/skills — official, HOME-scoped installer)"

UPDATE_JSON="$(mktemp)"
trap 'rm -f "$UPDATE_JSON"' EXIT

# --json prints pure JSON on stdout (the interactive progress boxes go to
# stderr, which is left to print normally so a human running this sees them).
"$HF_BIN" skills update --json > "$UPDATE_JSON"

# "installed" = just (re)installed this run; "current" = already at latest
# and left untouched. Both mean "present and up to date" — copy both.
INSTALLED_SKILLS="$(python3 -c "
import json
with open('$UPDATE_JSON') as f:
    data = json.load(f)
names = sorted(set(data.get('installed', [])) | set(data.get('current', [])))
for name in names:
    print(name)
")"

if [[ -z "$INSTALLED_SKILLS" ]]; then
  echo "error: 'hyperframes skills update --json' reported zero installed/current skills — aborting, nothing copied." >&2
  cat "$UPDATE_JSON" >&2
  exit 1
fi

echo "==> Official updater reports these skills present:"
echo "$INSTALLED_SKILLS" | sed 's/^/     - /'

mkdir -p "$TARGET_DIR"

echo "==> Copying into $TARGET_DIR (custom skill '$CUSTOM_SKILL' is never touched by this script)"
while IFS= read -r skill; do
  [[ -z "$skill" ]] && continue
  if [[ "$skill" == "$CUSTOM_SKILL" ]]; then
    echo "     skipping '$skill' (not part of the official catalog)"
    continue
  fi
  SRC="$HOME/.claude/skills/$skill"
  if [[ ! -d "$SRC" ]]; then
    echo "     warn: '$skill' reported installed but not found at $SRC — skipping" >&2
    continue
  fi
  rm -rf "$TARGET_DIR/$skill"
  cp -a "$SRC" "$TARGET_DIR/$skill"
  echo "     synced $skill"
done <<< "$INSTALLED_SKILLS"

echo
echo "==> Diff (review before committing):"
if git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "$REPO_ROOT" add -N "$TARGET_DIR" >/dev/null 2>&1 || true
  git -C "$REPO_ROOT" --no-pager diff --stat -- "$TARGET_DIR"
else
  echo "    (not a git repo — skipping diff)"
fi

echo
echo "==> Done. Review the diff above, then:"
echo "      git add .claude/skills-media && git commit -m 'chore: update hyperframes skills'"
echo "    This script does NOT run automatically in production — it is meant"
echo "    to be run by hand and committed deliberately (briefing Etapa 5)."
