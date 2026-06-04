"""Tests for secrets_scanner — false-positive and true-positive coverage.

Run with:
    uv run --python 3.11 python -m pytest dashboard/backend/brain_repo/tests/test_secrets_scanner.py -v
"""
import re
from pathlib import Path

import pytest

from dashboard.backend.brain_repo.secrets_scanner import PATTERNS, scan_files


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COMPILED: dict[str, re.Pattern] = {name: re.compile(pat) for name, pat in PATTERNS}


def _matches(pattern_name: str, line: str) -> bool:
    return bool(_COMPILED[pattern_name].search(line))


# ---------------------------------------------------------------------------
# GENERIC_SECRET — false positives: ALL_CAPS_SNAKE binding names (must NOT flag)
# ---------------------------------------------------------------------------

class TestGenericSecretAllCapsBindingFalsePositives:
    """Values that are ALL_CAPS_SNAKE identifiers (binding/env-var names, not secrets)."""

    def test_capi_token_secret_binding_name(self):
        # Cloudflare Worker wrangler.toml style — value is an env-var binding name
        assert not _matches(
            "GENERIC_SECRET",
            "meta_capi_token_secret: 'ANALYTICS_API_TOKEN_PROD'",
        )

    def test_webhook_token_secret_binding_name(self):
        assert not _matches(
            "GENERIC_SECRET",
            "crm_webhook_token_secret: 'CRM_WEBHOOK_TOKEN_PROD'",
        )

    def test_capi_token_secret_binding_name_staging(self):
        assert not _matches(
            "GENERIC_SECRET",
            "meta_capi_token_secret: 'ANALYTICS_API_TOKEN_STAGING'",
        )

    def test_unquoted_all_caps_binding(self):
        assert not _matches(
            "GENERIC_SECRET",
            "access_token: MY_ACCESS_TOKEN_BINDING_NAME",
        )


# ---------------------------------------------------------------------------
# GENERIC_SECRET — true positives: real mixed-case / entropy values (MUST flag)
# ---------------------------------------------------------------------------

class TestGenericSecretTruePositives:
    """Values with lowercase / symbols that are real credentials — must still be flagged."""

    def test_mixed_case_secret(self):
        # Real entropy value — has lowercase letters
        assert _matches(
            "GENERIC_SECRET",
            "secret: 'aB3xK9mRpQzLwVnYtUcJd8eH2fSoGiEl'",
        )

    def test_hex_value_on_secret_key(self):
        # key is literally 'secret', value is lowercase hex — must flag
        # (note: SECRET_KEY_BASE= doesn't match because 'secret' ≠ 'SECRET_KEY_BASE')
        assert _matches(
            "GENERIC_SECRET",
            "secret=8d0b1f4a2c9e7b3d5f6a8c0e2b4d6f8a0c2e4b6d",
        )

    def test_value_with_lowercase(self):
        # Value contains lowercase — must flag
        assert _matches(
            "GENERIC_SECRET",
            "api_key: 'abcXYZ123defGHI456jklMNO789pqr'",
        )

    def test_value_with_mixed_caps(self):
        # CamelCase value — has lowercase, must flag
        assert _matches(
            "GENERIC_SECRET",
            "access_token = 'MyRealTokenWithLowercaseChars123456789'",
        )


# ---------------------------------------------------------------------------
# GENERIC_PASSWORD — false positives (must NOT flag)
# ---------------------------------------------------------------------------

class TestGenericPasswordFalsePositives:
    """Lines that contain the word 'password' but NOT a hardcoded secret."""

    def test_environ_get_call(self):
        # Reported FP: dataforseo_client.py
        assert not _matches(
            "GENERIC_PASSWORD",
            'self.password = password or os.environ.get("DATAFORSEO_PASSWORD", "")',
        )

    def test_environ_get_variable_only(self):
        # Reported FP: evolution_go_mta.py
        assert not _matches(
            "GENERIC_PASSWORD",
            'password = os.environ.get("WEBSHARE_PROXY_PASSWORD")',
        )

    def test_function_default_none(self):
        assert not _matches(
            "GENERIC_PASSWORD",
            "def __init__(self, login=None, password=None):",
        )

    def test_dict_key_variable_value(self):
        assert not _matches("GENERIC_PASSWORD", '"password": password,')

    def test_argparse_help_string(self):
        assert not _matches(
            "GENERIC_PASSWORD",
            'p.add_argument("--password", help="Proxy password (manual mode)")',
        )

    def test_pure_alpha_variable_assignment(self):
        # 'password' as the right-hand side — all-alpha, no entropy indicator
        assert not _matches("GENERIC_PASSWORD", "self.password = password")

    def test_comment_line(self):
        assert not _matches("GENERIC_PASSWORD", "# password field stores hashed value")


# ---------------------------------------------------------------------------
# GENERIC_PASSWORD — true positives (MUST flag)
# ---------------------------------------------------------------------------

class TestGenericPasswordTruePositives:
    """Real hardcoded password assignments that must always be detected."""

    def test_postgres_password_yaml(self):
        # Real TP from [C]docker-stack.yml
        assert _matches("GENERIC_PASSWORD", "      - 'POSTGRES_PASSWORD=aB3xK9&zQmP7'")

    def test_smtp_password_yaml(self):
        # Real TP from [C]docker-stack.yml — long hex token
        assert _matches(
            "GENERIC_PASSWORD",
            "      - SMTP_PASSWORD=fake-smtp-pass-aB3xK9zQmP7sT5vW",
        )

    def test_db_password_yaml(self):
        assert _matches("GENERIC_PASSWORD", "      - DB_PASSWORD=aB3xK9&zQmP7")

    def test_bare_password_with_symbols(self):
        assert _matches("GENERIC_PASSWORD", "password: MyStr0ngP@ss123")

    def test_quoted_password_value(self):
        assert _matches("GENERIC_PASSWORD", "password: 'realpass123abc'")

    def test_env_assignment_no_prefix(self):
        assert _matches("GENERIC_PASSWORD", "PASSWORD=ActualSecret123")

    def test_postgres_password_in_docker_compose(self):
        assert _matches("GENERIC_PASSWORD", "- PASSWORD_POSTGRES=strongP@ss456")

    def test_guia_instalacao_redis(self):
        # Real TP from [C]guia-instalacao-evo-crm.md
        assert _matches(
            "GENERIC_PASSWORD",
            "REDIS_PASSWORD=SenhaRedis123!",
        )


# ---------------------------------------------------------------------------
# JWT_TOKEN — false positives (must NOT flag)
# ---------------------------------------------------------------------------

class TestJwtTokenFalsePositives:
    """Non-JWT strings that start with 'ey' embedded in larger words or filenames."""

    def test_image_filename_with_ey_substring(self):
        # Reported FP: analytics_diagnostic.json — image asset name
        assert not _matches(
            "JWT_TOKEN",
            '"name": "Wagner Ziegelmeyer-79_1.9108.jpg"',
        )

    def test_email_with_ey_prefix_domain(self):
        # Reported FP: agendor-ALL-deals.json — email address
        assert not _matches(
            "JWT_TOKEN",
            '"email": "eduardo@halleyshair.com.br"',
        )

    def test_another_email_ey_prefix(self):
        assert not _matches("JWT_TOKEN", '"email": "eyne@someplace.com.br"')

    def test_short_ey_base64_snippet(self):
        # Three-segment but each segment too short
        assert not _matches("JWT_TOKEN", "eywo.text.volume")

    def test_ey_inside_word(self):
        assert not _matches("JWT_TOKEN", "halleyshair.com.br")


# ---------------------------------------------------------------------------
# JWT_TOKEN — true positives (MUST flag)
# ---------------------------------------------------------------------------

class TestJwtTokenTruePositives:

    def test_standard_jwt(self):
        # HS256 JWT with real segment lengths
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
            ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ"
            ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        assert _matches("JWT_TOKEN", f"Authorization: Bearer {jwt}")

    def test_jwt_at_start_of_value(self):
        jwt = (
            "eyJhbGciOiJIUzI1NiJ9"
            ".eyJpZCI6MTIzLCJlbWFpbCI6InRlc3RAZXhhbXBsZS5jb20ifQ"
            ".abc123def456ghi789jkl012mno345pqrstu678vwx"
        )
        assert _matches("JWT_TOKEN", f"token: {jwt}")


# ---------------------------------------------------------------------------
# FERNET_KEY — false positives (must NOT flag)
# ---------------------------------------------------------------------------

class TestFernetKeyFalsePositives:
    """Base64 tokens that match the 43+= length but are NOT standalone Fernet keys."""

    def test_recaptcha_sxtoken_embedded_in_longer_base64(self):
        # Reported FP: recaptcha submit-test stub JSONs
        # The 43-char sequence 'bCbl1keLuCjIogQYpfImI8F52ozRMjeCCt34oj8RRrQ=' is
        # the TAIL of a longer encrypted blob ending in '=='
        line = '"sxtoken": "U2FsdGVkX19PgbqpuD7W4+U58R5p74bCbl1keLuCjIogQYpfImI8F52ozRMjeCCt34oj8RRrQ=="'
        assert not _matches("FERNET_KEY", line)

    def test_standard_base64_substring(self):
        # A 43-char base64 sequence followed by = that is part of a longer value
        # e.g. padding == at end of longer blob
        line = '"value": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqr3456789=="'
        assert not _matches("FERNET_KEY", line)

    def test_base64_data_uri(self):
        # 43-char sequence inside a data:image/... URI (preceded by base64 chars)
        line = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgABCDEFGHIJKLMNOPQRSTUVWXY43="
        assert not _matches("FERNET_KEY", line)


# ---------------------------------------------------------------------------
# FERNET_KEY — true positives (MUST flag)
# ---------------------------------------------------------------------------

class TestFernetKeyTruePositives:

    def test_encryption_key_yaml_assignment(self):
        # Real TP from [C]docker-stack.yml
        assert _matches(
            "FERNET_KEY",
            "      - ENCRYPTION_KEY=fAKEfernetKey000aB3xK9zQmP2sT5vW8yA1cD4fG7h=",
        )

    def test_fernet_key_env_var(self):
        assert _matches("FERNET_KEY", "FERNET_KEY=abcdefghijklmnopqrstuvwxyzABCDEF12345678901=")

    def test_quoted_fernet_key(self):
        # A valid Fernet key is exactly 44 chars (43 base64url chars + '=')
        fernet_key = "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890abcdefg="
        assert len(fernet_key) == 44, f"fixture key must be 44 chars, got {len(fernet_key)}"
        assert _matches("FERNET_KEY", f"encryption_key = '{fernet_key}'")


# ---------------------------------------------------------------------------
# AWS_SECRET_KEY — false positives (must NOT flag)
# ---------------------------------------------------------------------------

class TestAwsSecretKeyFalsePositives:
    """AWS-like patterns inside base64 image data (data URIs in lighthouse JSON)."""

    def test_base64_image_data_awsu_substring(self):
        # Reported FP: lh_int_posfix_2026-05-18.json line 110
        # 'AwSu' appears inside a base64-encoded image blob (preceded by 't')
        line = "e/g8fy0QxOekJxcd2SlUrFod9cJCrKlikpWoA7tidoq6Fgd6GtAwSuI2DiivR654fCAw5v8ACExsvLzHUfT7Mlx2M8P+zXnGnCiQpO7m7/Dd7f"
        assert not _matches("AWS_SECRET_KEY", line)

    def test_base64_image_awsm_substring(self):
        # Reported FP: lh_nac_posfix_2026-05-18.json
        # 'AWSm' inside a long base64 image (preceded by base64 chars)
        line = "xR9F4VKj8tZpAWSm4TrQlNs6dLUvYbWGcPqJeHiOzXwCfDkMnBaRsToYpWqAzGoon3Fd8Ke"
        assert not _matches("AWS_SECRET_KEY", line)


# ---------------------------------------------------------------------------
# AWS_SECRET_KEY — true positives (MUST flag)
# ---------------------------------------------------------------------------

class TestAwsSecretKeyTruePositives:

    def test_aws_secret_access_key_assignment(self):
        assert _matches(
            "AWS_SECRET_KEY",
            "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        )

    def test_aws_secret_env_var(self):
        assert _matches(
            "AWS_SECRET_KEY",
            "AWS_SECRET=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        )

    def test_aws_secret_yaml(self):
        assert _matches(
            "AWS_SECRET_KEY",
            "  aws_key: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        )


# ---------------------------------------------------------------------------
# Integration: scan_files against fixture directories
# ---------------------------------------------------------------------------

@pytest.fixture
def fp_fixture_dir(tmp_path: Path) -> Path:
    """Create temp files that represent the known FP classes."""
    (tmp_path / "dataforseo_client.py").write_text(
        "def __init__(self, login=None, password=None):\n"
        '    self.password = password or os.environ.get("DATAFORSEO_PASSWORD", "")\n',
        encoding="utf-8",
    )
    (tmp_path / "evolution_go_mta.py").write_text(
        'password = os.environ.get("WEBSHARE_PROXY_PASSWORD")\n'
        '"password": password,\n',
        encoding="utf-8",
    )
    (tmp_path / "analytics_diagnostic.json").write_text(
        '"name": "Wagner Ziegelmeyer-79_1.9108.jpg"\n'
        '"email": "eduardo@halleyshair.com.br"\n',
        encoding="utf-8",
    )
    sxtoken_value = (
        "U2FsdGVkX19PgbqpuD7W4+U58R5p74bCbl1keLuCjIogQYpfImI8F52ozRMjeCCt34oj8RRrQ=="
    )
    (tmp_path / "submit-tests-stub.json").write_text(
        f'{{"sxtoken": "{sxtoken_value}"}}\n',
        encoding="utf-8",
    )
    # AWS FP: 'AwSu' inside base64 image blob (preceded by 't', a base64 char)
    (tmp_path / "lighthouse.json").write_text(
        "e/g8fy0QxOekJxcd2SlUrFod9cJCrKlikpWoA7tidoq6Fgd6GtAwSuI2DiivR654fCAw5v8ACExsvLzHUfT7Mlx2M8P+zXnGnCiQpO7m7/Dd7f\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def tp_fixture_dir(tmp_path: Path) -> Path:
    """Create temp files that represent known true positives (real secrets)."""
    (tmp_path / "docker-stack.yml").write_text(
        "      - 'POSTGRES_PASSWORD=aB3xK9&zQmP7'\n"
        "      - ENCRYPTION_KEY=fAKEfernetKey000aB3xK9zQmP2sT5vW8yA1cD4fG7h=\n"
        "      - postgres://user:aB3xK9&zQmP7@db:5432/appdb\n"
        "      - SMTP_PASSWORD=fake-smtp-pass-aB3xK9zQmP7sT5vW\n",
        encoding="utf-8",
    )
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    (tmp_path / "config.yml").write_text(
        f"jwt_secret: {jwt}\n"
        "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def cloudflare_bindings_fixture_dir(tmp_path: Path) -> Path:
    """Cloudflare Worker config with ALL_CAPS_SNAKE binding references — must scan clean."""
    (tmp_path / "wrangler-config.md").write_text(
        "# Cloudflare Worker config (binding-name references)\n"
        "meta_capi_token_secret: 'ANALYTICS_API_TOKEN_PROD'\n"
        "crm_webhook_token_secret: 'CRM_WEBHOOK_TOKEN_PROD'\n"
        "meta_capi_token_secret: 'ANALYTICS_API_TOKEN_STAGING'\n",
        encoding="utf-8",
    )
    return tmp_path


def test_cloudflare_bindings_scan_clean(cloudflare_bindings_fixture_dir: Path) -> None:
    """ALL_CAPS_SNAKE binding names in Cloudflare Worker configs must produce zero findings."""
    files = list(cloudflare_bindings_fixture_dir.rglob("*"))
    findings = scan_files(files)
    assert findings == [], (
        f"Expected 0 findings in Cloudflare bindings fixture, got {len(findings)}:\n"
        + "\n".join(f"  {f['file']}:{f['line']} [{f['pattern']}] {f['snippet']}" for f in findings)
    )


def test_fp_fixtures_scan_clean(fp_fixture_dir: Path) -> None:
    """All known false-positive files must produce zero findings."""
    files = list(fp_fixture_dir.rglob("*"))
    findings = scan_files(files)
    assert findings == [], (
        f"Expected 0 findings in FP fixtures, got {len(findings)}:\n"
        + "\n".join(f"  {f['file']}:{f['line']} [{f['pattern']}] {f['snippet']}" for f in findings)
    )


def test_tp_fixtures_still_flagged(tp_fixture_dir: Path) -> None:
    """True-positive fixture files must produce at least one finding each."""
    files = list(tp_fixture_dir.rglob("*"))
    findings = scan_files(files)
    flagged_files = {Path(f["file"]).name for f in findings}

    assert "docker-stack.yml" in flagged_files, (
        "docker-stack.yml (GENERIC_PASSWORD + FERNET_KEY + DATABASE_URL) must be flagged"
    )
    assert "config.yml" in flagged_files, (
        "config.yml (JWT_TOKEN + AWS_SECRET_KEY) must be flagged"
    )


# ---------------------------------------------------------------------------
# Round 3 — var-ref (${VAR}) and documentation-placeholder false positives.
# After redacting real secrets out to .env, docs reference ${VAR}; install
# guides carry placeholders like 'SUA_SENHA'. Neither is a credential.
# ---------------------------------------------------------------------------

_VARREF_FP_LINES = [
    "url = postgresql://postgres:${APP_POSTGRES_PASSWORD_URLENC}@h:5432/db",
    "      - 'POSTGRES_PASSWORD=${APP_POSTGRES_PASSWORD}'",
    "password=${SOME_VAR}",
    "secret: ${BOT_RUNTIME_SECRET}",
]

_PLACEHOLDER_FP_LINES = [
    "POSTGRES_PASSWORD='SUA_SENHA_POSTGRES'",
    "REDIS_PASSWORD=SUA_SENHA_REDIS",
    "PROCESSOR_POSTGRES_CONNECTION_STRING=postgresql://postgres:SUA_SENHA@host:5432/db",
    "exemplo: 'POSTGRES_PASSWORD=senha&especial'",
    "password=CHANGEME_NOW",
    "api_key=YOUR_API_KEY_HERE",
]

# Correctly-shaped real secrets — must STAY flagged (no weakening).
_REAL_TP_LINES = [
    "url=postgresql://postgres:aB3xK9zQmP@host/db",
    '"password=L1l9rXyWgMb7"',
    "ENCRYPTION_KEY=aB3xK9zQmP2sT5vW8yA1cD4fG7hJ0kL3nP6rU9wX2Zq=",
    "secret = aB3xK9zQmP2sT5vW8yA1cD4f",
]


def _scan_line(tmp_path: Path, line: str) -> list[dict]:
    f = tmp_path / "x.yml"
    f.write_text(line, encoding="utf-8")
    return scan_files([f])


@pytest.mark.parametrize("line", _VARREF_FP_LINES)
def test_varref_is_not_a_secret(tmp_path: Path, line: str) -> None:
    assert _scan_line(tmp_path, line) == [], f"${{VAR}} reference flagged as secret: {line!r}"


@pytest.mark.parametrize("line", _PLACEHOLDER_FP_LINES)
def test_placeholder_is_not_a_secret(tmp_path: Path, line: str) -> None:
    assert _scan_line(tmp_path, line) == [], f"Placeholder flagged as secret: {line!r}"


@pytest.mark.parametrize("line", _REAL_TP_LINES)
def test_real_secret_still_flagged(tmp_path: Path, line: str) -> None:
    assert len(_scan_line(tmp_path, line)) > 0, f"Real secret no longer detected: {line!r}"
