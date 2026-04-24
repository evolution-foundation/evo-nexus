"""Wave 2.5 — Deterministic regex security scanner for plugin artifacts.

This module runs as the first layer of the hybrid scan:
  1. Regex scan (this file) — always runs, deterministic, fast
  2. LLM scan (plugin_scan_runner.py) — optional, semantic, for markdown only

Public API:
    scan(staged_path, manifest) -> ScanVerdict

SCANNER_VERSION controls the cache key. Bump it whenever pattern sets change.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCANNER_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Severity constants
# ---------------------------------------------------------------------------
CRITICAL = "CRITICAL"
HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"
INFO = "INFO"

# ---------------------------------------------------------------------------
# Whitelisted domains — never flagged by net.non_whitelisted_url or
# hook.external_download
# ---------------------------------------------------------------------------
_WHITELISTED_DOMAINS: frozenset[str] = frozenset(
    {
        # Auth / AI APIs
        "api.anthropic.com",
        "api.openai.com",
        "api.github.com",
        # Package registries
        "npmjs.com",
        "pypi.org",
        "crates.io",
        # Docs / specs commonly referenced
        "semver.org",
        "modelcontextprotocol.io",
        "anthropic.com",
        "docs.anthropic.com",
        "developer.mozilla.org",
        "w3.org",
        "spec.whatwg.org",
        # GitHub CDN / raw content
        "github.com",
        "raw.githubusercontent.com",
        "objects.githubusercontent.com",
        # Google services (from integrations.md)
        "googleapis.com",
        "accounts.google.com",
        "calendar.google.com",
        "mail.google.com",
        "drive.google.com",
        # Common CDNs / trusted services
        "cdn.jsdelivr.net",
        "unpkg.com",
        "fonts.googleapis.com",
        "fonts.gstatic.com",
        # EvoNexus own domains
        "evolutionapi.com",
        "evo.ai",
        # Stripe — payments (integrations.md)
        "api.stripe.com",
        "stripe.com",
        # Notion (integrations.md)
        "api.notion.com",
        "notion.so",
        # Linear (integrations.md)
        "api.linear.app",
        "linear.app",
        # Discord (integrations.md)
        "discord.com",
        "discord.gg",
        "discordapp.com",
        # Telegram (integrations.md)
        "api.telegram.org",
        "t.me",
        # Asaas — Brazilian payments (integrations.md)
        "app.asaas.com",
        "asaas.com",
        # Bling — Brazilian ERP (integrations.md)
        "bling.com.br",
        "api.bling.com.br",
        # Omie — Brazilian ERP (integrations.md)
        "app.omie.com.br",
        "omie.com.br",
        # Fathom — meetings (integrations.md)
        "fathom.video",
        "api.fathom.video",
        # YouTube (integrations.md)
        "youtube.com",
        "www.youtube.com",
        "youtu.be",
        # Instagram Graph API (integrations.md)
        "graph.facebook.com",
        "instagram.com",
        # LinkedIn (integrations.md)
        "api.linkedin.com",
        "linkedin.com",
        # HubSpot (integrations.md)
        "api.hubapi.com",
        "hubspot.com",
        # DocuSign (integrations.md)
        "docusign.net",
        "docusign.com",
        # Amplitude (integrations.md)
        "api.amplitude.com",
        "amplitude.com",
        # Intercom (integrations.md)
        "api.intercom.io",
        "intercom.io",
        # Figma (integrations.md)
        "api.figma.com",
        "figma.com",
        # Canva (integrations.md)
        "api.canva.com",
        "canva.com",
        # Todoist (integrations.md)
        "api.todoist.com",
        "todoist.com",
    }
)

# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------


@dataclass
class PatternDef:
    category: str  # dotted category identifier
    severity: str
    pattern: re.Pattern[str]
    description: str
    include_exts: frozenset[str] | None = None  # None = all files
    exclude_exts: frozenset[str] | None = None


def _r(pattern: str, flags: int = re.IGNORECASE) -> re.Pattern[str]:
    return re.compile(pattern, flags)


# Extensions considered "code" (not binary)
_CODE_EXTS = frozenset(
    {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".sh", ".bash", ".zsh",
        ".md", ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg",
        ".html", ".htm", ".css", ".sql", ".txt", ".env",
    }
)

_MARKDOWN_EXTS = frozenset({".md", ".mdx"})
_SCRIPT_EXTS = frozenset({".py", ".js", ".ts", ".jsx", ".tsx", ".sh", ".bash", ".zsh"})
_DATA_EXTS = frozenset({".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".env", ".sql"})

# Patterns in order of severity: CRITICAL first, then HIGH, MEDIUM, LOW
_PATTERN_DEFS: list[PatternDef] = [
    # -------------------------------------------------------------------
    # CRITICAL — shell injection via exec/eval (Python/JS/shell)
    # -------------------------------------------------------------------
    PatternDef(
        category="shell.exec_injection",
        severity=CRITICAL,
        pattern=_r(
            # No (?x) — see note on ai.prompt_injection for why verbose + `#`
            # comments is unsafe. Keep this pattern inline.
            # Lookbehinds prevent false positives from method-style calls
            # like `regex.exec(str)` or `foo.eval(x)` where the dot indicates
            # access to a language builtin (RegExp.prototype.exec, etc).
            r"(?:"
            r"os\.system\s*\("
            r"|subprocess\.(?:call|run|Popen|check_output|check_call)\s*\("
            r"|(?<!\.)eval\s*\("
            r"|(?<!\.)exec\s*\("
            r"|__import__\s*\("
            r"|importlib\.import_module"
            r"|child_process"
            r"|(?<!\w)spawn\s*\("
            r")"
        ),
        description="Dangerous subprocess or eval execution found",
        include_exts=_SCRIPT_EXTS,
    ),
    # -------------------------------------------------------------------
    # CRITICAL — external download / payload fetch
    # -------------------------------------------------------------------
    PatternDef(
        category="hook.external_download",
        severity=CRITICAL,
        pattern=_r(
            r"""(?x)
            (?:
                curl\s+[^|;\n]*-[oO]\s    # curl -o / curl -O
              | wget\s+[^|;\n]*-[oO]\s    # wget -o / wget -O
              | wget\s+\S+\.(?:exe|sh|bin|py|ps1)  # wget *.exe|sh|bin etc
              | curl\s+\S+\.(?:exe|sh|bin|py|ps1)  # curl *.exe|sh|bin etc
              | urllib\.request\.urlretrieve  # Python urlretrieve to file
              | requests\.get.*\.content    # requests binary download
            )
            """
        ),
        description="Plugin downloads external payload at install/runtime",
        include_exts=_SCRIPT_EXTS | frozenset({".sh", ".bash"}),
    ),
    # -------------------------------------------------------------------
    # CRITICAL — dangerous SQL: DROP table, GRANT, ATTACH outside slug ns
    # -------------------------------------------------------------------
    PatternDef(
        category="sql.dangerous_statement",
        severity=CRITICAL,
        pattern=_r(
            r"""(?x)
            (?:
                \bDROP\s+TABLE\b       # DROP TABLE
              | \bGRANT\b              # GRANT
              | \bATTACH\s+DATABASE\b  # ATTACH DATABASE
              | \bDROP\s+DATABASE\b    # DROP DATABASE
              | \bTRUNCATE\s+TABLE\b   # TRUNCATE TABLE
            )
            """,
            flags=re.IGNORECASE,
        ),
        description="Dangerous SQL statement (DROP/GRANT/ATTACH/TRUNCATE) detected",
        include_exts=frozenset({".sql", ".py", ".js", ".ts"}),
    ),
    # -------------------------------------------------------------------
    # CRITICAL — credential harvesting: API key / secret exfil patterns
    # -------------------------------------------------------------------
    PatternDef(
        category="sec.credential_harvest",
        severity=CRITICAL,
        pattern=_r(
            r"""(?x)
            (?:
                os\.environ(?:\.get)?\s*\(['"]\w*(?:KEY|SECRET|TOKEN|PASSWORD|PASS|PWD|AUTH)\w*['"]\)  # env read
              | requests\.(post|get|put)\(.*(?:api_key|secret|password|token)  # sending secrets
            )
            """
        ),
        description="Potential credential harvesting or exfiltration pattern detected",
        include_exts=_SCRIPT_EXTS,
    ),
    # -------------------------------------------------------------------
    # HIGH — JavaScript eval / Function constructor / dynamic import
    # -------------------------------------------------------------------
    PatternDef(
        category="js.dynamic_eval",
        severity=HIGH,
        pattern=_r(
            r"""(?x)
            (?:
                new\s+Function\s*\(       # new Function(
              | import\s*\(.*\+           # import(variable + concatenation)
              | require\s*\(.*\+          # require(var + concat)
              | eval\s*`                  # eval template literal
              | Function\.prototype\.call # Function prototype abuse
            )
            """
        ),
        description="Dynamic JavaScript eval or dynamic import with concatenation",
        include_exts=frozenset({".js", ".ts", ".jsx", ".tsx"}),
    ),
    # -------------------------------------------------------------------
    # HIGH — large base64 / hex blob (≥40 chars of encoded data)
    # -------------------------------------------------------------------
    PatternDef(
        category="enc.base64_blob",
        severity=HIGH,
        pattern=_r(
            r"""(?x)
            (?:
                [A-Za-z0-9+/]{40,}={0,2}   # raw base64 ≥40 chars
              | (?:atob|btoa)\s*\(          # JS atob/btoa
              | Buffer\.from\s*\([^,]+,\s*['"]base64['"]  # Node Buffer.from base64
              | base64\.(?:b64decode|b64encode)\s*\(  # Python base64
              | 0x[0-9a-fA-F]{40,}         # hex blob ≥40 chars
            )
            """
        ),
        description="Large base64 or hex encoded blob detected (potential hidden payload)",
        include_exts=_CODE_EXTS,
    ),
    # -------------------------------------------------------------------
    # HIGH — access to sensitive system files
    # -------------------------------------------------------------------
    PatternDef(
        category="fs.sensitive_file_access",
        severity=HIGH,
        pattern=_r(
            r"""(?x)
            (?:
                [/\\]\.ssh[/\\]           # ~/.ssh/
              | [/\\]\.aws[/\\]           # ~/.aws/
              | id_rsa                    # SSH private key filename
              | id_ed25519                # SSH Ed25519 key
              | [/\\]etc[/\\](?:passwd|shadow|sudoers)  # /etc/passwd etc
              | /proc/[a-z]              # /proc/ enumeration
              | \.env(?:ironment)?$       # .env files in path
              | /var/log                  # Log file access
            )
            """
        ),
        description="Access to sensitive system files or directories",
        include_exts=_CODE_EXTS,
    ),
    # -------------------------------------------------------------------
    # MEDIUM — non-whitelisted external URL in hook/script
    # (checked per-match in _check_url_whitelist, not via simple pattern)
    # Placeholder — actual logic is in _scan_file for this category
    # -------------------------------------------------------------------
    # PatternDef for net.non_whitelisted_url is handled specially below
    # -------------------------------------------------------------------
    # MEDIUM — SQL table access outside plugin's namespace ({slug}_ prefix)
    # (checked per-match with slug context, handled specially below)
    # -------------------------------------------------------------------
    # MEDIUM — writable_data scope violations (plugin writes outside its dir)
    # -------------------------------------------------------------------
    PatternDef(
        category="fs.writable_scope",
        severity=MEDIUM,
        pattern=_r(
            r"""(?x)
            (?:
                open\s*\([^)]*,\s*['"]w  # open(..., 'w'
              | Path\([^)]*\)\.write_    # Path(...).write_text/bytes
              | shutil\.copy(?:tree)?\s*\(  # shutil.copy/copytree
              | os\.(?:makedirs|mkdir|rename|replace)\s*\(  # fs mutations
            )
            """
        ),
        description="File write operation detected outside plugin data directory",
        include_exts=_SCRIPT_EXTS,
    ),
    # -------------------------------------------------------------------
    # MEDIUM — unencrypted HTTP (non-localhost)
    # -------------------------------------------------------------------
    PatternDef(
        category="net.unencrypted_http",
        severity=MEDIUM,
        pattern=_r(r"http://(?!(?:localhost|127\.0\.0\.1|0\.0\.0\.0|::1))"),
        description="Unencrypted HTTP URL to non-localhost endpoint",
        include_exts=_CODE_EXTS,
    ),
    # -------------------------------------------------------------------
    # LOW — suspicious TLD (common free/throwaway domains)
    # -------------------------------------------------------------------
    PatternDef(
        category="net.suspicious_tld",
        severity=LOW,
        pattern=_r(
            r"https?://[^\s\"']+\.(?:xyz|tk|ml|ga|cf|gq|pw|cc|top|link|click|win|party)\b"
        ),
        description="URL with suspicious TLD (common in malware campaigns)",
        include_exts=_CODE_EXTS,
    ),
    # -------------------------------------------------------------------
    # LOW — prompt injection markers in markdown
    # -------------------------------------------------------------------
    PatternDef(
        category="ai.prompt_injection",
        severity=LOW,
        pattern=_r(
            # NOTE: This pattern does NOT use (?x) verbose mode because in
            # verbose mode an unescaped `#` starts a comment that runs to EOL,
            # which silently truncated several alternatives (e.g. `###\s*System`)
            # and caused the entire regex to match empty strings at every offset
            # — firing ~10k false positives per markdown file scanned.
            r"(?:"
            r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?"
            r"|you\s+are\s+now\s+(?:a|an)\s+(?:new\s+)?(?:assistant|AI|GPT|LLM)"
            r"|disregard\s+(?:all\s+)?(?:previous|prior)\s+instructions?"
            r"|\[\[SYSTEM\]\]"
            r"|<\|(?:system|user|assistant)\|>"
            r"|\#\#\#\s*System\s+Override"
            r"|act\s+as\s+(?:a\s+)?(?:different|new)\s+AI"
            r")",
            flags=re.IGNORECASE,
        ),
        description="Possible prompt injection pattern in markdown",
        include_exts=_MARKDOWN_EXTS,
    ),
    # -------------------------------------------------------------------
    # LOW — excessive capabilities (writable_data mentions system dirs)
    # -------------------------------------------------------------------
    PatternDef(
        category="cap.excessive_scope",
        severity=LOW,
        pattern=_r(
            r"""(?x)
            (?:
                writable_data\s*:\s*['"]/(?:etc|var|usr|opt|home|root)  # /etc etc in writable_data
              | write_access\s*:\s*true  # blanket write access
            )
            """,
            flags=re.IGNORECASE,
        ),
        description="Plugin manifest claims excessive filesystem scope",
        include_exts=frozenset({".yaml", ".yml"}),
    ),
]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    category: str
    severity: str
    file: str
    line: int
    snippet: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "file": self.file,
            "line": self.line,
            "snippet": self.snippet,
            "description": self.description,
        }


@dataclass
class ScanVerdict:
    verdict: str  # "APPROVE" | "WARN" | "BLOCK"
    findings: list[Finding] = field(default_factory=list)
    scanner_version: str = SCANNER_VERSION
    tarball_sha256: str = ""
    scanned_files: int = 0
    llm_augmented: bool = False  # set by plugin_scan_runner after LLM pass

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "findings": [f.to_dict() for f in self.findings],
            "scanner_version": self.scanner_version,
            "tarball_sha256": self.tarball_sha256,
            "scanned_files": self.scanned_files,
            "llm_augmented": self.llm_augmented,
        }


# ---------------------------------------------------------------------------
# URL whitelist helper
# ---------------------------------------------------------------------------


def _is_whitelisted_url(url: str) -> bool:
    """Return True if the URL's domain (or parent domain) is whitelisted."""
    try:
        # Extract hostname
        after_scheme = url.split("://", 1)[-1]
        host = after_scheme.split("/")[0].split(":")[0].lower()
        # Check exact match or suffix match (e.g. api.github.com matches github.com)
        for domain in _WHITELISTED_DOMAINS:
            if host == domain or host.endswith("." + domain):
                return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Core file scanner
# ---------------------------------------------------------------------------


def _scan_file(rel_path: str, content: str, pattern_defs: list[PatternDef]) -> list[Finding]:
    """Scan a single file's content against all applicable patterns."""
    findings: list[Finding] = []
    ext = Path(rel_path).suffix.lower()
    lines = content.splitlines()

    for pdef in pattern_defs:
        # Extension filter
        if pdef.include_exts is not None and ext not in pdef.include_exts:
            continue
        if pdef.exclude_exts is not None and ext in pdef.exclude_exts:
            continue

        for m in pdef.pattern.finditer(content):
            # Compute line number from match start
            line_no = content[: m.start()].count("\n") + 1
            snippet = lines[line_no - 1].strip()[:120] if line_no <= len(lines) else ""

            # Special handling: net.non_whitelisted_url skip if whitelisted
            if pdef.category in ("net.non_whitelisted_url", "net.suspicious_tld"):
                if _is_whitelisted_url(m.group(0)):
                    continue

            # sql.dangerous_statement: DROP TABLE is legitimate inside
            # uninstall.sql — the manifest contract requires plugins to clean
            # up their own tables. Skip when the file is the uninstall migration.
            if pdef.category == "sql.dangerous_statement":
                lower_path = rel_path.lower()
                if lower_path.endswith("uninstall.sql") or lower_path.endswith("/uninstall.sql"):
                    continue

            # enc.base64_blob: 64-char hex on a `sha256:` key is a legitimate
            # asset fingerprint, not an obfuscated payload. Skip when the
            # surrounding line/key contains sha256/checksum tokens.
            if pdef.category == "enc.base64_blob":
                snippet_lower = snippet.lower()
                if "sha256" in snippet_lower or "sha-256" in snippet_lower or "checksum" in snippet_lower:
                    continue

            findings.append(
                Finding(
                    category=pdef.category,
                    severity=pdef.severity,
                    file=rel_path,
                    line=line_no,
                    snippet=snippet,
                    description=pdef.description,
                )
            )

    # Non-whitelisted URL check (separate pass — needs URL context)
    if ext in _CODE_EXTS:
        _scan_urls(rel_path, content, lines, findings)

    return findings


_URL_RE = re.compile(r"https?://[^\s\"'`<>]+", re.IGNORECASE)


def _scan_urls(rel_path: str, content: str, lines: list[str], findings: list[Finding]) -> None:
    """Flag https:// URLs not in the whitelist as MEDIUM net.non_whitelisted_url."""
    for m in _URL_RE.finditer(content):
        url = m.group(0).rstrip(".,;)'\"")
        if _is_whitelisted_url(url):
            continue
        # Skip localhost / private RFC1918
        host = url.split("://", 1)[-1].split("/")[0].split(":")[0].lower()
        if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
            continue
        line_no = content[: m.start()].count("\n") + 1
        snippet = lines[line_no - 1].strip()[:120] if line_no <= len(lines) else ""
        findings.append(
            Finding(
                category="net.non_whitelisted_url",
                severity=MEDIUM,
                file=rel_path,
                line=line_no,
                snippet=snippet,
                description=f"Non-whitelisted external URL: {url[:80]}",
            )
        )


# ---------------------------------------------------------------------------
# Verdict computation (ADR Decision 6)
# ---------------------------------------------------------------------------


def _compute_verdict(findings: list[Finding]) -> str:
    """
    Verdict matrix (ADR Decision 6):
      - Any CRITICAL  → BLOCK
      - ≥3 HIGH       → BLOCK
      - 1-2 HIGH      → WARN
      - MEDIUM only   → WARN
      - LOW/none      → APPROVE
    """
    criticals = [f for f in findings if f.severity == CRITICAL]
    highs = [f for f in findings if f.severity == HIGH]
    mediums = [f for f in findings if f.severity == MEDIUM]

    if criticals:
        return "BLOCK"
    if len(highs) >= 3:
        return "BLOCK"
    if highs:
        return "WARN"
    if mediums:
        return "WARN"
    return "APPROVE"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Files/dirs to always skip during scan
_SKIP_DIRS = frozenset(
    {
        ".git", "__pycache__", "node_modules", ".venv", "venv",
        "dist", "build", ".cache",
    }
)
# Max file size to scan (bytes) — skip binary blobs > 512 KB
_MAX_FILE_BYTES = 512 * 1024


def scan(staged_path: Path, manifest: dict) -> ScanVerdict:
    """Scan a staged plugin directory for security issues.

    Args:
        staged_path: Absolute path to the extracted staging directory.
        manifest:    Dict from ``preview()["manifest"]`` (PluginManifest.model_dump()).

    Returns:
        ScanVerdict with verdict, findings, and metadata.
    """
    if not staged_path.is_dir():
        logger.error("scan() called with non-directory: %s", staged_path)
        return ScanVerdict(
            verdict="BLOCK",
            findings=[
                Finding(
                    category="internal.bad_path",
                    severity=CRITICAL,
                    file="",
                    line=0,
                    snippet="",
                    description=f"staged_path does not exist or is not a directory: {staged_path}",
                )
            ],
        )

    all_findings: list[Finding] = []
    scanned = 0

    for fpath in staged_path.rglob("*"):
        if not fpath.is_file():
            continue

        # Skip excluded directories
        parts = fpath.relative_to(staged_path).parts
        if any(part in _SKIP_DIRS for part in parts):
            continue

        # Extension filter — only scan text/code files
        ext = fpath.suffix.lower()
        if ext not in _CODE_EXTS and ext not in ("",):
            continue

        # Size guard
        try:
            size = fpath.stat().st_size
        except OSError:
            continue
        if size > _MAX_FILE_BYTES:
            logger.debug("Skipping large file: %s (%d bytes)", fpath, size)
            continue

        # Read with fallback encoding
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", fpath, exc)
            continue

        rel = str(fpath.relative_to(staged_path))
        file_findings = _scan_file(rel, content, _PATTERN_DEFS)
        all_findings.extend(file_findings)
        scanned += 1

    verdict = _compute_verdict(all_findings)

    return ScanVerdict(
        verdict=verdict,
        findings=all_findings,
        scanner_version=SCANNER_VERSION,
        scanned_files=scanned,
    )


def sha256_of_dir(path: Path) -> str:
    """Compute a stable SHA-256 over all file contents in a directory tree.

    Used to populate ``tarball_sha256`` when the raw tarball bytes are gone
    (already extracted). For fresh downloads, prefer capturing bytes during
    fetch_from_tarball to avoid a second pass.
    """
    h = hashlib.sha256()
    for fpath in sorted(path.rglob("*")):
        if fpath.is_file():
            try:
                h.update(fpath.read_bytes())
            except OSError:
                pass
    return h.hexdigest()
