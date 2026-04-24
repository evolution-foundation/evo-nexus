#!/usr/bin/env python3
"""ADW: Brain KB Mirror — exporta Knowledge Base para markdown | @system"""

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "dashboard", "backend"
))

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main():
    print("=== Brain KB Mirror ===")
    try:
        from app import app  # type: ignore[import]
        from models import BrainRepoConfig  # type: ignore[import]
        from brain_repo import kb_mirror  # type: ignore[import]

        with app.app_context():
            config = BrainRepoConfig.query.filter_by(sync_enabled=True).first()
            if config is None:
                log.info("brain_kb_mirror: no enabled brain repo config found, skipping")
                return

            if not config.local_path:
                log.warning("brain_kb_mirror: config has no local_path, skipping")
                return

            brain_repo_dir = Path(config.local_path)
            exported = kb_mirror.export_kb_to_markdown(brain_repo_dir)
            print(f"brain_kb_mirror: {exported} chunk(s) exported to markdown")

    except Exception as exc:
        log.error("brain_kb_mirror failed: %s", exc)
        raise


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
