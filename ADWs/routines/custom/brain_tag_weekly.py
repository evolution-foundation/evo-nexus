#!/usr/bin/env python3
"""ADW: Brain Tag Weekly — cria snapshot tag semanal | @system"""

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "dashboard", "backend"
))

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main():
    print("=== Brain Tag Weekly ===")
    try:
        from app import app  # type: ignore[import]
        from models import BrainRepoConfig  # type: ignore[import]
        from brain_repo.github_oauth import decrypt_token, get_master_key  # type: ignore[import]
        from brain_repo import git_ops  # type: ignore[import]

        with app.app_context():
            config = BrainRepoConfig.query.filter_by(sync_enabled=True).first()
            if config is None:
                log.info("brain_tag_weekly: no enabled brain repo config found, skipping")
                return

            if not config.local_path or not config.github_token_encrypted:
                log.warning("brain_tag_weekly: missing local_path or token, skipping")
                return

            brain_repo_dir = Path(config.local_path)
            master_key = get_master_key()
            token = decrypt_token(config.github_token_encrypted, master_key)

            now = datetime.now(timezone.utc)
            # ISO week: YYYY-WNN (e.g. 2026-W17)
            year, week, _ = now.isocalendar()
            tag = f"weekly/{year}-W{week:02d}"
            message = f"Weekly snapshot {year}-W{week:02d}"

            # Commit pending changes first
            git_ops.commit_all(brain_repo_dir, f"chore: weekly snapshot {year}-W{week:02d}")
            created = git_ops.create_tag(brain_repo_dir, tag, message)
            if created:
                git_ops.push(brain_repo_dir, token)
                print(f"brain_tag_weekly: created and pushed tag {tag}")
            else:
                print(f"brain_tag_weekly: tag {tag} already exists or creation failed")

    except Exception as exc:
        log.error("brain_tag_weekly failed: %s", exc)
        raise


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
