"""Password hashing for IGLA auth.

bcrypt only. Plaintext passwords are NEVER stored, NEVER logged, and
never leave this module -- callers pass a plaintext in and get a hash
out (register) or a bool out (login). The hash embeds a per-password
random salt, so identical passwords produce different hashes.

bcrypt hashes at most the first 72 BYTES of input (note: bytes, not
characters -- multibyte UTF-8 chars count for more). Modern bcrypt
raises on longer input rather than truncating silently. The API
boundary (register endpoint) enforces a max length so users get a
clean error; this module backstops that with an explicit check so an
oversized password can never reach bcrypt unhandled.
"""

import bcrypt

# bcrypt's hard input ceiling. Enforced here as defense-in-depth; the
# API layer also rejects over-long passwords at the boundary.
MAX_PASSWORD_BYTES = 72


def hash_password(plaintext: str) -> str:
    """Return a bcrypt hash (with embedded salt) for a plaintext password.

    Raises ValueError if the password exceeds bcrypt's 72-byte limit.
    """
    password_bytes = plaintext.encode("utf-8")
    if len(password_bytes) > MAX_PASSWORD_BYTES:
        raise ValueError(
            f"Password exceeds {MAX_PASSWORD_BYTES}-byte limit."
        )
    hashed = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plaintext: str, password_hash: str) -> bool:
    """Return True if plaintext matches the stored bcrypt hash.

    Returns False (never raises) on mismatch or a malformed/oversized
    input, so a bad login is always a clean False, never a 500.
    """
    try:
        return bcrypt.checkpw(
            plaintext.encode("utf-8"), password_hash.encode("utf-8")
        )
    except ValueError:
        return False