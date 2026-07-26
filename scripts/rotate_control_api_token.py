#!/usr/bin/env python3
"""Rotate HERMES_CONTROL_API_TOKEN with a safe overlap window.

Usage:
    python scripts/rotate_control_api_token.py

Generates a new token, moves the current token to
HERMES_CONTROL_API_TOKEN_PREVIOUS (so in-flight callers keep working), and
prints the values for the operator to place in .env / secret manager.
Never writes secrets to logs or stdout other than this explicit printout.
"""

from __future__ import annotations

import os
import secrets


def main() -> None:
    new_token = secrets.token_urlsafe(32)
    current_token = os.environ.get("HERMES_CONTROL_API_TOKEN", "")

    print("Set the following environment variables, then restart the dashboard:")
    print(f"HERMES_CONTROL_API_TOKEN={new_token}")
    if current_token:
        print(f"HERMES_CONTROL_API_TOKEN_PREVIOUS={current_token}")
    print()
    print("After all Hermes callers have picked up the new token (overlap window),")
    print("remove HERMES_CONTROL_API_TOKEN_PREVIOUS and restart again.")


if __name__ == "__main__":
    main()
