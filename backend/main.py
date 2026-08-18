"""FastAPI application entrypoint."""
import os
import sys

from dotenv import load_dotenv

# Load .env before importing routes — route modules read env vars at import
# time, so load_dotenv must run first.
load_dotenv()

# Fail fast on missing secrets. A clear startup error beats a silent security
# hole such as signing JWTs with a predictable default key.
_REQUIRED = ("JWT_SECRET_KEY", "MONGODB_URI")
_missing = [name for name in _REQUIRED if not os.getenv(name)]
if _missing:
    raise RuntimeError(
        f"Missing required environment variables: {', '.join(_missing)}. "
        f"Copy .env.example to .env and fill it in. "
        f"Generate a JWT secret with: openssl rand -hex 32"
    )

from contextlib import asynccontextmanager  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from config import APP_NAME, SIMILARITY_THRESHOLD  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from slowapi import _rate_limit_exceeded_handler  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402

from db import ensure_indexes  # noqa: E402
from limiter import limiter  # noqa: E402
from routes.auth import router as auth_router  # noqa: E402
from routes.chat import router as chat_router  # noqa: E402
from routes.conversations import router as conversations_router  # noqa: E402
from routes.documents import router as documents_router  # noqa: E402
from routes.projects import router as projects_router  # noqa: E402


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup work that touches the database.

    Index creation used to run at import of db.py. Doing I/O at import means a
    database problem surfaces as a traceback while modules are still loading,
    which is hard to read and impossible to handle. Running it here makes a
    failure a clear startup error instead.

    This runs once per uvicorn worker. Concurrent create_index calls with
    identical options are no-ops, so multiple workers racing is safe.
    """
    ensure_indexes()
    yield


app = FastAPI(
    lifespan=lifespan,
    title=f"{APP_NAME} API",
    description="Grounded question answering over internal policy documents.",
    version="0.1.0",
)

# Attach limiter state so slowapi can find it on the app instance
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# In production Nginx proxies the API and the frontend under one origin, so CORS
# is only needed for local development against the Vite dev server.
_default_origins = "http://localhost:5173,http://localhost:3000"
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", _default_origins).split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(auth_router, prefix="/api/auth")
app.include_router(chat_router, prefix="/api")
app.include_router(conversations_router, prefix="/api")
app.include_router(documents_router, prefix="/api")
app.include_router(projects_router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def config():
    """Non-secret configuration the frontend reads at startup."""
    return {
        "app_name": APP_NAME,
        "similarity_threshold": SIMILARITY_THRESHOLD,
    }
