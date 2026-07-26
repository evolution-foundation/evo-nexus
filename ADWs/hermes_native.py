#!/usr/bin/env python3
"""
Native Hermes runner — calls Hermes CLI directly without adapter wrapper.

This is the preferred approach for Hermes integration. The runner.py
will call Hermes CLI directly with appropriate flags.
"""

import subprocess
import os
import json
from pathlib import Path


def run_hermes(
    prompt: str,
    max_turns: int = 90,
    timeout: int = 600,
    agent: str | None = None,
    cwd: str | None = None,
) -> dict:
    """
    Run Hermes CLI and return structured result.

    Args:
        prompt: The prompt to execute
        max_turns: Max conversation turns
        timeout: Timeout in seconds
        agent: Agent/skills to load
        cwd: Working directory

    Returns:
        Dict with success, stdout, stderr, returncode, duration, usage
    """
    from datetime import datetime
    import time

    start_time = time.time()
    workdir = cwd or str(Path.cwd())

    # Build Hermes command. --skills is a Hermes global flag, so keep it
    # before the chat subcommand.
    cmd = ["hermes"]

    if max_turns:
        env = os.environ.copy()
        env["HERMES_MAX_ITERATIONS"] = str(max_turns)
    else:
        env = os.environ.copy()

    if agent:
        cmd.extend(["--skills", agent])

    cmd.extend(["chat", "-Q", "-q", prompt])

    env["TERM"] = "dumb"

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=workdir,
            env=env,
        )

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            duration = time.time() - start_time

            # Try to extract usage from Hermes output if available
            usage = {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cache_creation_tokens": 0,
                "cache_read_tokens": 0,
                "cost_usd": 0.0,
            }

            # Parse JSON output if available
            result_text = stdout
            try:
                # Try to find JSON in output (Hermes may embed usage)
                json_start = stdout.find("{")
                json_end = stdout.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    json_str = stdout[json_start:json_end]
                    json_obj = json.loads(json_str)
                    if "usage" in json_obj:
                        usage.update(json_obj["usage"])
                    if "result" in json_obj:
                        result_text = json_obj["result"]
            except (json.JSONDecodeError, ValueError):
                pass

            return {
                "success": proc.returncode == 0,
                "stdout": result_text,
                "stderr": stderr,
                "returncode": proc.returncode,
                "duration": duration,
                "usage": usage,
            }

        except subprocess.TimeoutExpired:
            proc.kill()
            duration = time.time() - start_time
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Timeout after {timeout}s",
                "returncode": -1,
                "duration": duration,
                "usage": None,
            }

    except Exception as e:
        duration = time.time() - start_time
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "returncode": -3,
            "duration": duration,
            "usage": None,
        }
