#!/usr/bin/env python3
"""ADW: Memory Sync — Consolidates memory via Clawdia"""

import sys
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from runner import run_claude, banner, summary

PROMPT = """Run the memory consolidation routine:

1. Read the last 3 daily logs in 'workspace/daily-logs/' (most recent first)
2. Read the meeting summaries from the last 3 days in 'workspace/meetings/summaries/'
3. Analyze recent git log: `git log --oneline --since="3 days ago"` and `git diff --stat HEAD~10` to understand what changed in the workspace
4. For each source, extract:
   - Decisions made → save in memory/ as type 'project'
   - New people or new context about people → save as type 'user' or update existing
   - Feedback or approach corrections → save as type 'feedback'
   - New terms or external references → save as type 'reference'
   - Skills or routines created/changed → update references if relevant
5. Before saving, check if similar memory already exists — update instead of duplicating
6. **Ingest propagation** — when saving/updating a memory, check which OTHER memories reference the same entity and update them too. Examples:
   - New info about a person → update their people/ file AND any projects/ that mention them
   - New project detail → update glossary.md if it has a codename entry
   - Role change → update people/ file, glossary.md nicknames table, and CLAUDE.md hot cache
7. Update MEMORY.md with pointers to new files
8. Update memory/index.md — ensure all files in memory/ are cataloged by category
9. Append operations to memory/log.md with format: [DATE] SYNC — summary of changes

Report at the end: how many memories created/updated by type, and how many cross-references propagated.
Be concise — don't create memories for obvious things or things already documented in code."""

def main():
    banner("🧠 Memory Sync", "Logs • Meetings → Memory | @clawdia")
    results = []
    results.append(run_claude(PROMPT, log_name="memory-sync", timeout=600, agent="clawdia-assistant", daily_output_kind="memory-sync"))
    sys.exit(1 if summary(results, "Memory Sync") else 0)
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠ Cancelled.")
