"""Shared FastAPI dependencies — primarily JWT verification."""

import hmac
import os

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from policy_assistant.api.routes.auth import (
    FINGERPRINT_HEX_LEN,
    PASSWORD_HASH_VARS,
    credential_fingerprint,
)

# No default — main.py already enforced this is set at startup
SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = "HS256"

bearer_scheme = HTTPBearer()

# Membership check before any os.getenv(cred): a JWT must not pick an arbitrary
# environment variable. Tuple membership is fine at this size; the frozenset
# makes the allowlist intent obvious at the call site.
_PASSWORD_HASH_VAR_NAMES = frozenset(PASSWORD_HASH_VARS)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_auth(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise _unauthorized() from None

    cred = payload.get("cred")
    fingerprint = payload.get("fingerprint")
    # Missing fingerprint (legacy tokens) and non-strings are the same failure:
    # the session is not bound to a currently configured hash. Length must match
    # before compare_digest so a malformed claim cannot turn into a 500.
    if not isinstance(cred, str) or not isinstance(fingerprint, str):
        raise _unauthorized()
    if len(fingerprint) != FINGERPRINT_HEX_LEN:
        raise _unauthorized()
    if cred not in _PASSWORD_HASH_VAR_NAMES:
        raise _unauthorized()

    current_hash = os.getenv(cred, "")
    if not current_hash:
        raise _unauthorized()

    expected = credential_fingerprint(current_hash)
    if not hmac.compare_digest(fingerprint, expected):
        raise _unauthorized()
