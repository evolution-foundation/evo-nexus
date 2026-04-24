"""File operations for plugin install/uninstall.

Handles:
- Copying plugin knowledge-layer files with namespace enforcement (plugin-{slug}-*)
- SHA256 manifest (.install-manifest.json) write and verification
- Rules index marker blocks in .claude/rules/_plugins-index.md
- Reverse (uninstall) removal from manifest

ADR reference: architecture-plugins.md step 4
Plan reference: plan-plugins-v1a.md step 4 (AC8, AC9, AC14, AC23, RF9, R7)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

WORKSPACE = Path(__file__).resolve().parent.parent.parent

# Marker pattern for rules index blocks
_MARKER_START = "<!-- PLUGIN:{slug}:START -->"
_MARKER_END = "<!-- PLUGIN:{slug}:END -->"
_MARKER_BLOCK_RE = re.compile(
    r"<!-- PLUGIN:([^:]+):START -->.+?<!-- PLUGIN:\1:END -->",
    re.DOTALL,
)

RULES_INDEX_PATH = WORKSPACE / ".claude" / "rules" / "_plugins-index.md"

# Manifest filename inside each installed plugin dir
MANIFEST_FILENAME = ".install-manifest.json"


# ---------------------------------------------------------------------------
# SHA256 helpers
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    """Compute SHA256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Namespace enforcement
# ---------------------------------------------------------------------------

def _enforce_namespace(filename: str, slug: str, category: str) -> str:
    """Return the namespaced destination filename.

    For agents/skills/commands/rules, the file must be named `plugin-{slug}-{original}`.
    If already prefixed correctly, returns as-is. Otherwise prepends the prefix.

    Args:
        filename: Source filename (e.g., 'my-agent.md').
        slug: Plugin slug (e.g., 'pm-essentials').
        category: 'agents', 'skills', 'commands', or 'rules'.

    Returns:
        Namespaced filename (e.g., 'plugin-pm-essentials-my-agent.md').
    """
    prefix = f"plugin-{slug}-"
    if filename.startswith(prefix):
        return filename
    return prefix + filename


# Regex to replace the `name:` value inside a YAML frontmatter block.
# Match anchored to the start of a line within the frontmatter. Supports
# quoted ("pm-nova") or bare (pm-nova) values.
_FRONTMATTER_NAME_RE = re.compile(
    r'(?m)^(name\s*:\s*)(?:"[^"]*"|\'[^\']*\'|[^\r\n]+)\s*$'
)


def _rewrite_frontmatter_name(md_path: Path, new_name: str) -> None:
    """Rewrite `name: <value>` inside the YAML frontmatter of a .md file.

    Keeps the Claude Code contract that `name` in frontmatter equals the
    filename (or skill directory name) without the `.md` suffix. Plugins
    ship bare names; the installer prefixes the filename with
    `plugin-{slug}-`, so the frontmatter must follow.

    No-op if the file doesn't have frontmatter or `name:` is missing.
    """
    try:
        raw = md_path.read_text(encoding="utf-8")
    except Exception:
        return

    # Only touch the first 60 lines — frontmatter lives at the top.
    # Split the file at `---` markers to avoid rewriting `name:` in prose.
    lines = raw.split("\n", 1)
    if not lines or not lines[0].strip().startswith("---"):
        return
    rest = lines[1] if len(lines) > 1 else ""
    # Find the closing `---`
    end_match = re.search(r"\n---\s*\n", rest)
    if not end_match:
        return

    frontmatter = rest[: end_match.start() + 1]  # include the trailing newline
    after = rest[end_match.start():]  # starts with \n---\n
    new_frontmatter, count = _FRONTMATTER_NAME_RE.subn(
        rf'\1"{new_name}"', frontmatter, count=1
    )
    if count == 0:
        return
    new_raw = lines[0] + "\n" + new_frontmatter + after
    md_path.write_text(new_raw, encoding="utf-8")


# ---------------------------------------------------------------------------
# copy_with_manifest
# ---------------------------------------------------------------------------

def copy_with_manifest(
    source_dir: Path,
    dest_dir: Path,
    slug: str,
    category: str,
    manifest_list: List[Dict[str, Any]],
    glob_pattern: str = "*.md",
) -> List[Dict[str, Any]]:
    """Copy files from source_dir to dest_dir with namespace enforcement.

    For each matched file:
    1. Computes SHA256 of source.
    2. Determines namespaced destination filename (plugin-{slug}-*).
    3. Copies to dest_dir.
    4. Appends a record to manifest_list.

    Args:
        source_dir: Directory containing files to copy (e.g., plugin/agents/).
        dest_dir: Destination directory (e.g., .claude/agents/).
        slug: Plugin slug.
        category: Category label for the manifest record (agents, skills, etc.).
        manifest_list: Accumulated list — records are appended in place.
        glob_pattern: Pattern for source files (default '*.md').

    Returns:
        List of newly added manifest records (same objects appended to manifest_list).

    Raises:
        FileNotFoundError: If source_dir does not exist.
        ValueError: If realpath of dest file escapes dest_dir (path traversal guard).
    """
    if not source_dir.exists():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_root = str(dest_dir.resolve()) + "/"

    added: List[Dict[str, Any]] = []

    # Skills are directories (`foo/SKILL.md`); everything else is a flat .md file.
    # Copy whole subdirs for skills, per-file for the rest. Keeps the Claude
    # Code contract that `name` in frontmatter == filename/dirname.
    if category == "skills":
        for src_entry in sorted(source_dir.iterdir()):
            if not src_entry.is_dir() or src_entry.name.startswith("."):
                continue
            dest_name = _enforce_namespace(src_entry.name, slug, category)
            dest_skill_dir = dest_dir / dest_name
            real_dest = str(dest_skill_dir.resolve())
            if not real_dest.startswith(dest_root):
                raise ValueError(
                    f"Path traversal detected: '{dest_name}' resolves outside dest_dir."
                )
            if dest_skill_dir.exists():
                shutil.rmtree(dest_skill_dir)
            shutil.copytree(src_entry, dest_skill_dir)

            # Rewrite `name:` inside SKILL.md so it matches the new dirname.
            skill_md = dest_skill_dir / "SKILL.md"
            if skill_md.is_file():
                _rewrite_frontmatter_name(skill_md, dest_name)
                sha256 = _sha256_file(skill_md)
            else:
                sha256 = ""

            record = {
                "src": str(src_entry),
                "dest": str(dest_skill_dir),
                "sha256": sha256,
                "category": category,
            }
            manifest_list.append(record)
            added.append(record)
            logger.debug("Copied skill dir %s → %s", src_entry.name, dest_name)
        return added

    # agents / commands / rules — flat .md files
    for src_file in sorted(source_dir.glob(glob_pattern)):
        if not src_file.is_file():
            continue

        dest_name = _enforce_namespace(src_file.name, slug, category)
        dest_file = dest_dir / dest_name

        real_dest = str(dest_file.resolve())
        if not real_dest.startswith(dest_root):
            raise ValueError(
                f"Path traversal detected: '{dest_name}' resolves outside dest_dir."
            )

        shutil.copy2(src_file, dest_file)

        # Rewrite `name:` inside the YAML frontmatter so it matches the
        # new filename (without the `.md` suffix). Skipped for rules/commands
        # that don't declare `name:` — helper is a no-op in that case.
        if category in {"agents", "commands", "skills"}:
            stem = dest_name[:-3] if dest_name.endswith(".md") else dest_name
            _rewrite_frontmatter_name(dest_file, stem)

        sha256 = _sha256_file(dest_file)
        record = {
            "src": str(src_file),
            "dest": str(dest_file),
            "sha256": sha256,
            "category": category,
        }
        manifest_list.append(record)
        added.append(record)

        logger.debug("Copied %s → %s (sha256=%s...)", src_file.name, dest_name, sha256[:12])

    return added


# ---------------------------------------------------------------------------
# write_manifest / read_manifest
# ---------------------------------------------------------------------------

def write_manifest(plugin_dir: Path, manifest_data: List[Dict[str, Any]]) -> Path:
    """Write .install-manifest.json to plugin_dir.

    Args:
        plugin_dir: The installed plugin directory.
        manifest_data: List of file records produced by copy_with_manifest().

    Returns:
        Path to the written manifest file.
    """
    manifest_path = plugin_dir / MANIFEST_FILENAME
    payload = {
        "version": 1,
        "files": manifest_data,
    }
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Wrote install manifest: %s (%d files)", manifest_path, len(manifest_data))
    return manifest_path


def read_manifest(plugin_dir: Path) -> Optional[Dict[str, Any]]:
    """Read .install-manifest.json from plugin_dir. Returns None if missing."""
    manifest_path = plugin_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        return None
    with open(manifest_path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Wave 2.0: In-place asset registration (icon + avatar)
# ---------------------------------------------------------------------------

# Maximum allowed size for any declared plugin asset (icon / avatar).
_ASSET_MAX_BYTES = 512 * 1024  # 512 KB

# Magic byte signatures for allowed image formats.
# References: PNG (RFC 2083), JPEG (JFIF/Exif), WEBP (RIFF container).
_MAGIC_PNG = b"\x89PNG\r\n\x1a\n"
_MAGIC_JPEG = b"\xff\xd8\xff"
_MAGIC_WEBP_RIFF = b"RIFF"
_MAGIC_WEBP_TAG = b"WEBP"


def _sniff_image_mime(header: bytes) -> Optional[str]:
    """Return MIME type from magic bytes, or None if not a supported image.

    Supports PNG, JPEG/JPG, and WEBP.  SVG is not supported and returns None.
    """
    if header[:8] == _MAGIC_PNG:
        return "image/png"
    if header[:3] == _MAGIC_JPEG:
        return "image/jpeg"
    # WEBP: "RIFF????WEBP" where ???? is 4-byte file size (little-endian)
    if header[:4] == _MAGIC_WEBP_RIFF and len(header) >= 12 and header[8:12] == _MAGIC_WEBP_TAG:
        return "image/webp"
    return None


def register_in_place_asset(
    plugin_dir: Path,
    rel_path: str,
    slug: str,
    expected_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate and register a plugin asset that lives in-place (not copied).

    Assets (icon, avatar) remain inside ``plugins/{slug}/ui/assets/`` and are
    served by the existing ``/plugins/<slug>/ui/<path>`` endpoint.  This
    function validates size, magic bytes (MIME sniff), and optional SHA256,
    then returns a record suitable for appending to the install manifest with
    ``category: "asset"``.

    Args:
        plugin_dir: Absolute path to the installed plugin directory.
        rel_path: Path to the asset relative to plugin_dir (e.g. ``ui/assets/icon.png``).
        slug: Plugin slug (for error messages).
        expected_sha256: If the manifest declares a SHA256, validate against it.

    Returns:
        Dict with keys ``rel_path``, ``sha256``, ``size_bytes``, ``mime``,
        ``category`` (= "asset").

    Raises:
        FileNotFoundError: If the asset file does not exist.
        ValueError: If size > 512KB, MIME not supported, or SHA mismatch.
    """
    asset_path = plugin_dir / rel_path

    # Realpath containment — asset must stay inside plugin_dir
    plugin_real = os.path.realpath(str(plugin_dir))
    asset_real = os.path.realpath(str(asset_path))
    if not asset_real.startswith(plugin_real + os.sep):
        raise ValueError(
            f"Asset path '{rel_path}' escapes plugin directory (path traversal)."
        )

    if not asset_path.exists():
        raise FileNotFoundError(
            f"Plugin '{slug}': declared asset '{rel_path}' not found in plugin directory."
        )

    # Size check
    size_bytes = asset_path.stat().st_size
    if size_bytes > _ASSET_MAX_BYTES:
        size_kb = size_bytes // 1024
        raise ValueError(
            f"Asset '{rel_path}' exceeds 512KB limit ({size_kb}KB). "
            "Reduce the image size or use a compressed format."
        )

    # Magic byte MIME sniff
    with open(asset_path, "rb") as f:
        header = f.read(16)
    mime = _sniff_image_mime(header)
    if mime is None:
        raise ValueError(
            f"Asset '{rel_path}' is not a supported image format. "
            "Allowed: PNG, JPEG, WEBP. SVG is rejected due to XSS risk."
        )

    # SHA256 — compute always; compare if declared
    actual_sha256 = _sha256_file(asset_path)
    if expected_sha256 is not None and actual_sha256 != expected_sha256.lower():
        raise ValueError(
            f"Asset '{rel_path}' SHA256 mismatch. "
            f"Expected: {expected_sha256}. Actual: {actual_sha256}."
        )

    logger.info(
        "Registered in-place asset '%s' for plugin '%s' (size=%dB, mime=%s, sha=%s...)",
        rel_path, slug, size_bytes, mime, actual_sha256[:12],
    )

    return {
        "category": "asset",
        "rel_path": rel_path,
        "sha256": actual_sha256,
        "size_bytes": size_bytes,
        "mime": mime,
    }


# ---------------------------------------------------------------------------
# Atomic file write helper (AC33 — TOCTOU prevention)
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* atomically using a temp file + os.replace().

    Creates a NamedTemporaryFile in the same directory as *path* so that
    os.replace() is a same-filesystem rename (POSIX: atomic). On failure the
    temporary file is cleaned up before re-raising.

    Args:
        path: Target file path. Parent directory must already exist.
        content: UTF-8 text to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Rules index (marker-delimited blocks)
# ---------------------------------------------------------------------------

def _build_block(slug: str, rule_filenames: List[str]) -> str:
    """Build a marker-delimited block for the rules index."""
    start = _MARKER_START.format(slug=slug)
    end = _MARKER_END.format(slug=slug)
    inner_lines = "\n".join(
        f"@{fn}" for fn in sorted(rule_filenames)
    )
    return f"{start}\n{inner_lines}\n{end}"


def append_rules_index(slug: str, rule_filenames: List[str]) -> None:
    """Append or replace a plugin's block in _plugins-index.md.

    If a block for this slug already exists, it is replaced (idempotent).
    Other plugins' blocks are preserved.

    Args:
        slug: Plugin slug.
        rule_filenames: List of rule filenames to reference (e.g., ['my-rule.md']).
    """
    RULES_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)

    existing = RULES_INDEX_PATH.read_text(encoding="utf-8") if RULES_INDEX_PATH.exists() else ""

    new_block = _build_block(slug, rule_filenames)

    # Check if block for this slug already exists
    pattern = re.compile(
        rf"<!-- PLUGIN:{re.escape(slug)}:START -->.+?<!-- PLUGIN:{re.escape(slug)}:END -->",
        re.DOTALL,
    )
    if pattern.search(existing):
        # Replace existing block
        updated = pattern.sub(new_block, existing)
    else:
        # Append new block
        sep = "\n" if existing and not existing.endswith("\n") else ""
        updated = existing + sep + new_block + "\n"

    _atomic_write(RULES_INDEX_PATH, updated)
    logger.info("Updated rules index for plugin '%s' (%d rules)", slug, len(rule_filenames))


def remove_rules_index(slug: str) -> None:
    """Remove a plugin's block from _plugins-index.md.

    Preserves other plugins' blocks. No-op if the index file or block doesn't exist.

    Args:
        slug: Plugin slug whose block should be removed.
    """
    if not RULES_INDEX_PATH.exists():
        return

    content = RULES_INDEX_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"\n?<!-- PLUGIN:{re.escape(slug)}:START -->.+?<!-- PLUGIN:{re.escape(slug)}:END -->\n?",
        re.DOTALL,
    )
    updated = pattern.sub("", content)
    _atomic_write(RULES_INDEX_PATH, updated)
    logger.info("Removed rules index block for plugin '%s'", slug)


# ---------------------------------------------------------------------------
# Wave 1.1: per-file capability toggle (skills / agents / commands)
# ---------------------------------------------------------------------------

# Map from capability type to the .claude/ subdirectory
_CAP_DIR: dict[str, str] = {
    "skills": "skills",
    "agents": "agents",
    "commands": "commands",
}


def _toggle_file_disabled(cap_type: str, slug: str, stem: str, disable: bool) -> bool:
    """Rename a plugin capability to/from its disabled form to hide/show it from Claude Code.

    Handles both file-based capabilities (.md files for agents/commands) and
    directory-based capabilities (skill directories). The disabled form is:
      - For .md files:    plugin-slug-name.md       -> plugin-slug-name.md.disabled
      - For directories:  plugin-slug-name/          -> plugin-slug-name.disabled/

    Args:
        cap_type: One of 'skills', 'agents', 'commands'.
        slug: Plugin slug (e.g. 'pm-essentials').
        stem: Namespaced name WITHOUT extension, e.g. 'plugin-pm-essentials-sprint-health'.
        disable: True to disable (rename to disabled form); False to re-enable.

    Returns:
        True if the operation succeeded or was a no-op; False on error.
    """
    # Security: reject cap_ids that could escape the target directory.
    # Valid stems are namespaced as plugin-{slug}-<name> where <name> is
    # alphanumeric plus hyphens/underscores/dots (covers .md filenames used
    # by rules). Anything with path separators or parent-dir segments is
    # rejected before any filesystem operation.
    _STEM_RE = re.compile(r"^plugin-[a-zA-Z0-9_-]+-[a-zA-Z0-9_.@-]+$")
    if not _STEM_RE.match(stem):
        logger.warning(
            "_toggle_file_disabled: rejected invalid cap_id '%s' for plugin '%s'", stem, slug
        )
        return False

    subdir = _CAP_DIR.get(cap_type)
    if not subdir:
        logger.warning("_toggle_file_disabled: unknown cap_type '%s'", cap_type)
        return False

    target_dir = WORKSPACE / ".claude" / subdir

    # Determine which form exists (file .md, directory, or their disabled counterparts)
    src_file = target_dir / f"{stem}.md"
    dst_file = target_dir / f"{stem}.md.disabled"
    src_dir = target_dir / stem          # directory form (e.g. skill bundles)
    dst_dir = target_dir / f"{stem}.disabled"

    try:
        if disable:
            if src_file.exists():
                src_file.rename(dst_file)
                logger.info("Disabled capability file: %s -> %s", src_file.name, dst_file.name)
            elif src_dir.is_dir():
                src_dir.rename(dst_dir)
                logger.info("Disabled capability dir: %s -> %s", src_dir.name, dst_dir.name)
            elif dst_file.exists() or dst_dir.is_dir():
                logger.debug("_toggle_file_disabled: already disabled: %s", stem)
            else:
                logger.warning("_toggle_file_disabled: source not found: %s", stem)
        else:
            if dst_file.exists():
                dst_file.rename(src_file)
                logger.info("Re-enabled capability file: %s -> %s", dst_file.name, src_file.name)
            elif dst_dir.is_dir():
                dst_dir.rename(src_dir)
                logger.info("Re-enabled capability dir: %s -> %s", dst_dir.name, src_dir.name)
            elif src_file.exists() or src_dir.is_dir():
                logger.debug("_toggle_file_disabled: already enabled: %s", stem)
            else:
                logger.warning("_toggle_file_disabled: neither enabled nor disabled form found: %s", stem)
        return True
    except OSError as exc:
        logger.error("_toggle_file_disabled failed for '%s': %s", stem, exc)
        return False


# ---------------------------------------------------------------------------
# reverse_remove_from_manifest (uninstall)
# ---------------------------------------------------------------------------

def reverse_remove_from_manifest(manifest_path: Path) -> None:
    """Delete files listed in .install-manifest.json in reverse order.

    Iterates the manifest file list in reverse (last-copied → first), deleting
    each dest file. If a file is already missing, logs a WARNING and continues
    (partial uninstall is safe).

    Args:
        manifest_path: Path to the .install-manifest.json file.

    Raises:
        FileNotFoundError: If manifest_path itself does not exist.
    """
    if not manifest_path.exists():
        raise FileNotFoundError(f"Install manifest not found: {manifest_path}")

    with open(manifest_path, encoding="utf-8") as f:
        data = json.load(f)

    files = data.get("files", [])
    for record in reversed(files):
        dest = Path(record["dest"])
        if dest.is_dir():
            # Skills are directories
            shutil.rmtree(dest, ignore_errors=True)
            logger.debug("Removed dir %s", dest)
        elif dest.exists():
            dest.unlink()
            logger.debug("Removed %s", dest)
        else:
            logger.warning("File already missing during uninstall (skipping): %s", dest)
