#!/usr/bin/env python3
"""ADW: Memory Lint — Weekly health check on memory/ base via Clawdia"""

import sys
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from runner import run_claude, banner, summary

PROMPT = """Run a memory lint (health check) on the memory/ directory:

1. Read memory/index.md to get the full catalog
2. For each file listed in the index, verify it still exists and is not empty
3. Detect orphan files — files in memory/ that exist but are NOT listed in the index
4. Cross-reference check:
   - People mentioned in projects/ should have a file in people/
   - Projects mentioned in people/ should have a file in projects/
   - Terms in glossary.md that reference people/projects should point to valid entries
5. Stale data check:
   - Read memory/projects/*.md — check if Linear issues listed are still open (use Linear MCP if available, otherwise flag for manual review)
   - Check if people roles/companies are still current based on recent daily logs
   - Flag any claim that includes a date older than 60 days as potentially stale
6. Contradiction check:
   - Compare CLAUDE.md hot cache with memory/glossary.md — flag mismatches
   - Compare people info in CLAUDE.md vs memory/people/*.md — flag divergences
7. Coverage gaps:
   - Check if active projects in CLAUDE.md all have a memory/projects/ file
   - Check if people in CLAUDE.md all have a memory/people/ file
8. Fix what you can automatically:
   - Update memory/index.md with any orphan files found
   - Fix obvious contradictions (prefer the more recent source)
   - Add missing cross-references
9. Append findings summary to memory/log.md with format: [DATE] LINT — summary
10. Report: issues found, fixed automatically, and items needing manual review.

Be concise. Only flag real problems, not hypothetical ones."""

def main():
    banner("🔍 Memory Lint", "Health check • Contradictions • Gaps • Stale data | @clawdia")
    results = []
    results.append(run_claude(PROMPT, log_name="memory-lint", timeout=600, agent="clawdia-assistant", daily_output_kind="memory-lint"))
    sys.exit(1 if summary(results, "Memory Lint") else 0)
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠ Cancelado.")
