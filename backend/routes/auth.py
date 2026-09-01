import os
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from jose import jwt
from passlib.context import CryptContext

from limiter import limiter

logger = logging.getLogger(__name__)
router = APIRouter()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

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
    payload.update({"exp": datetime.now(timezone.utc) + expires_delta})
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

    if not pwd_context.verify(body.password, password_hash):
        logger.warning("Failed login attempt from %s", request.client.host if request.client else "unknown")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password.",
        )

    token = create_access_token(
        data={"sub": "user"},
        expires_delta=timedelta(hours=TOKEN_EXPIRE_HOURS),
    )
    return TokenResponse(access_token=token)
