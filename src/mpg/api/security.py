"""Password hashing and bearer tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt

from mpg.config import get_settings

ALGORITHM = "HS256"
TOKEN_TTL = timedelta(days=7)
PBKDF2_ROUNDS = 240_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return (
        f"pbkdf2${PBKDF2_ROUNDS}${base64.b64encode(salt).decode()}"
        f"${base64.b64encode(digest).decode()}"
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if scheme != "pbkdf2":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), base64.b64decode(salt_b64), int(rounds)
    )
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


def create_token(user_id: int) -> str:
    payload = {"sub": str(user_id), "exp": datetime.now(UTC) + TOKEN_TTL}
    return jwt.encode(payload, get_settings().secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])
        return int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        return None
