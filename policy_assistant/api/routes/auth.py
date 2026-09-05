import hashlib
import logging
import os
import re
from datetime import UTC, datetime, timedelta

import anyio
import bcrypt
from anyio import CapacityLimiter
from fastapi import APIRouter, HTTPException, Request, status
from jose import jwt
from pydantic import BaseModel

from policy_assistant.api.limiter import limiter
from policy_assistant.rag.config import LOGIN_THREADPOOL_TOKENS

logger = logging.getLogger(__name__)
router = APIRouter()

# Login runs off the default THREADPOOL_TOKENS pool so bcrypt still works when
# every chat slot is held by a stalled or slow provider call. Sized small:
# checkpw is milliseconds, not generation-length.
_login_limiter = CapacityLimiter(LOGIN_THREADPOOL_TOKENS)

# No default — main.py already enforced this is set at startup
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

# bcrypt hashes at most 72 bytes. bcrypt 4.x silently truncated longer input;
# 5.x raises. Truncating here keeps a deployment whose password is longer than
# that working across the upgrade, matching the behaviour before this change.
BCRYPT_MAX_PASSWORD_BYTES = 72

# Bound into the JWT so require_auth can tell a rotated hash from the one that
# opened the session. First 12 hex chars of sha256(hash) — never the hash.
FINGERPRINT_HEX_LEN = 12

# Whole-string shape of a bcrypt hash: prefix, two-digit cost in bcrypt's legal
# range, then 22 salt and 31 checksum characters. bcrypt.checkpw only parses the
# prefix and returns False for anything else wrong, so a truncated hash or a
# trailing newline from a mounted secret would read as a wrong password instead
# of a broken deployment. main.py runs this at import; login runs it again so a
# value that changes under a live process fails just as loudly.
_BCRYPT_HASH = re.compile(r"\$2[aby]\$(0[4-9]|[12]\d|3[01])\$[./A-Za-z0-9]{53}\Z")


def is_bcrypt_hash(value: str) -> bool:
    return _BCRYPT_HASH.fullmatch(value) is not None


# The two environment variables that may hold an accepted password hash: the
# team's, which is required, and an optional second so a reviewer's password
# can be handed out and rotated without touching the team's. This is a pair,
# not a list: a third password means editing this tuple, .env.example, and the
# README together. Separate variables rather than one delimited list because a
# bcrypt hash is full of `$`, which makes a list painful to quote in .env.
PASSWORD_HASH_VARS = ("APP_PASSWORD_HASH", "APP_PASSWORD_HASH_2")
PRIMARY_PASSWORD_HASH_VAR = PASSWORD_HASH_VARS[0]


class PasswordHashError(ValueError):
    """A password hash variable is missing or is not a bcrypt hash."""


def configured_password_hashes() -> list[tuple[str, str]]:
    """(variable name, value) for each password hash variable that is set.

    Unset and empty are the same thing: not configured.
    """
    return [(name, value) for name in PASSWORD_HASH_VARS if (value := os.getenv(name, ""))]


def validate_password_hashes() -> list[tuple[str, str]]:
    """The hashes login may accept, or PasswordHashError naming the bad variable.

    checkpw cannot tell a corrupted hash from a wrong password, so a stray
    newline in a mounted secret would lock everyone out and log it as failed
    logins. main.py runs this at import so that refuses to start instead;
    login runs it again so a value that changes under a live process fails
    just as loudly. The first variable is required; the second, if set at
    all, has to be right.
    """
    hashes = configured_password_hashes()
    if not any(name == PRIMARY_PASSWORD_HASH_VAR for name, _ in hashes):
        raise PasswordHashError(f"{PRIMARY_PASSWORD_HASH_VAR} is not configured")
    for name, value in hashes:
        if not is_bcrypt_hash(value):
            raise PasswordHashError(
                f"{name} is not a valid bcrypt hash (length {len(value)}, prefix {value[:4]!r})"
            )
    return hashes


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


def _check(candidate: bytes, name: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(candidate, password_hash.encode())
    except ValueError:
        logger.exception(
            "%s is not a parseable bcrypt hash (length %d, prefix %r)",
            name,
            len(password_hash),
            password_hash[:4],
        )
        raise _not_configured() from None


def _password_hashes() -> list[tuple[str, str]]:
    """The hashes login may accept, or a 500 if any configured one is broken."""
    try:
        return validate_password_hashes()
    except PasswordHashError as exc:
        logger.error("%s", exc)
        raise _not_configured() from None


def _client_host(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def credential_fingerprint(password_hash: str) -> str:
    """Stable token claim derived from a password hash.

    Short enough to keep the JWT small; collision-resistant enough that a
    rotated hash will not match by chance. Never put the hash itself in a token.
    """
    return hashlib.sha256(password_hash.encode()).hexdigest()[:FINGERPRINT_HEX_LEN]


def create_access_token(data: dict, expires_delta: timedelta) -> str:
    payload = data.copy()
    payload.update({"exp": datetime.now(UTC) + expires_delta})
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _authenticate(password: str, client_host: str) -> TokenResponse:
    """Verify the password and mint a token. Runs on the login thread pool."""
    hashes = _password_hashes()

    # First match wins, primary first. Stopping at a match is fine: the only
    # thing a shorter response reveals is which variable the password lives
    # in, and the only party who can observe that has just logged in with it.
    candidate = _encode_candidate(password)
    matched: tuple[str, str] | None = None
    if candidate is not None:
        matched = next(
            ((name, value) for name, value in hashes if _check(candidate, name, value)),
            None,
        )
    if matched is None:
        logger.warning("Failed login attempt from %s", client_host)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password.",
        )

    # Both passwords open the same door. Recording which one was used is the
    # only way to tell a reviewer's session from the team's afterwards. cred is
    # the variable name; fingerprint binds the session to that hash so rotating
    # it revokes those sessions without touching JWT_SECRET_KEY.
    cred, password_hash = matched
    logger.info("Login with %s from %s", cred, client_host)
    token = create_access_token(
        data={
            "sub": "user",
            "cred": cred,
            "fingerprint": credential_fingerprint(password_hash),
        },
        expires_delta=timedelta(hours=TOKEN_EXPIRE_HOURS),
    )
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest):
    return await anyio.to_thread.run_sync(
        _authenticate,
        body.password,
        _client_host(request),
        limiter=_login_limiter,
    )
