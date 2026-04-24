"""Brain Repo — GitHub-based versioning for workspace memory and customizations.

Import-time API-contract check
------------------------------
``routes/brain_repo.py`` assumes specific symbols and signatures from this
package. When they drift (module renamed, function signature changed, keyword
arg missing) the old behaviour was to fall through to an `except ImportError`
branch in the routes, which silently stored PAT tokens as plaintext and
returned generic "restore module not yet available" messages — bugs that
slipped past reviewers because they never surfaced at startup.

This block runs ``inspect.signature`` on the critical APIs and logs a WARNING
if they diverge. The warning is visible in startup logs so future regressions
can be caught before they reach production. Assertions are soft (log only)
because we don't want a typo to crash the entire app — a warning with the
exact divergence is enough signal.
"""

import inspect
import logging as _logging

_log = _logging.getLogger(__name__)


def _verify_api_compat() -> None:
    """Fail-loud import-time check: routes/brain_repo.py assumes these contracts."""
    # pat_auth.py is a shim re-exporting from github_oauth — must expose both
    # the class and the module-level decrypt_token function.
    try:
        from brain_repo.pat_auth import PATAuthProvider, decrypt_token  # noqa: F401
    except ImportError as exc:
        _log.warning("brain_repo API check: pat_auth missing expected exports: %s", exc)
        return

    # PATAuthProvider must be constructable with (pat, master_key) and expose
    # encrypt_token(). The old bug was that routes/brain_repo.py called
    # ``.encrypt(token)`` on a class whose real method is ``encrypt_token()`` —
    # the type mismatch dropped into the `except ImportError` fallback which
    # stored the PAT as raw bytes (plaintext).
    try:
        init_sig = inspect.signature(PATAuthProvider.__init__)
        init_params = set(init_sig.parameters.keys()) - {"self"}
        assert init_params == {"pat", "master_key"}, \
            f"PATAuthProvider.__init__ signature drift: {init_params} (expected pat, master_key)"
        assert hasattr(PATAuthProvider, "encrypt_token"), \
            "PATAuthProvider missing encrypt_token() method"
    except AssertionError as exc:
        _log.warning("brain_repo API check: %s", exc)

    # execute_restore is called from an SSE generator; a signature drift used
    # to crash silently in production with TypeError wrapped in a generic
    # error event. Now it logs loudly at import.
    try:
        from brain_repo.restore import execute_restore
        sig = inspect.signature(execute_restore)
        params = set(sig.parameters.keys())
        expected = {"repo_url", "ref", "token", "install_dir", "include_kb", "kb_key_matches"}
        if params != expected:
            missing = expected - params
            extra = params - expected
            _log.warning(
                "brain_repo API check: execute_restore signature drift — missing=%s, extra=%s",
                missing, extra,
            )
    except ImportError as exc:
        _log.warning("brain_repo API check: restore module unavailable: %s", exc)


def _verify_crypto_ready() -> None:
    """Validate that token encryption is actually wired up.

    Emits CRITICAL-level logs when crypto is broken so the failure appears in
    production log aggregators (Sentry, syslog, etc.) — the previous WARNING
    was routinely ignored. Flagged in PR review by @davidsoncelestino:
    'log.warning não é notado por ninguém em produção'.
    """
    import os as _os
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        _log.critical(
            "brain_repo CRYPTO CHECK: cryptography module unavailable — "
            "connect/sync endpoints will return 500; stored tokens cannot be decrypted",
        )
        return
    key = _os.environ.get("BRAIN_REPO_MASTER_KEY", "")
    if not key:
        _log.critical(
            "brain_repo CRYPTO CHECK: BRAIN_REPO_MASTER_KEY not set — "
            "connect/sync endpoints will return 500 with code=CRYPTO_UNAVAILABLE",
        )
        return
    try:
        Fernet(key.encode())  # validates format (must be 32-byte url-safe base64)
    except Exception as exc:
        _log.critical(
            "brain_repo CRYPTO CHECK: master key invalid format (%s) — connect/sync will fail",
            exc,
        )


def is_crypto_ready() -> bool:
    """Runtime check exposed so the HTTP /status endpoint can report it."""
    import os as _os
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return False
    key = _os.environ.get("BRAIN_REPO_MASTER_KEY", "")
    if not key:
        return False
    try:
        Fernet(key.encode())
    except Exception:
        return False
    return True


try:
    _verify_api_compat()
    _verify_crypto_ready()
except Exception as _exc:  # pragma: no cover — belt-and-suspenders
    _log.warning("brain_repo API/crypto check raised unexpectedly: %s", _exc)
