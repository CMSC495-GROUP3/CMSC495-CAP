"""Single shared MongoDB client for the whole application.

Both the API (`policy_assistant/api/`) and the ingestion scripts (`policy_assistant/rag/`) get their
collections from here. Before this module existed there were two independent
`MongoClient` objects per process — one in `api/db.py` and one in
`rag/rag_chain.py` — each with pymongo's default pool of 100 connections.

## Connection arithmetic — redo this before raising WEB_CONCURRENCY

    total connections = uvicorn workers x MONGO_MAX_POOL_SIZE

    4 workers x 20 = 80

MongoDB Atlas caps concurrent connections per cluster, and the free M0 tier caps
it in the low hundreds. Exceeding the cap fails as connection errors under load,
not as a clean error at startup, so the budget is worth keeping honest.

## Deployment constraint: do not use gunicorn --preload

`MongoClient` is not fork-safe. A client created before a process forks shares
socket state with its children, which corrupts connections in ways that surface
as intermittent errors under load rather than as a clean failure.

The client here is created on first use rather than at import, but note that
`api/db.py` binds its collection handles at module level, which triggers
that first use as soon as it is imported. So in practice the client is created
during application import, and the lazy accessor does not by itself make this
safe under a pre-forking server.

What makes it safe is the deployment model: uvicorn's `--workers` spawns fresh
processes that each import the app independently, so every worker builds its own
client after the fork. gunicorn with `--preload` imports the app once in the
master and then forks, which would share one client across workers. Do not use
it. If that ever changes, move the collection handles in `api/db.py` behind
accessor functions so nothing is bound at import.

Constructing a client performs no I/O — pymongo connects on the first real
operation — so import stays fast either way.
"""

import os
from threading import Lock

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from policy_assistant.rag.config import MONGO_MAX_POOL_SIZE

_client: MongoClient | None = None
_lock = Lock()


def get_client() -> MongoClient:
    """Return the process-wide client, creating it on first use."""
    global _client
    if _client is not None:
        return _client

    # Double-checked under a lock: FastAPI serves sync routes from a thread
    # pool, so two requests can race here on the very first call.
    with _lock:
        if _client is None:
            uri = os.getenv("MONGODB_URI")
            if not uri:
                raise RuntimeError(
                    "MONGODB_URI is not set. Copy .env.example to .env and fill it in."
                )
            _client = MongoClient(uri, maxPoolSize=MONGO_MAX_POOL_SIZE)
    return _client


def get_db() -> Database:
    """Return the configured application database."""
    return get_client()[os.getenv("MONGODB_DB", "policy_assistant")]


def get_collection(name: str) -> Collection:
    """Return one collection by name."""
    return get_db()[name]


def reset_client() -> None:
    """Close and forget the client. For tests only."""
    global _client
    with _lock:
        if _client is not None:
            _client.close()
            _client = None
