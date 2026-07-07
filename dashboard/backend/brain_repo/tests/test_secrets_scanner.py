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

    def test_meta_capi_token_secret_evory(self):
        # Cloudflare Worker wrangler.toml style — value is an env-var binding name
        assert not _matches(
            "GENERIC_SECRET",
            "meta_capi_token_secret: 'META_CAPI_TOKEN_RDC_EVORY'",
        )

    def test_crm_webhook_token_secret(self):
        assert not _matches(
            "GENERIC_SECRET",
            "crm_webhook_token_secret: 'BITRIX_WEBHOOK_TOKEN_RDC'",
        )

    def test_meta_capi_token_secret_quadria(self):
        assert not _matches(
            "GENERIC_SECRET",
            "meta_capi_token_secret: 'META_CAPI_TOKEN_RDC_QUADRIA_AQ'",
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
        # Real TP from [C]stack11-automazap.yml
        assert _matches("GENERIC_PASSWORD", "      - 'POSTGRES_PASSWORD=aB3xK9&zQmP7'")

    def test_smtp_password_yaml(self):
        # Real TP from [C]stack11-automazap.yml — long hex token
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
        # Reported FP: erbe_google_diagnostico.json — image asset name
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
        # Reported FP: erbe recaptcha submit-tests stub JSONs
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
        # Real TP from [C]stack11-automazap.yml
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
    (tmp_path / "erbe_google_diagnostico.json").write_text(
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
    (tmp_path / "stack11-automazap.yml").write_text(
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
    (tmp_path / "wrangler-evory.md").write_text(
        "# Cloudflare Worker config (binding-name references)\n"
        "meta_capi_token_secret: 'META_CAPI_TOKEN_RDC_EVORY'\n"
        "crm_webhook_token_secret: 'BITRIX_WEBHOOK_TOKEN_RDC'\n"
        "meta_capi_token_secret: 'META_CAPI_TOKEN_RDC_QUADRIA_AQ'\n",
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

    assert "stack11-automazap.yml" in flagged_files, (
        "stack11-automazap.yml (GENERIC_PASSWORD + FERNET_KEY + DATABASE_URL) must be flagged"
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
    "url = postgresql://postgres:${EVOCRM_STACK_POSTGRES_PASSWORD_URLENC}@h:5432/db",
    "      - 'POSTGRES_PASSWORD=${EVOCRM_STACK_POSTGRES_PASSWORD}'",
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


# ---------------------------------------------------------------------------
# Provider-specific tokens — true positives (one per new pattern).
# ---------------------------------------------------------------------------

class TestProviderSpecificTokensTruePositives:
    """Each new provider pattern must detect a synthetic but correctly-shaped token."""

    def test_gitlab_pat(self):
        assert _matches("GITLAB_PAT", "GITLAB_TOKEN=glpat-abcdef1234567890abcd12")

    def test_supabase_service_key(self):
        # sbp_ + exactly 40 lowercase hex chars
        key = "sbp_" + "a1b2c3d4" * 5  # 40 chars
        assert _matches("SUPABASE_SERVICE_KEY", f"SUPABASE_KEY={key}")

    def test_supabase_secret_key(self):
        assert _matches("SUPABASE_SECRET_KEY", "sb_secret_EXAMPLE0000EXAMPLE0000EXAMPLE0000")

    def test_huggingface_token(self):
        token = "hf_" + "aAbBcCdDeEfF0011223344556677889900ab"  # 34 alphanum chars
        assert len(token) >= 37
        assert _matches("HUGGINGFACE_TOKEN", f"HF_TOKEN={token}")

    def test_replicate_token(self):
        token = "r8_" + "aAbBcCdDeEfF00112233445566778899001122334455"  # 43 chars
        assert _matches("REPLICATE_TOKEN", f"REPLICATE_API_TOKEN={token}")

    def test_groq_api_key(self):
        token = "gsk_" + "a" * 48
        assert _matches("GROQ_API_KEY", f"GROQ_API_KEY={token}")

    def test_npm_token(self):
        token = "npm_" + "aAbBcCdDeEfF00112233445566778899001122"  # 38 chars
        assert _matches("NPM_TOKEN", f"NPM_TOKEN={token}")

    def test_pypi_token(self):
        # Real PyPI tokens are ~160 chars total; use 105-char suffix to stay above threshold
        suffix = "AgEIcHlwaS5vcmcCBAAAAA" * 4 + "AgEIcHlw"  # 96 chars -> use longer
        suffix2 = "AgEIcHlwaS5vcmcCBAAAAA" * 5  # 110 chars
        token = f"pypi-{suffix2}"
        assert _matches("PYPI_TOKEN", f"PYPI_API_TOKEN={token}")

    def test_linear_api_key(self):
        token = "lin_api_" + "aAbBcCdDeEfF001122334455667788990011223344"  # 42 chars
        assert _matches("LINEAR_API_KEY", f"LINEAR_API_KEY={token}")

    def test_figma_token(self):
        token = "figd_" + "aAbBcCdDeEfF001122334455667788990011223344"  # 42 chars
        assert _matches("FIGMA_TOKEN", f"FIGMA_TOKEN={token}")

    def test_telegram_bot_token(self):
        # Real Telegram example: 110201543:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw (34 chars after ':')
        assert _matches("TELEGRAM_BOT_TOKEN", "bot_token = 110201543:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw")

    def test_notion_token(self):
        token = "ntn_" + "a" * 40
        assert _matches("NOTION_TOKEN", f"NOTION_API_KEY={token}")


# ---------------------------------------------------------------------------
# Provider-specific tokens — false positives (isca strings that must NOT flag).
# ---------------------------------------------------------------------------

class TestProviderSpecificTokensFalsePositives:
    """Near-miss strings that must NOT trigger the new patterns."""

    def test_gitlab_pat_too_short(self):
        # Under minimum 20 chars after prefix
        assert not _matches("GITLAB_PAT", "glpat-tooshort")

    def test_supabase_service_key_too_short(self):
        # sbp_ but only 30 hex chars (needs 40)
        assert not _matches("SUPABASE_SERVICE_KEY", "sbp_" + "a" * 30)

    def test_supabase_service_key_non_hex(self):
        # sbp_ + uppercase letter → not hex → no match
        assert not _matches("SUPABASE_SERVICE_KEY", "sbp_" + "G" * 40)

    def test_huggingface_token_too_short(self):
        assert not _matches("HUGGINGFACE_TOKEN", "hf_" + "a" * 10)

    def test_groq_placeholder_not_flagged(self):
        # GROQ_API_KEY=gsk_placeholder is 11 chars after gsk_ → below 48 minimum
        assert not _matches("GROQ_API_KEY", "GROQ_API_KEY=gsk_placeholder")

    def test_npm_too_short(self):
        assert not _matches("NPM_TOKEN", "npm_tooshort")

    def test_pypi_package_name_not_flagged(self):
        # A package reference like 'pypi-setuptools-74.0.0' has dots (not in charset)
        # and is far below 100 chars — must not flag.
        assert not _matches("PYPI_TOKEN", "pypi-setuptools-74.0.0-py3-none-any.whl")

    def test_pypi_medium_string_not_flagged(self):
        # 50-char suffix — realistic package name variant, below 100-char threshold
        assert not _matches("PYPI_TOKEN", "pypi-" + "a" * 50)

    def test_linear_api_key_too_short(self):
        assert not _matches("LINEAR_API_KEY", "lin_api_short")

    def test_figma_too_short(self):
        assert not _matches("FIGMA_TOKEN", "figd_" + "a" * 10)

    def test_telegram_phone_number_not_flagged(self):
        # Phone number in JSON — no ':' followed by long alphanum
        assert not _matches("TELEGRAM_BOT_TOKEN", '"phone": "11987654321"')

    def test_telegram_timestamp_not_flagged(self):
        # Timestamp with message — ':' is followed by a space, not alphanum
        assert not _matches("TELEGRAM_BOT_TOKEN", "2024-01-01 10:00:00: starting service")

    def test_notion_too_short(self):
        assert not _matches("NOTION_TOKEN", "ntn_" + "a" * 10)


# ---------------------------------------------------------------------------
# META_ACCESS_TOKEN — true positives (must flag)
# ---------------------------------------------------------------------------

class TestMetaAccessTokenTruePositives:
    """Meta/Facebook Graph API access tokens must be detected when context keyword present."""

    def test_access_token_url_param(self):
        # Real pattern: Graph API URL query param
        token = "EAAQEwuUtyr0BQ0OXsMIkj2FtzvELZAUyIgiA6LetsqCtROp8Ir6eUb34tVhbbaR86OqZ"
        assert _matches("META_ACCESS_TOKEN", f"access_token={token}")

    def test_access_token_yaml_colon(self):
        # YAML-style assignment
        token = "EAAQEwuUtyr0BQ0OXsMIkj2FtzvELZAUyIgiA6LetsqCtROp8Ir6eUb34tVhbbaR86OqZ"
        assert _matches("META_ACCESS_TOKEN", f"access_token: {token}")

    def test_bearer_header(self):
        # HTTP Authorization header
        token = "EAAQEwuUtyr0BQ0OXsMIkj2FtzvELZAUyIgiA6LetsqCtROp8Ir6eUb34tVhbbaR86OqZ"
        assert _matches("META_ACCESS_TOKEN", f"Authorization: Bearer {token}")

    def test_page_token(self):
        # page_token parameter
        token = "EAAQEwuUtyr0BQ0OXsMIkj2FtzvELZAUyIgiA6LetsqCtROp8Ir6eUb34tVhbbaR86OqZ"
        assert _matches("META_ACCESS_TOKEN", f"page_token={token}")


# ---------------------------------------------------------------------------
# META_ACCESS_TOKEN — false positives (must NOT flag)
# ---------------------------------------------------------------------------

class TestMetaAccessTokenFalsePositives:
    """Strings with EAA prefix that must NOT trigger META_ACCESS_TOKEN."""

    def test_base64_jpeg_no_context(self):
        # EAA inside base64 JPEG EXIF data — no token context keyword
        # (real FP from workspace/_scripts/proshock/lh_*.json)
        line = "EAABAwMCAwIHCwYPAAAAAAAAAQIDBAURBgcSITETQQgUGCJEUdIjVFZhcZGSk6TR0xYXMjNCsRUkJTRDRlJiZHKBlKLBwv"
        assert not _matches("META_ACCESS_TOKEN", line)

    def test_meta_creative_object_id_no_context(self):
        # Short creative object ID embedded in JSON — no token context
        # (real FP from workspace/marketing/_state/rdc-ctwa-investigation/ads-*.json)
        line = 'klGgY4ScEAAGzXegQmBOnh--yLeFhaBxP1-1naN7dB","image_hash":"f9f2"'
        assert not _matches("META_ACCESS_TOKEN", line)

    def test_all_caps_binding_access_token(self):
        # ALL_CAPS binding name as value — not a real token
        assert not _matches("META_ACCESS_TOKEN", "access_token: META_CAPI_TOKEN_RDC_EVORY")

    def test_var_ref_access_token(self):
        # ${VAR} interpolation — not a credential
        assert not _matches("META_ACCESS_TOKEN", "access_token=${META_ACCESS_TOKEN}")

    def test_fake_test_token_no_context(self):
        # Fake token in int-meta-ads test file — lacks access_token= context
        line = 'FAKE_TOKEN = "EAAQtestTOKENfake1234567890ABCDEFabcdef0123456789XYZ"'
        assert not _matches("META_ACCESS_TOKEN", line)

    def test_bearer_too_short(self):
        # Token shorter than 60 chars after EAA — must not flag
        assert not _matches("META_ACCESS_TOKEN", "bearer EAAshort1234567890abcd")

    def test_icc_profile_blob_no_context(self):
        # ICC colour profile binary blob — all-zeros, no token context
        line = "EAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQ"
        assert not _matches("META_ACCESS_TOKEN", line)


# ---------------------------------------------------------------------------
# GOOGLE_OAUTH_CLIENT_SECRET — true positives (must flag)
# ---------------------------------------------------------------------------

class TestGoogleOauthClientSecretTruePositives:
    """Google OAuth 2.0 client secrets (GOCSPX- prefix) must be detected."""

    def test_standard_secret(self):
        # Correctly shaped secret — 28 chars after prefix
        assert _matches("GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_CLIENT_SECRET=GOCSPX-EXAMPLEsyntheticVALUE1234567")

    def test_secret_in_yaml(self):
        # YAML assignment
        assert _matches("GOOGLE_OAUTH_CLIENT_SECRET", "client_secret: GOCSPX-abcdefghijklmnopqrstuvwxyz12")

    def test_secret_inline_doc(self):
        # Bare in a doc or memory note — must still be detected
        assert _matches("GOOGLE_OAUTH_CLIENT_SECRET", "correct value is GOCSPX-EXAMPLEsyntheticVALUE1234567")


# ---------------------------------------------------------------------------
# GOOGLE_OAUTH_CLIENT_SECRET — false positives (must NOT flag)
# ---------------------------------------------------------------------------

class TestGoogleOauthClientSecretFalsePositives:
    """Near-miss strings that must NOT trigger GOOGLE_OAUTH_CLIENT_SECRET."""

    def test_too_short_after_prefix(self):
        # Only 6 chars after GOCSPX- (truncated in docs)
        assert not _matches("GOOGLE_OAUTH_CLIENT_SECRET", "GOCSPX-tZSBC5")

    def test_single_char_after_prefix(self):
        assert not _matches("GOOGLE_OAUTH_CLIENT_SECRET", "GOCSPX-i")

    def test_placeholder_length(self):
        # 'GOCSPX-YOUR_SECRET_HERE' = 16 chars after prefix — under threshold
        assert not _matches("GOOGLE_OAUTH_CLIENT_SECRET", "GOCSPX-YOUR_SECRET_HERE")

    def test_wrong_separator(self):
        # GOCSPX_ (underscore, not hyphen) — not the real format
        assert not _matches("GOOGLE_OAUTH_CLIENT_SECRET", "GOCSPX_NOT_A_REAL_SECRET_ABCDEFGHIJ")


# ---------------------------------------------------------------------------
# Round 4 — 5 confirmed false positives that were silently deleting legitimate
# backup files from the Brain Repo (scan_files, not just the raw regex, since
# the fixes live in _is_false_positive which sits downstream of every regex).
# ---------------------------------------------------------------------------

_ROUND4_FP_LINES = [
    # 1. Guillemet-wrapped placeholder in a connection string.
    "POSTGRES_CONNECTION_STRING=postgresql://postgres:«DB_PASS»@pgvector:5432/evocrm",
    # 2/3. Prose about WordPress Application Passwords — "REST-only" has a
    # hyphen but no digit, so it isn't real password entropy.
    "não autentica com app-password (Application Passwords = REST-only; 404)",
    "Sign-off visual (Application Passwords = REST-only) é do revisor humano",
    # 4. ALL_CAPS_SNAKE fixture/placeholder values naming their own kind.
    'password="SENHA_SECRETA"',
    'password="SUPER_SECRET_PASSWORD"',
    # 5. Already-redacted token.
    "access_token=EAA_REDIGIDO_token_meta_audi_2026",
]


@pytest.mark.parametrize("line", _ROUND4_FP_LINES)
def test_round4_false_positives_scan_clean(tmp_path: Path, line: str) -> None:
    assert _scan_line(tmp_path, line) == [], f"Round 4 FP still flagged: {line!r}"


# Real secrets that share surface features with the Round 4 FPs (digits,
# ALL_CAPS-adjacent, EAA prefix) must remain detected — no loosening.
_ROUND4_REAL_TP_LINES = [
    "POSTGRES_CONNECTION_STRING=postgresql://postgres:aB3xK9zQmP7@pgvector:5432/evocrm",
    "password=MyStr0ngP@ss123",
    'password="realpass123abc"',
    "access_token=EAAQEwuUtyr0BQ0OXsMIkj2FtzvELZAUyIgiA6LetsqCtROp8Ir6eUb34tVhbbaR86OqZ",
]


@pytest.mark.parametrize("line", _ROUND4_REAL_TP_LINES)
def test_round4_real_secrets_still_flagged(tmp_path: Path, line: str) -> None:
    assert len(_scan_line(tmp_path, line)) > 0, f"Real secret no longer detected: {line!r}"
