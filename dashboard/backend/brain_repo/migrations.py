"""Brain Repo — Schema migrations between manifest versions."""

import logging
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

# Maps from_version string → migration function(repo_dir: Path) -> None
MIGRATION_REGISTRY: dict[str, Callable[[Path], None]] = {
    # Example entry (uncomment when migration from "0.9" to "1.0" is needed):
    # "0.9": _migrate_0_9_to_1_0,
}


def migrate(repo_dir: Path, from_version: str, to_version: str) -> None:
    """Run all necessary migrations from from_version up to to_version.

    Migrations are applied in sorted version order for all versions
    between from_version (exclusive) and to_version (inclusive).

    For schema 1.0 initial setup the registry is empty, so this is a no-op.
    """
    if from_version == to_version:
        log.debug("migrate: already at version %s, nothing to do", to_version)
        return

    def _version_tuple(v: str) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in str(v).split("."))
        except (ValueError, TypeError):
            return (0,)

    from_t = _version_tuple(from_version)
    to_t = _version_tuple(to_version)

    applied = 0
    for reg_version in sorted(MIGRATION_REGISTRY.keys(), key=_version_tuple):
        reg_t = _version_tuple(reg_version)
        if from_t < reg_t <= to_t:
            log.info("Applying brain repo migration: %s → %s", from_version, reg_version)
            try:
                MIGRATION_REGISTRY[reg_version](repo_dir)
                applied += 1
            except Exception as exc:
                raise RuntimeError(
                    f"Migration {reg_version} failed: {exc}"
                ) from exc

    if applied == 0:
        log.debug(
            "migrate: no registered migrations between %s and %s",
            from_version,
            to_version,
        )
    else:
        log.info("migrate: applied %d migration(s) to reach %s", applied, to_version)
