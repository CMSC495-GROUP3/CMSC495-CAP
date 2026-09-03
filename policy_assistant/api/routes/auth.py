import logging
import os
import re
from datetime import UTC, datetime, timedelta

import bcrypt
from fastapi import APIRouter, HTTPException, Request, status
from jose import jwt
from pydantic import BaseModel

from policy_assistant.api.limiter import limiter

logger = logging.getLogger(__name__)
router = APIRouter()

# No default — main.py already enforced this is set at startup
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

# bcrypt hashes at most 72 bytes. bcrypt 4.x silently truncated longer input;
# 5.x raises. Truncating here keeps a deployment whose password is longer than
# that working across the upgrade, matching the behaviour before this change.
BCRYPT_MAX_PASSWORD_BYTES = 72

# Whole-string shape of a bcrypt hash: prefix, two-digit cost in bcrypt's legal
# range, then 22 salt and 31 checksum characters. bcrypt.checkpw only parses the
# prefix and returns False for anything else wrong, so a truncated hash or a
# trailing newline from a mounted secret would read as a wrong password instead
# of a broken deployment. main.py runs this at import; login runs it again so a
# value that changes under a live process fails just as loudly.
_BCRYPT_HASH = re.compile(r"\$2[aby]\$(0[4-9]|[12]\d|3[01])\$[./A-Za-z0-9]{53}\Z")


def is_bcrypt_hash(value: str) -> bool:
    return _BCRYPT_HASH.fullmatch(value) is not None


class LoginRequest(BaseModel):
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


def _not_configured() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Server authentication is not configured.",
    )


def _encode_candidate(password: str) -> bytes | None:
    """Client input to bytes, or None if it cannot be encoded.

    UnicodeEncodeError subclasses ValueError, and JSON accepts a lone surrogate
    that str.encode refuses. That is a bad password, not a bad hash, so it is
    kept away from the except around checkpw.
    """
    try:
        return password.encode()[:BCRYPT_MAX_PASSWORD_BYTES]
    except UnicodeEncodeError:
        return None


def _check(candidate: bytes, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(candidate, password_hash.encode())
    except ValueError:
        logger.exception(
            "APP_PASSWORD_HASH is not a parseable bcrypt hash (length %d, prefix %r)",
            len(password_hash),
            password_hash[:4],
        )
        raise _not_configured() from None


def create_access_token(data: dict, expires_delta: timedelta) -> str:
    payload = data.copy()
    payload.update({"exp": datetime.now(UTC) + expires_delta})
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
def login(request: Request, body: LoginRequest):
    password_hash = os.getenv("APP_PASSWORD_HASH", "")
    if not password_hash:
        logger.error("APP_PASSWORD_HASH is not configured")
        raise _not_configured()
    if not is_bcrypt_hash(password_hash):
        logger.error(
            "APP_PASSWORD_HASH is not a valid bcrypt hash (length %d, prefix %r)",
            len(password_hash),
            password_hash[:4],
        )
        raise _not_configured()

    candidate = _encode_candidate(body.password)
    is_valid = candidate is not None and _check(candidate, password_hash)
    if not is_valid:
        logger.warning(
            "Failed login attempt from %s", request.client.host if request.client else "unknown"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password.",
        )

    token = create_access_token(
        data={"sub": "user"},
        expires_delta=timedelta(hours=TOKEN_EXPIRE_HOURS),
    )
    return TokenResponse(access_token=token)
