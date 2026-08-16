#!/bin/bash
# Agent Activity Tracker Hook
# Writes active agent state to .claude/agent-status.json
# Used by dashboard to show which agents are running in real-time
#
# The hook event arrives as argv[1] (configured in settings.json) and falls back
# to the `hook_event_name` field of the JSON payload on stdin. Claude Code does
# not export a CLAUDE_HOOK_EVENT environment variable.

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
STATUS_FILE="$PROJECT_DIR/.claude/agent-status.json"

# Initialize file if missing
if [ ! -f "$STATUS_FILE" ]; then
  echo '{"active_agents":[],"last_updated":""}' > "$STATUS_FILE"
fi

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
INPUT=$(cat)
EVENT="${1:-}"

# Fall back to the payload when the event is not passed as an argument.
if [ -z "$EVENT" ]; then
  EVENT=$(printf '%s' "$INPUT" | python3 -c \
    "import sys,json;print(json.load(sys.stdin).get('hook_event_name',''))" 2>/dev/null)
fi

if [ "$EVENT" = "PreToolUse" ] || [ "$EVENT" = "PostToolUse" ]; then
  # Parse the payload with json, not grep: the real payload is pretty-printed
  # ("tool_name": "Agent"), so a pattern anchored on `"tool_name":"` never matches.
  # Passing values via argv also avoids breaking on quotes in the description.
  printf '%s' "$INPUT" | python3 -c '
import json, sys

status_file, event, now = sys.argv[1], sys.argv[2], sys.argv[3]

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

if payload.get("tool_name") != "Agent":
    sys.exit(0)

tool_input = payload.get("tool_input") or {}
agent = payload.get("subagent_type") or tool_input.get("subagent_type") or "general-purpose"
description = payload.get("description") or tool_input.get("description") or ""

try:
    with open(status_file) as fh:
        data = json.load(fh)
except Exception:
    data = {"active_agents": [], "last_updated": ""}

active = data.get("active_agents") or []

if event == "PreToolUse":
    active.append({"agent": agent, "description": description, "started_at": now})
else:
    # PostToolUse: drop the most recent matching entry so the list reflects what
    # is running now, instead of everything that ever started this session.
    for i in range(len(active) - 1, -1, -1):
        entry = active[i]
        if entry.get("agent") == agent and entry.get("description") == description:
            active.pop(i)
            break

data["active_agents"] = active[-20:]
data["last_updated"] = now

with open(status_file, "w") as fh:
    json.dump(data, fh)
' "$STATUS_FILE" "$EVENT" "$NOW"

elif [ "$EVENT" = "Stop" ]; then
  # Clear active agents on session stop
  echo "{\"active_agents\":[],\"last_updated\":\"$NOW\"}" > "$STATUS_FILE"
fi

exit 0
