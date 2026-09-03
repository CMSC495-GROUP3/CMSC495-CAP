import logging
import os
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


class LoginRequest(BaseModel):
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server authentication is not configured.",
        )

    try:
        is_valid = bcrypt.checkpw(body.password.encode(), password_hash.encode())
    except ValueError:
        # checkpw raises when the stored value is not a bcrypt string.
        logger.error("APP_PASSWORD_HASH is not a valid bcrypt hash")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server authentication is not configured.",
        ) from None

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
