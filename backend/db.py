"""Single shared MongoDB client for the entire backend.

All route modules import collections from here instead of each creating their
own MongoClient. This avoids spinning up multiple connection pools against the
same cluster — which matters on a free-tier Atlas deployment with a low
connection cap — and keeps index creation in one place.
"""
import os
import sys

from pymongo import ASCENDING, DESCENDING, MongoClient

# src/ holds the RAG pipeline and its config; the backend reads both.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from config import PASSAGES_COLLECTION  # noqa: E402

_client = MongoClient(os.getenv("MONGODB_URI"))
_db = _client[os.getenv("MONGODB_DB", "policy_assistant")]

conversations_col = _db["conversations"]
projects_col = _db["projects"]

# One record per passage: text, metadata, and embedding together.
passages_col = _db[PASSAGES_COLLECTION]

# Denormalized one-record-per-document view, built from passages_col. Kept
# separate so browsing and searching the corpus never scans the passage
# collection, which carries a 1536-float vector on every record.
documents_col = _db["documents"]


def ensure_indexes() -> None:
    """Create indexes if they don't already exist (idempotent).

    Note this cannot create the Atlas Vector Search index — that is a search
    index, not a regular one, and must be created in the Atlas UI or CLI. See
    the README.
    """
    # conversations — point lookup by session_id, sorted by updated_at for the sidebar
    conversations_col.create_index("session_id", unique=True)
    conversations_col.create_index([("updated_at", DESCENDING)])

    # projects — point lookup by project_id
    projects_col.create_index("project_id", unique=True)

    # passages — fetch one document's passages in order
    passages_col.create_index([("source", ASCENDING), ("chunk_index", ASCENDING)])

    # documents — unique key, plus sorted browse and category filtering
    documents_col.create_index("source", unique=True)
    documents_col.create_index([("title", ASCENDING)])
    documents_col.create_index([("category", ASCENDING)])


# Run once at import time — all no-ops after the first run
ensure_indexes()
