#!/usr/bin/env python3
"""
Hermes CLI adapter — wraps Hermes chat to be compatible with Claude Code/openclaude interface.

This adapter accepts the same CLI flags as Claude Code:
- --print (hermes uses -q/--query which prints result)
- --output-format json (hermes chat returns JSON when available)
- --max-turns N (mapped to Hermes' HERMES_MAX_ITERATIONS setting)
- --agent NAME (hermes uses --skills or profiles)
- prompt as positional argument

Output format matches Claude Code JSON:
{
  "result": "...",
  "usage": {
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "cost_usd": 0.0
  }
}
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Hermes CLI adapter for Claude Code/openclaude compatibility"
    )
    parser.add_argument("--print", action="store_true", help="Print result (compat flag)")
    parser.add_argument("--output-format", default="text", help="Output format (json/text)")
    parser.add_argument("--max-turns", type=int, help="Max conversation turns")
    parser.add_argument("--agent", help="Agent/skills to load")
    parser.add_argument("--profile", help="Hermes profile slug (privilege routing)")
    parser.add_argument("--dangerously-skip-permissions", action="store_true",
                        help="Skip permission prompts (compat flag, no-op for Hermes)")
    parser.add_argument("prompt", help="Prompt to execute")

    args = parser.parse_args()

    # Build Hermes command. --skills and -p/--profile are Hermes global flags,
    # so keep them before the chat subcommand.
    hermes_cmd = ["hermes"]

    # Select the Hermes profile per-invocation (does not touch
    # ~/.hermes/active_profile). Validate the slug to avoid arg injection.
    if args.profile:
        if not re.match(r"^[a-z0-9][a-z0-9-]*$", args.profile):
            print(json.dumps({
                "result": "",
                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0},
                "error": f"Invalid Hermes profile slug: {args.profile!r}",
            }, ensure_ascii=False))
            sys.exit(1)
        hermes_cmd.extend(["-p", args.profile])

    # Map flags to Hermes equivalents
    if args.max_turns:
        # Hermes uses max_turns from config or CLI if supported
        env = os.environ.copy()
        env["HERMES_MAX_ITERATIONS"] = str(args.max_turns)
    else:
        env = os.environ.copy()

    # EvoNexus agents identify personas; Hermes profiles control permissions.
    # Never reinterpret an agent slug as a Hermes skill: most slugs are not skills.

    hermes_cmd.extend(["chat", "-Q", "-q", args.prompt])

    # Suppress interactive elements
    env["TERM"] = "dumb"

    try:
        result = subprocess.run(
            hermes_cmd,
            capture_output=True,
            text=True,
            timeout=600,
            env=env,
            cwd=str(Path.cwd()),
        )

        # Preserve stderr for failures so callers can diagnose missing config,
        # missing binary, or provider auth errors without scraping logs.
        output = result.stdout
        # Hermes chat does not emit machine-readable usage. Keep the legacy
        # numeric shape for callers, but identify it as unavailable.
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        }

        if args.output_format == "json":
            response = {
                "result": output,
                "usage": usage,
                "usage_source": "unavailable",
            }
            if result.returncode != 0:
                response["error"] = result.stderr or output or f"Hermes exited with {result.returncode}"
            print(json.dumps(response, ensure_ascii=False))
        else:
            print(output, end="")
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr)

        sys.exit(result.returncode)

    except subprocess.TimeoutExpired:
        print(json.dumps({
            "result": "",
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0},
            "usage_source": "unavailable",
            "error": "Timeout after 600s"
        }, ensure_ascii=False))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({
            "result": "",
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0},
            "usage_source": "unavailable",
            "error": str(e)
        }, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
