"""Security helpers for auth routes.

Keeps password policy and login throttling out of the route handlers so the
same rules can be reused across setup, user management, and password changes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from models import LoginThrottle, db


PASSWORD_COMMON_BLOCKLIST = {
    "123456",
    "12345678",
    "123456789",
    "password",
    "password1",
    "password123",
    "admin",
    "admin123",
    "qwerty",
    "qwerty123",
    "welcome",
    "welcome123",
    "letmein",
    "iloveyou",
    "abc123",
    "changeme",
    "monkey",
    "dragon",
    "sunshine",
    "football",
}

LOGIN_RESET_WINDOW = timedelta(minutes=15)
LOGIN_LOCK_RULES = (
    (10, timedelta(minutes=30)),
    (5, timedelta(minutes=5)),
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_login_key(value: str | None) -> str:
    return (value or "").strip().lower()


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iter_login_keys(username: str | None, ip_address: str | None):
    username_key = normalize_login_key(username)
    if username_key:
        yield "username", username_key
    ip_key = (ip_address or "").strip()
    if ip_key:
        yield "ip", ip_key


def _lock_duration(failed_attempts: int) -> timedelta | None:
    for threshold, duration in LOGIN_LOCK_RULES:
        if failed_attempts >= threshold:
            return duration
    return None


def password_policy_violations(password: str, *, username: str = "", email: str = "") -> list[str]:
    """Return human-readable violations for a candidate password."""

    candidate = password or ""
    violations: list[str] = []

    if len(candidate) < 8:
        violations.append("Password must be at least 8 characters")
    if not any(ch.islower() for ch in candidate):
        violations.append("Password must include a lowercase letter")
    if not any(ch.isupper() for ch in candidate):
        violations.append("Password must include an uppercase letter")
    if not any(ch.isdigit() for ch in candidate):
        violations.append("Password must include a digit")
    if not any(not ch.isalnum() for ch in candidate):
        violations.append("Password must include a special character")

    normalized = candidate.casefold()
    if normalized in PASSWORD_COMMON_BLOCKLIST:
        violations.append("Password is too common")

    identity_tokens = {
        normalize_login_key(username),
        normalize_login_key(email),
    }
    email_value = normalize_login_key(email)
    if "@" in email_value:
        identity_tokens.add(email_value.split("@", 1)[0])
        identity_tokens.add(email_value.split("@", 1)[1])

    for token in identity_tokens:
        if len(token) >= 3 and token in normalized:
            violations.append("Password must not contain your username or email address")
            break

    return violations


def get_active_login_lockout(username: str | None, ip_address: str | None) -> datetime | None:
    """Return the furthest active lockout for username/IP, if any."""

    now = utcnow()
    lock_until: datetime | None = None

    for dimension, lookup_key in _iter_login_keys(username, ip_address):
        bucket = LoginThrottle.query.filter_by(dimension=dimension, lookup_key=lookup_key).first()
        bucket_lock_until = _as_utc(bucket.locked_until) if bucket else None
        if not bucket or not bucket_lock_until or bucket_lock_until <= now:
            continue
        if lock_until is None or bucket_lock_until > lock_until:
            lock_until = bucket_lock_until

    return lock_until


def record_login_failure(username: str | None, ip_address: str | None) -> datetime | None:
    """Increment failure counters and return the resulting lockout time."""

    now = utcnow()
    lock_until: datetime | None = None

    for dimension, lookup_key in _iter_login_keys(username, ip_address):
        bucket = LoginThrottle.query.filter_by(dimension=dimension, lookup_key=lookup_key).first()
        last_failed_at = _as_utc(bucket.last_failed_at) if bucket else None
        locked_until = _as_utc(bucket.locked_until) if bucket else None
        if bucket is None:
            bucket = LoginThrottle(
                dimension=dimension,
                lookup_key=lookup_key,
                failed_attempts=0,
                first_failed_at=now,
                last_failed_at=now,
            )
            db.session.add(bucket)
        elif last_failed_at and now - last_failed_at > LOGIN_RESET_WINDOW:
            bucket.failed_attempts = 0
            bucket.first_failed_at = now

        if locked_until and locked_until > now:
            if lock_until is None or locked_until > lock_until:
                lock_until = locked_until
            continue

        bucket.failed_attempts += 1
        bucket.last_failed_at = now

        duration = _lock_duration(bucket.failed_attempts)
        if duration is not None:
            bucket.locked_until = now + duration
            if lock_until is None or bucket.locked_until > lock_until:
                lock_until = bucket.locked_until
        else:
            bucket.locked_until = None

    return lock_until


def clear_login_throttles(username: str | None, ip_address: str | None) -> None:
    for dimension, lookup_key in _iter_login_keys(username, ip_address):
        bucket = LoginThrottle.query.filter_by(dimension=dimension, lookup_key=lookup_key).first()
        if bucket is not None:
            db.session.delete(bucket)
