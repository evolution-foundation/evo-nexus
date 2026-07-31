#!/usr/bin/env python3
"""ADW: Good Morning — Briefing matinal via Clawdia"""

import sys
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from runner import run_skill, banner, summary

def main():
    banner("☀️  Good Morning", "Agenda • Emails • Tarefas | @clawdia")
    results = []
    results.append(run_skill("prod-good-morning", log_name="good-morning", timeout=600, agent="clawdia-assistant", notify_telegram=True, daily_output_kind="morning"))
    sys.exit(1 if summary(results, "Good Morning") else 0)
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠ Cancelado.")
