"""Collection handles for the backend.

The client itself lives in `src/mongo.py` and is shared with the ingestion
scripts, so the whole process holds exactly one connection pool. See that
module for the connection budget and the fork-safety reasoning.

Index creation is deliberately *not* run at import. It performs real I/O, and
doing I/O at import means a database problem surfaces as an confusing traceback
during module loading rather than as a clear startup failure. `main.py` calls
`ensure_indexes()` from the application lifespan instead.
"""
import os
import sys

from pymongo import ASCENDING, DESCENDING

# src/ holds the RAG pipeline, its config, and the shared Mongo client.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from config import PASSAGES_COLLECTION  # noqa: E402
from mongo import get_collection  # noqa: E402

# Constructing a collection handle performs no I/O — pymongo connects on the
# first real operation — so binding these at import is safe.
conversations_col = get_collection("conversations")
projects_col = get_collection("projects")

# One record per passage: text, metadata, and embedding together.
passages_col = get_collection(PASSAGES_COLLECTION)

# Denormalized one-record-per-document view, built from passages_col. Kept
# separate so browsing and searching the corpus never scans the passage
# collection, which carries a 1536-float vector on every record.
documents_col = get_collection("documents")


def ensure_indexes() -> None:
    """Create indexes if they don't already exist (idempotent).

    Called once per worker at startup. Concurrent calls across workers are safe:
    `create_index` with identical options is a no-op.

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
