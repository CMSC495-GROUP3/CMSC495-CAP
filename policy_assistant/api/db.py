"""Collection handles for the backend.

The client itself lives in `rag/mongo.py` and is shared with the ingestion
scripts, so the whole process holds exactly one connection pool. See that
module for the connection budget and the fork-safety reasoning.

Index creation is deliberately *not* run at import. It performs real I/O, and
doing I/O at import means a database problem surfaces as an confusing traceback
during module loading rather than as a clear startup failure. `main.py` calls
`ensure_indexes()` from the application lifespan instead.
"""

from pymongo import ASCENDING, DESCENDING

# rag/ holds the pipeline, its config, and the shared Mongo client.
from policy_assistant.rag.config import (
    ANSWER_CACHE_TTL_SECONDS,
    EMBEDDING_CACHE_TTL_SECONDS,
    PASSAGES_COLLECTION,
    QUERY_LOG_TTL_SECONDS,
)
from policy_assistant.rag.mongo import get_collection

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

# One record per chat request. The substrate for content-gap and FAQ analytics.
query_logs_col = get_collection("query_logs")

# One record per hand-off to a person. See api/routes/escalations.py.
escalations_col = get_collection("escalations")

# Caches. Keyed by content hash on _id, so no separate unique index is needed.
answer_cache_col = get_collection("answer_cache")
embedding_cache_col = get_collection("embedding_cache")

# Single-document collection holding the corpus version. No index needed — it
# is only ever read by _id.
meta_col = get_collection("meta")


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

    # query_logs — three access patterns:
    #   created_at        windowed counts, and the TTL that keeps this bounded
    #   refused + time    the content-gap query ("what did we fail to answer?")
    #   hash + time       grouping repeats into a ranked FAQ list
    #
    # The TTL is not housekeeping. At the 83 req/s target this collection would
    # grow by roughly 7M documents a day.
    query_logs_col.create_index(
        [("created_at", DESCENDING)], expireAfterSeconds=QUERY_LOG_TTL_SECONDS
    )
    query_logs_col.create_index([("refused", ASCENDING), ("created_at", DESCENDING)])
    query_logs_col.create_index([("question_hash", ASCENDING), ("created_at", DESCENDING)])

    # escalations — point lookup by id, the open queue newest first, and the
    # per-conversation lookup the chat UI uses when reopening a conversation
    escalations_col.create_index("escalation_id", unique=True)
    escalations_col.create_index([("status", ASCENDING), ("created_at", DESCENDING)])
    # One escalation per message. The route checks before inserting, but two
    # concurrent requests can both pass that check; this index is what makes
    # the second one fail instead of filing a duplicate.
    escalations_col.create_index(
        [("session_id", ASCENDING), ("message_index", ASCENDING)], unique=True
    )

    # Caches — expiry only. Lookups are by _id, which is indexed implicitly.
    #
    # NOTE: changing any expireAfterSeconds above or below raises
    # IndexOptionsConflict on an existing index. MongoDB requires collMod to
    # change a TTL; drop the index first if you need to adjust one.
    answer_cache_col.create_index(
        [("created_at", ASCENDING)], expireAfterSeconds=ANSWER_CACHE_TTL_SECONDS
    )
    embedding_cache_col.create_index(
        [("created_at", ASCENDING)], expireAfterSeconds=EMBEDDING_CACHE_TTL_SECONDS
    )
