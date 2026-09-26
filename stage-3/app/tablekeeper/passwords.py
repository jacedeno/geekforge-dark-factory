"""Password hashing with scrypt. Hashing is slow on purpose and runs outside the state lock."""

import base64
import hashlib
import hmac
import secrets
import threading

N, R, P = 2 ** 14, 8, 1
_MAXMEM = 64 * 1024 * 1024
# Each hash takes 16 MiB; bound how many run at once so 50 concurrent logins stay in memory.
_slots = threading.BoundedSemaphore(8)


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def hash_password(password):
    salt = secrets.token_bytes(16)
    with _slots:
        digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=N, r=R, p=P,
                                maxmem=_MAXMEM, dklen=32)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"


def parse_hash(stored):
    """Return (n, r, p, salt, digest) or None when the stored string is not a valid hash."""
    if not isinstance(stored, str):
        return None
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return None
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = base64.b64decode(parts[4], validate=True)
        digest = base64.b64decode(parts[5], validate=True)
    except ValueError:
        return None
    if n < 2 or n > 2 ** 20 or n & (n - 1) or not 1 <= r <= 32 or not 1 <= p <= 16:
        return None
    if not salt or len(salt) > 64 or not 16 <= len(digest) <= 64:
        return None
    return n, r, p, salt, digest


def verify_password(password, stored):
    parsed = parse_hash(stored)
    if parsed is None:
        return False
    n, r, p, salt, digest = parsed
    with _slots:
        candidate = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                                   maxmem=_MAXMEM, dklen=len(digest))
    return hmac.compare_digest(candidate, digest)
