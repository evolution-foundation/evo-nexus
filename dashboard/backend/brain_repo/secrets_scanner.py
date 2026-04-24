"""Brain Repo — Secret scanner for pre-commit security checks."""

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

# (name, pattern) — minimum 20 patterns
PATTERNS: list[tuple[str, str]] = [
    ("AWS_ACCESS_KEY", r"AKIA[0-9A-Z]{16}"),
    ("AWS_SECRET_KEY", r"(?i)aws.{0,20}[0-9a-zA-Z/+]{40}"),
    ("GITHUB_TOKEN", r"gh[pousr]_[A-Za-z0-9_]{36,255}"),
    ("ANTHROPIC_API_KEY", r"sk-ant-api[0-9]{2}-[A-Za-z0-9_\-]{93,}AA"),
    ("OPENAI_API_KEY", r"sk-[a-zA-Z0-9]{20,}T3BlbkFJ[a-zA-Z0-9]{20,}"),
    ("OPENAI_PROJECT_KEY", r"sk-proj-[A-Za-z0-9_\-]{40,}"),
    ("GENERIC_SECRET", r"(?i)(secret|api_key|private_key|access_token|auth_token)\s*[=:]\s*[\"']?[A-Za-z0-9_\-]{20,}[\"']?"),
    ("JWT_TOKEN", r"ey[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
    ("SSH_PRIVATE_KEY", r"-----BEGIN (?:RSA|EC|OPENSSH) PRIVATE KEY-----"),
    ("STRIPE_KEY", r"(?:sk|pk)_(?:live|test)_[0-9a-zA-Z]{24,}"),
    ("SENDGRID_KEY", r"SG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}"),
    ("TWILIO_KEY", r"SK[0-9a-fA-F]{32}"),
    ("SLACK_TOKEN", r"xox[baprs]-[0-9a-zA-Z\-]{10,}"),
    ("DISCORD_TOKEN", r"[MN][A-Za-z0-9]{23}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{27,}"),
    ("GOOGLE_API_KEY", r"AIza[0-9A-Za-z\-_]{35}"),
    ("AZURE_KEY", r"(?i)azure.{0,30}[A-Za-z0-9+/]{44}={0,2}"),
    ("DIGITALOCEAN_TOKEN", r"dop_v1_[a-f0-9]{64}"),
    ("HEROKU_KEY", r"(?i)heroku.{0,20}[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
    ("DATABASE_URL_WITH_PASSWORD", r"(?:postgres|mysql|mongodb)(?:ql)?://[^:]+:[^@]{6,}@"),
    ("FERNET_KEY", r"[A-Za-z0-9_\-]{43}="),
    ("GENERIC_PASSWORD", r"(?i)password\s*[=:]\s*[\"']?[A-Za-z0-9!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?]{8,}[\"']?"),
]

_CHECKED_EXTENSIONS = {
    ".py", ".env", ".yaml", ".yml", ".json", ".txt", ".md",
    ".sh", ".conf", ".ini", ".cfg",
}

_AUTO_EXCLUDE_PARTS = {".git", "__pycache__", ".venv", "node_modules"}
_AUTO_EXCLUDE_SUFFIXES = {".pyc"}


def _mask_match(match_text: str) -> str:
    """Mask a secret match: show first 4 + '***' + last 4 chars."""
    if len(match_text) <= 8:
        return "***"
    return match_text[:4] + "***" + match_text[-4:]


def _should_exclude(path: Path, extra_exclude: list[str] | None = None) -> bool:
    """Return True if path should be skipped."""
    parts = set(path.parts)
    if parts & _AUTO_EXCLUDE_PARTS:
        return True
    if path.suffix in _AUTO_EXCLUDE_SUFFIXES:
        return True
    if extra_exclude:
        for excl in extra_exclude:
            if excl in str(path):
                return True
    return False


def scan_files(files: list[Path]) -> list[dict]:
    """Scan a list of files for secret patterns.

    Returns:
        List of findings: [{"file": str, "line": int, "pattern": str, "snippet": str}]
    """
    findings: list[dict] = []
    compiled = [(name, re.compile(pattern)) for name, pattern in PATTERNS]

    for filepath in files:
        if filepath.suffix not in _CHECKED_EXTENSIONS:
            continue
        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            log.debug("Could not read %s: %s", filepath, exc)
            continue

        for lineno, line in enumerate(content.splitlines(), start=1):
            for name, regex in compiled:
                m = regex.search(line)
                if m:
                    findings.append({
                        "file": str(filepath),
                        "line": lineno,
                        "pattern": name,
                        "snippet": _mask_match(m.group(0)),
                    })

    return findings


def scan_directory(directory: Path, exclude: list[str] | None = None) -> list[dict]:
    """Recursively scan a directory for secrets.

    Args:
        directory: Root directory to scan.
        exclude: Additional path substrings to exclude.

    Returns:
        List of findings (same format as scan_files).
    """
    files_to_scan: list[Path] = []
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        if _should_exclude(path, exclude):
            continue
        files_to_scan.append(path)

    return scan_files(files_to_scan)
