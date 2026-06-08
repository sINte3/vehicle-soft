"""
bot_security.py -- BOT001 Telegram Foundation
Security helper functions for Telegram bot integration.

Rules:
- stdlib only (secrets, hashlib, hmac, datetime)
- No external dependencies
- No tokens or secrets stored in this file
- Compatible with Python 3.14
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timezone


def utcnow():
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


def generate_link_code(length=6):
    """Generate a random numeric one-time link code.

    Returns a zero-padded string of `length` decimal digits.
    Uses secrets.randbelow for cryptographic randomness.
    """
    if length < 4 or length > 12:
        raise ValueError("length must be between 4 and 12")
    upper = 10 ** length
    code = secrets.randbelow(upper)
    return str(code).zfill(length)


def hash_secret(value):
    """Return a SHA-256 hex digest of value (str or bytes).

    Use this to store one-time codes and API tokens -- never store
    the raw value in the database.
    """
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def verify_secret(value, expected_hash):
    """Constant-time comparison of hash_secret(value) against expected_hash.

    Returns True only when the hash matches.
    Uses hmac.compare_digest to prevent timing attacks.
    """
    if not value or not expected_hash:
        return False
    computed = hash_secret(value)
    if not isinstance(expected_hash, str):
        return False
    return hmac.compare_digest(computed, expected_hash)


def make_api_token():
    """Generate a cryptographically random URL-safe API token (32 bytes = 43 chars).

    The raw token is returned to the caller ONCE; only the hash should be stored.
    """
    return secrets.token_urlsafe(32)


def hash_api_token(token):
    """Return a SHA-256 hex digest of an API token string.

    Store this in bot_api_sessions.token_hash, not the raw token.
    """
    return hash_secret(token)


def parse_datetime_safe(value):
    """Parse a datetime from various formats, returning None on failure.

    Accepts:
    - datetime objects (returned as-is, converted to UTC-aware if naive)
    - ISO 8601 strings (with or without timezone)
    - None / empty string

    Never raises an exception.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    # Try common ISO formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None
