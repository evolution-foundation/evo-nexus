#!/usr/bin/env bash
# ============================================================================
# clean-history.sh — one-time rewrite to drop orphaned PNG avatars from history
#
# Context: dashboard/frontend/public/avatar/*.png was replaced by .webp in HEAD
# a while ago, but the old PNG blobs live forever in git history. They account
# for ~85% of .git/objects size (283 MB of 292 MB). Dropping them with
# git-filter-repo cuts fresh-clone size from ~290 MB to ~10 MB. See #26 for
# the full writeup.
#
# This script is SAFE BY DEFAULT:
#   * It never touches the original working tree.
#   * It never pushes to a remote. Force-push is the maintainer's call.
#   * It works on a throwaway `git clone --mirror` in a scratch directory.
#   * It aborts if the HEAD tree of develop or main changes after the rewrite
#     (i.e. if the path-regex accidentally matched something in HEAD).
#
# Usage:
#   ./scripts/clean-history.sh            # dry-run, writes the rewritten
#                                         # mirror to a scratch dir and prints
#                                         # a verification report.
#   ./scripts/clean-history.sh --help     # show options
#
# Requirements:
#   * git-filter-repo on PATH (install: `pip install git-filter-repo` or
#     `brew install git-filter-repo`).
#   * git 2.22+ (for --symbolic-full-name, commit-map support).
#
# After the dry-run, review the report. If everything checks out, the
# maintainer can push from the rewritten mirror using the instructions
# printed at the end of the script. Nothing in this file pushes; all
# remote-touching commands are emitted as copy-paste text.
# ============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REPO_URL_DEFAULT="https://github.com/EvolutionAPI/evo-nexus.git"
PATH_REGEX='^dashboard/frontend/public/avatar/.*\.png$'
BRANCHES=("develop" "main")

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
REPO_URL="$REPO_URL_DEFAULT"
WORK_DIR=""
FORCE_FILTER=0

usage() {
    cat <<USAGE
Usage: $0 [--repo URL] [--work-dir DIR] [--force-filter]

  --repo URL        Remote to clone as mirror. Default: $REPO_URL_DEFAULT
  --work-dir DIR    Scratch directory for the mirror clone.
                    Default: \$TMPDIR/evo-nexus-clean-history-<timestamp>
  --force-filter    Pass --force to git-filter-repo. Only needed when
                    cloning from a local non-bare repo (which copies its
                    stash/reflog and trips filter-repo's freshness check).
                    Never use this on a mirror of the real GitHub remote.

Dry-run only. Does not push. See top of script for details.
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --repo) REPO_URL="$2"; shift 2 ;;
        --work-dir) WORK_DIR="$2"; shift 2 ;;
        --force-filter) FORCE_FILTER=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown arg: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ -z "$WORK_DIR" ]; then
    WORK_DIR="${TMPDIR:-/tmp}/evo-nexus-clean-history-$(date +%Y%m%d-%H%M%S)"
fi

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
section() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
info()    { printf '    %s\n' "$*"; }
warn()    { printf '\033[1;33m!!  %s\033[0m\n' "$*" >&2; }
fail()    { printf '\033[1;31mxx  %s\033[0m\n' "$*" >&2; exit 1; }

section "Preflight"
command -v git >/dev/null 2>&1 || fail "git not on PATH"
command -v git-filter-repo >/dev/null 2>&1 \
    || fail "git-filter-repo not on PATH. Install with: pip install git-filter-repo"

git_version=$(git --version | awk '{print $3}')
info "git version: $git_version"
info "git-filter-repo: $(command -v git-filter-repo)"
info "repo: $REPO_URL"
info "work dir: $WORK_DIR"

if [ -e "$WORK_DIR" ]; then
    fail "work dir already exists — pass --work-dir or remove it first"
fi

# ---------------------------------------------------------------------------
# Step 1: fresh mirror clone
# ---------------------------------------------------------------------------
section "Clone mirror"
mkdir -p "$(dirname "$WORK_DIR")"
git clone --mirror --quiet "$REPO_URL" "$WORK_DIR"
info "mirror at $WORK_DIR"

cd "$WORK_DIR"

BEFORE_SIZE=$(du -sh . | awk '{print $1}')
BEFORE_OBJECTS=$(du -sh objects | awk '{print $1}')
BEFORE_COMMITS=$(git rev-list --all --count)
info "before: $BEFORE_SIZE total, $BEFORE_OBJECTS objects, $BEFORE_COMMITS commits across all refs"

# Record HEAD tree hashes per branch. These MUST be unchanged after rewrite
# — if they change, the regex matched something still in HEAD.
# (Using two parallel arrays instead of an associative array so the script
#  runs on bash 3.x — still the default on macOS.)
TREE_BRANCHES=()
TREE_HASHES=()
for br in "${BRANCHES[@]}"; do
    if git rev-parse --verify --quiet "refs/heads/$br" >/dev/null; then
        tree=$(git rev-parse "refs/heads/$br^{tree}")
        TREE_BRANCHES+=("$br")
        TREE_HASHES+=("$tree")
        info "  $br HEAD tree: $tree"
    else
        warn "  $br: branch not found in mirror, skipping tree check"
    fi
done

# Capture tags before so we can iterate them post-rewrite.
# (Avoid bash 4+ `mapfile` so the script runs on macOS's default bash 3.x.)
TAGS_BEFORE=()
while IFS= read -r t; do
    [ -n "$t" ] && TAGS_BEFORE+=("$t")
done < <(git tag | sort -V)
info "tags before: ${#TAGS_BEFORE[@]} total"

# ---------------------------------------------------------------------------
# Step 2: preview — how many PNG blobs match?
# ---------------------------------------------------------------------------
section "Preview matching paths"
# Every path touched anywhere in history that matches PATH_REGEX
MATCHING_PATHS=()
while IFS= read -r p; do
    [ -n "$p" ] && MATCHING_PATHS+=("$p")
done < <(
    git log --all --pretty=format: --name-only --diff-filter=AMD -- \
        'dashboard/frontend/public/avatar/*.png' \
        | grep -E "$PATH_REGEX" \
        | sort -u
)
info "distinct PNG paths in history: ${#MATCHING_PATHS[@]}"
if [ "${#MATCHING_PATHS[@]}" -eq 0 ]; then
    fail "regex matched zero paths — either history was already cleaned or regex is wrong"
fi
# Sanity: none of these should exist in HEAD on any tracked branch.
STILL_IN_HEAD=()
for br in "${BRANCHES[@]}"; do
    if git rev-parse --verify --quiet "refs/heads/$br" >/dev/null; then
        for p in "${MATCHING_PATHS[@]}"; do
            if git cat-file -e "refs/heads/$br:$p" 2>/dev/null; then
                STILL_IN_HEAD+=("$br:$p")
            fi
        done
    fi
done
if [ "${#STILL_IN_HEAD[@]}" -gt 0 ]; then
    warn "the following matching paths are STILL in HEAD — rewrite would drop live files:"
    printf '      %s\n' "${STILL_IN_HEAD[@]}" >&2
    fail "refusing to proceed — tighten the regex or restore the files in HEAD first"
fi
info "all ${#MATCHING_PATHS[@]} matching paths are orphaned (not in HEAD of any tracked branch) — safe to drop"

# ---------------------------------------------------------------------------
# Step 3: run git-filter-repo
# ---------------------------------------------------------------------------
section "Rewrite history"
# A fresh --mirror clone passes filter-repo's safety check without --force
# in normal use. Mirrors of local non-bare clones inherit stash/reflog and
# need --force; the CLI flag --force-filter opts in for testing only.
filter_args=(--path-regex "$PATH_REGEX" --invert-paths)
if [ "$FORCE_FILTER" -eq 1 ]; then
    warn "running git-filter-repo with --force (test/local mode)"
    filter_args+=(--force)
fi
git filter-repo "${filter_args[@]}"

AFTER_SIZE=$(du -sh . | awk '{print $1}')
AFTER_OBJECTS=$(du -sh objects | awk '{print $1}')
AFTER_COMMITS=$(git rev-list --all --count)
info "after:  $AFTER_SIZE total, $AFTER_OBJECTS objects, $AFTER_COMMITS commits across all refs"

# ---------------------------------------------------------------------------
# Step 4: verify HEAD trees are unchanged
# ---------------------------------------------------------------------------
section "Verify HEAD integrity"
TREE_MISMATCH=0
i=0
while [ "$i" -lt "${#TREE_BRANCHES[@]}" ]; do
    br="${TREE_BRANCHES[$i]}"
    before_tree="${TREE_HASHES[$i]}"
    after_tree=$(git rev-parse "refs/heads/$br^{tree}")
    if [ "$after_tree" = "$before_tree" ]; then
        info "  $br HEAD tree unchanged ✓ ($after_tree)"
    else
        warn "  $br HEAD tree CHANGED: $before_tree -> $after_tree"
        TREE_MISMATCH=1
    fi
    i=$((i + 1))
done
if [ "$TREE_MISMATCH" -ne 0 ]; then
    fail "HEAD tree changed on one or more branches — regex is too broad, aborting"
fi

# ---------------------------------------------------------------------------
# Step 5: verify tags still resolve (via commit-map)
# ---------------------------------------------------------------------------
section "Verify tags"
COMMIT_MAP="$WORK_DIR/filter-repo/commit-map"
if [ ! -f "$COMMIT_MAP" ]; then
    warn "commit-map not found at $COMMIT_MAP — cannot verify tag preservation"
else
    info "commit-map: $(wc -l < "$COMMIT_MAP") entries"
    PRESERVED=0
    LOST=()
    for t in "${TAGS_BEFORE[@]}"; do
        if git rev-parse --verify --quiet "refs/tags/$t" >/dev/null; then
            PRESERVED=$((PRESERVED + 1))
        else
            LOST+=("$t")
        fi
    done
    info "tags preserved: $PRESERVED / ${#TAGS_BEFORE[@]}"
    if [ "${#LOST[@]}" -gt 0 ]; then
        warn "tags missing after rewrite:"
        printf '      %s\n' "${LOST[@]}" >&2
        warn "filter-repo normally rewrites tags in place — if any are missing the maintainer should recreate them manually from commit-map"
    fi
fi

# ---------------------------------------------------------------------------
# Step 6: summary report
# ---------------------------------------------------------------------------
section "Summary"
COMMIT_DELTA=$((BEFORE_COMMITS - AFTER_COMMITS))
cat <<REPORT
  repo:               $REPO_URL
  mirror:             $WORK_DIR
  rewrite:            path-regex $PATH_REGEX (inverted)
  size before:        $BEFORE_SIZE total, $BEFORE_OBJECTS objects
  size after:         $AFTER_SIZE total, $AFTER_OBJECTS objects
  commits before:     $BEFORE_COMMITS
  commits after:      $AFTER_COMMITS (Δ=-$COMMIT_DELTA; filter-repo prunes
                      commits that become empty after the strip — these
                      commits *only* added/removed matching PNGs and carry
                      no other change. HEAD tree integrity check above
                      proves no live content was lost.)
  tags before:        ${#TAGS_BEFORE[@]}
  HEAD integrity:     all tracked branches unchanged ✓
  orphaned paths:     ${#MATCHING_PATHS[@]} PNGs removed from history
REPORT

# ---------------------------------------------------------------------------
# Step 7: next steps (printed, not executed)
# ---------------------------------------------------------------------------
section "Next steps (the maintainer runs these manually)"
cat <<NEXT
The dry-run produced a rewritten mirror at:
    $WORK_DIR

Recommended validation before pushing:
  1. Inspect the diff in tree-hash for a known-good commit:
        cd $WORK_DIR
        git log --oneline | head
        git show HEAD --stat | head -30

  2. Compare: total .git/objects dropped from $BEFORE_OBJECTS to $AFTER_OBJECTS.

  3. If everything looks right, force-push (requires admin):
        cd $WORK_DIR
        git push --force --all origin
        git push --force --tags origin

  4. After force-push, announce to the community:
        - Discord: 48h heads-up + link to partial-clone recipe
        - Add \`git clone --filter=blob:none <repo>\` to CONTRIBUTING.md (already
          done in this PR).
        - Existing clones keep working for reads; contributors with open
          branches rebase on the new base.

  5. This script does NOT delete $WORK_DIR. Remove it manually when done:
        rm -rf $WORK_DIR
NEXT

section "Done"
info "No remote was modified. Everything above is dry-run output."
