#!/usr/bin/env python3
"""ADW: Brain Health — processa fila de sync pendente | @system"""

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
    print("=== Brain Health ===")
    try:
        from app import app  # type: ignore[import]
        from models import BrainRepoConfig  # type: ignore[import]
        from brain_repo.github_oauth import decrypt_token, get_master_key  # type: ignore[import]
        from brain_repo.sync_worker import SyncWorker  # type: ignore[import]

        with app.app_context():
            config = BrainRepoConfig.query.filter_by(sync_enabled=True).first()
            if config is None:
                log.info("brain_health: no enabled brain repo config found, skipping")
                return

            if not config.local_path or not config.github_token_encrypted:
                log.warning("brain_health: missing local_path or token, skipping")
                return

            brain_repo_dir = Path(config.local_path)
            install_dir = Path(__file__).resolve().parents[4]
            master_key = get_master_key()
            _token_enc = config.github_token_encrypted

            def _token_fn() -> str:
                return decrypt_token(_token_enc, master_key)

            worker = SyncWorker(
                install_dir=install_dir,
                brain_repo_dir=brain_repo_dir,
                token_fn=_token_fn,
            )
            processed = worker.process_pending()

            # Update badge count (remaining pending files)
            from brain_repo.sync_worker import PENDING_DIR_NAME  # type: ignore[import]
            pending_dir = install_dir.parent / PENDING_DIR_NAME
            remaining = len(list(pending_dir.glob("*.json"))) if pending_dir.exists() else 0
            worker.update_badge(config.user_id, remaining)

            print(f"brain_health: processed {processed} pending job(s), {remaining} remaining")

    except Exception as exc:
        log.error("brain_health failed: %s", exc)
        raise


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
