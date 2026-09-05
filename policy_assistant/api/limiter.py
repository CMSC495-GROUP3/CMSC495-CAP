"""Shared slowapi rate limiter instance.

Imported by both main.py (to register the exception handler) and any route
that needs a rate limit decorator.
"""

import logging
import os

from fastapi import Request
from fastapi.responses import Response
from jose import JWTError, jwt
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)

# Same secret the auth dependency uses. Read at call time so tests that patch
# the env after import still see the current value.
_ALGORITHM = "HS256"


def _cred_claim(request: Request) -> str | None:
    """Return the JWT ``cred`` claim for logging, or None if absent/invalid.

    Does not log the token. A missing or bad Authorization header is treated
    the same as no claim — rate limits also cover unauthenticated routes.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    secret = os.getenv("JWT_SECRET_KEY")
    if not secret:
        return None
    try:
        payload = jwt.decode(auth[7:], secret, algorithms=[_ALGORITHM])
    except JWTError:
        return None
    cred = payload.get("cred")
    return cred if isinstance(cred, str) else None


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """Log cred + remote address on 429, then return slowapi's default body."""
    logger.warning(
        "Rate limit exceeded for cred=%s from %s on %s",
        _cred_claim(request) or "unknown",
        get_remote_address(request),
        request.url.path,
    )
    return _rate_limit_exceeded_handler(request, exc)
