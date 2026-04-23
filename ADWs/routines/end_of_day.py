#!/usr/bin/env python3
"""ADW: End of Day — Consolidação do dia via Clawdia"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from runner import run_skill, banner, summary

def main():
    banner("🌙 End of Day", "Memória • Logs • Tarefas • Aprendizados | @clawdia")
    results = []
    results.append(run_skill("prod-end-of-day", log_name="end-of-day", timeout=600, agent="clawdia-assistant", notify_telegram=True))
    summary(results, "End of Day")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠ Cancelado.")
