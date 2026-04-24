#!/usr/bin/env python3
"""ADW: Brain Sync Transcripts — copia transcripts Claude Code CLI | @system"""

import logging
import os
import sys
from pathlib import Path

# Add dashboard/backend to path (same pattern as memory_sync.py)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "dashboard", "backend"
))

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main():
    print("=== Brain Sync Transcripts ===")
    try:
        from app import app  # type: ignore[import]
        from models import BrainRepoConfig  # type: ignore[import]
        from brain_repo.github_oauth import decrypt_token, get_master_key  # type: ignore[import]
        from brain_repo import transcripts_mirror  # type: ignore[import]

        with app.app_context():
            config = BrainRepoConfig.query.filter_by(sync_enabled=True).first()
            if config is None:
                log.info("brain_sync_transcripts: no enabled brain repo config found, skipping")
                return

            if not config.local_path:
                log.warning("brain_sync_transcripts: config has no local_path, skipping")
                return

            brain_repo_dir = Path(config.local_path)
            # install_dir is 4 levels above dashboard/backend (i.e. workspace root)
            install_dir = Path(__file__).resolve().parents[4]

            copied = transcripts_mirror.mirror_transcripts(
                install_dir=install_dir,
                brain_repo_dir=brain_repo_dir,
            )
            print(f"brain_sync_transcripts: {copied} transcript file(s) copied/updated")

    except Exception as exc:
        log.error("brain_sync_transcripts failed: %s", exc)
        raise


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
