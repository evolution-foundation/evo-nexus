"""Brain Repo — Secret scanner for pre-commit security checks."""

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

# (name, pattern) — minimum 20 patterns
PATTERNS: list[tuple[str, str]] = [
    ("AWS_ACCESS_KEY", r"AKIA[0-9A-Z]{16}"),
    # Negative lookbehind prevents matching 'aws' embedded inside base64 image data.
    ("AWS_SECRET_KEY", r"(?i)(?<![A-Za-z0-9+/])aws.{0,25}[0-9a-zA-Z/+]{40}"),
    ("GITHUB_TOKEN", r"gh[pousr]_[A-Za-z0-9_]{36,255}"),
    ("ANTHROPIC_API_KEY", r"sk-ant-api[0-9]{2}-[A-Za-z0-9_\-]{93,}AA"),
    ("OPENAI_API_KEY", r"sk-[a-zA-Z0-9]{20,}T3BlbkFJ[a-zA-Z0-9]{20,}"),
    ("OPENAI_PROJECT_KEY", r"sk-proj-[A-Za-z0-9_\-]{40,}"),
    # Positive lookahead on the value (case-sensitive via (?-i:...)):
    # Only match when the value contains at least one lowercase letter or entropy
    # character (+, /, =).  This excludes pure ALL_CAPS_UNDERSCORE identifiers such as
    # 'META_CAPI_TOKEN_RDC_EVORY' or 'BITRIX_WEBHOOK_TOKEN_RDC' — those are
    # binding/env-var NAMES used in Cloudflare Worker wrangler config and similar
    # tooling, not credential values.  Real secrets (base64/hex/Fernet/JWT) always
    # contain at least one lowercase letter or one of the base64 symbols '+', '/', '='.
    # (?-i:...) disables the outer case-insensitive flag for this lookahead only so
    # that [a-z] matches literal lowercase characters, not A-Z.
    ("GENERIC_SECRET", r"(?i)(secret|api_key|private_key|access_token|auth_token)\s*[=:]\s*[\"']?(?=(?-i:[A-Za-z0-9_\-]*[a-z+/=]))[A-Za-z0-9_\-]{20,}[\"']?"),
    # Negative lookbehind prevents matching 'ey' embedded inside a longer word
    # (e.g. 'halleyshair', 'Ziegelmeyer-79').  Minimum segment lengths exclude
    # image filenames (eyer…1.jpg) and short domain labels.
    ("JWT_TOKEN", r"(?<![A-Za-z0-9_\-])ey[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{20,}"),
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
    # Negative lookbehind requires the 44-char key NOT to be a substring embedded
    # inside a longer base64 string (e.g. recaptcha sxtoken, encrypted blobs).
    # Negative lookahead requires the trailing '=' to end the value.
    ("FERNET_KEY", r"(?<![A-Za-z0-9+/])[A-Za-z0-9_\-]{43}=(?![A-Za-z0-9_\-=])"),
    # Require the value to be either a quoted string literal OR a bare value
    # that contains at least one digit/symbol (entropy indicator).  This excludes:
    #   - 'password = os.environ.get(...)' (function call, no digit/symbol before paren)
    #   - 'password=None' / 'password=password' (pure-alpha variable names)
    #   - argument definitions: '--password' help strings
    # password[_suffix] form matches env-var names like POSTGRES_PASSWORD, SMTP_PASSWORD.
    ("GENERIC_PASSWORD", r"(?i)password[a-z_0-9]*\s*[=:]\s*(?:[\"'][A-Za-z0-9!@#$%^&*_+\-]{8,}[\"']|(?=[^\s\"']*[0-9!@#$%^&*_+\-][^\s\"']*)[A-Za-z0-9!@#$%^&*_+\-]{8,})"),
    # --- Provider-specific tokens with hard prefixes (near-zero FP rate) ---
    # GitLab Personal Access Token (glpat- prefix, introduced 2021)
    ("GITLAB_PAT", r"glpat-[0-9a-zA-Z\-_]{20,}"),
    # Supabase service-role key and secret key
    ("SUPABASE_SERVICE_KEY", r"sbp_[0-9a-f]{40}"),
    ("SUPABASE_SECRET_KEY", r"sb_secret_[A-Za-z0-9\-_]{20,}"),
    # Hugging Face access token (hf_ prefix, ~37 chars total)
    ("HUGGINGFACE_TOKEN", r"hf_[a-zA-Z0-9]{34,}"),
    # Replicate API token (r8_ prefix, ~41 chars total)
    ("REPLICATE_TOKEN", r"r8_[a-zA-Z0-9]{38,}"),
    # Groq API key (gsk_ prefix, ~52 chars total)
    ("GROQ_API_KEY", r"gsk_[a-zA-Z0-9]{48,}"),
    # npm access token (npm_ prefix, ~40 chars total)
    ("NPM_TOKEN", r"npm_[0-9a-zA-Z]{36,}"),
    # PyPI API token — real tokens are base64url and very long (~160 chars total).
    # Minimum of 100 suffix chars prevents FP on package names (e.g. pypi-setuptools-X.Y).
    ("PYPI_TOKEN", r"pypi-[A-Za-z0-9\-_]{100,}"),
    # Linear API key (lin_api_ prefix, ~48 chars total)
    ("LINEAR_API_KEY", r"lin_api_[a-zA-Z0-9]{40,}"),
    # Figma personal access token (figd_ prefix, ~44 chars total)
    ("FIGMA_TOKEN", r"figd_[0-9a-zA-Z\-_]{40,}"),
    # Meta/Facebook Graph API access token (EAA prefix).
    # Requires context keyword (access_token/page_token/user_token/graph_token or
    # 'Bearer') to avoid matching EAA substrings inside base64 image data (JPEG EXIF,
    # GTM binary blobs, Lighthouse audit JSONs) and Meta creative object IDs — all of
    # which appear in workspace/_scripts/**/*.json without any token context keyword.
    # Minimum 60 alphanum chars after 'EAA' (real tokens are 100-250+ chars total).
    ("META_ACCESS_TOKEN",
     r"(?i)"
     r"(?:"
     r"(?:access_token|graph_token|page_token|user_token)\s*[=:]\s*EAA[A-Za-z0-9]{60,}"
     r"|bearer\s+EAA[A-Za-z0-9]{60,}"
     r")"),
    # Google OAuth 2.0 client secret (GOCSPX- prefix, ~35 chars total).
    # Very distinctive prefix — near-zero FP rate in practice.  Minimum 26 chars
    # after the prefix matches the shortest real secret observed in this corpus
    # (GOCSPX-lH1MAHb_xQ7mdiSLKEyNdvZVjyz = 27 chars after prefix).
    ("GOOGLE_OAUTH_CLIENT_SECRET", r"GOCSPX-[A-Za-z0-9_\-]{26,}"),
    # Telegram Bot Token: <bot_id>:<secret>
    # bot_id = 8-10 digits; secret = 34+ alphanumeric chars (real tokens: 34-36 chars)
    ("TELEGRAM_BOT_TOKEN", r"[0-9]{8,10}:[A-Za-z0-9_\-]{34,}"),
    # Notion integration token (ntn_ new format, ~44 chars total)
    # NOTE: Notion legacy format (secret_XXXXX{43}) was evaluated and EXCLUDED:
    # 'secret_' is too generic — matches ALL_CAPS binding names of exactly 43 chars.
    ("NOTION_TOKEN", r"ntn_[0-9a-zA-Z]{40,}"),
]

_CHECKED_EXTENSIONS = {
    ".py", ".env", ".yaml", ".yml", ".json", ".txt", ".md",
    ".sh", ".conf", ".ini", ".cfg",
}

_AUTO_EXCLUDE_PARTS = {".git", "__pycache__", ".venv", "node_modules"}
_AUTO_EXCLUDE_SUFFIXES = {".pyc"}


# A value that is purely an env-var interpolation (e.g. ${DB_PASSWORD}) is a
# REFERENCE, never a credential — common after redacting secrets out to .env.
_VAR_REF_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")

# Documentation placeholders that look password-shaped but carry no secret.
# Each token is specific enough (8+ chars / unambiguous marker) that a real
# base64/hex/Fernet secret won't contain it by chance. Keep this list tight —
# every entry must be a phrase no genuine credential would embed.
# «...»-wrapped values (e.g. postgresql://postgres:«DB_PASS»@host) are the
# guillemet-style placeholder convention used in some MTA docs/configs.
# REDIGIDO/REDACTED/XXXX mark a value that was ALREADY masked before being
# committed — the masked stand-in is not a live secret.
_PLACEHOLDER_RE = re.compile(
    r"(?:SUA_SENHA|SUA_SEGREDO|SEU_SENHA|SEU_SEGREDO|SEU_TOKEN|"
    r"YOUR_|YOUR-|CHANGE[_-]?ME|senha&especial|"
    r"«[^»]*»|REDIGIDO|REDACTED|XXXX)",
    re.IGNORECASE,
)

# GENERIC_PASSWORD's quoted-value branch has no entropy check (any 8+ char
# quoted string matches) — so an ALL_CAPS_SNAKE fixture/placeholder value like
# password="SENHA_SECRETA" or password="SUPER_SECRET_PASSWORD" is flagged even
# though it's obviously not a live credential. Mirrors the same reasoning
# already applied to GENERIC_SECRET's ALL_CAPS binding-name exclusion: a value
# that is itself fully uppercase/digits/underscore and names its own kind
# (SECRET/SENHA/PASSWORD/...) is a placeholder, not entropy.
_FIXTURE_VALUE_RE = re.compile(
    r"[\"'][A-Z0-9_]*(?:SECRET|SENHA|PASSWORD|DUMMY|FAKE|EXAMPLE|CHANGEME)[A-Z0-9_]*[\"']"
)

# GENERIC_PASSWORD's bare (unquoted) branch treats a hyphen as an "entropy"
# symbol, which lets English/Portuguese prose like "Passwords = REST-only"
# (from docs describing WordPress Application Passwords) match — REST-only
# has a hyphen but no digit. Every real hardcoded password in this corpus
# contains at least one digit, so a bare value with letters/symbols but zero
# digits is prose, not a secret.
_PROSE_NO_DIGIT_PASSWORD_RE = re.compile(
    r"(?i)^password[a-z_0-9]*\s*[=:]\s*[A-Za-z][A-Za-z!@#$%^&*_+\-]{6,}$"
)


def _is_false_positive(match_text: str) -> bool:
    """True when a regex match is a known non-secret (var ref or placeholder).

    Applied after every pattern match so the FP rules live in one place instead
    of being smeared across every individual regex. Safe by construction: real
    secrets are neither ``${VAR}`` interpolations nor documentation placeholders.
    """
    if _VAR_REF_RE.search(match_text):
        return True
    if _PLACEHOLDER_RE.search(match_text):
        return True
    if _FIXTURE_VALUE_RE.search(match_text):
        return True
    if _PROSE_NO_DIGIT_PASSWORD_RE.match(match_text):
        return True
    return False


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
                    if _is_false_positive(m.group(0)):
                        continue
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
