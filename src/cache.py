"""Caching for embeddings and answers.

The product premise is that HR answers the same questions repeatedly, which
means query overlap is high and caching is the single biggest lever on cost.
At the 83 req/s target and ~$0.01 per query, every point of cache hit rate is
roughly $30/hour.

## Why MongoDB rather than Redis or an in-process dict

An in-process dict breaks the moment there is more than one uvicorn worker:
four workers means four cold caches and no way to invalidate them together.

Redis is the right tool at genuinely large scale, but here it would add a
service, a client library, a connection budget, and an operational concept —
to save perhaps 10ms on a path that costs 3000ms when it misses. MongoDB is
already running, is shared across workers, survives restarts, and gets expiry
for free from TTL indexes.

## Invalidation is passive

The corpus version is part of the answer cache key. Re-running ingestion bumps
that version, so every prior key simply stops matching and the TTL collects
them later. There is no cache-clearing code, so there is no cache-clearing bug.

## TTL indexes are awkward to change

Changing `expireAfterSeconds` on an existing index raises IndexOptionsConflict —
MongoDB requires a `collMod`, not a re-create. If you change one of the TTL
settings in config.py, drop the index by hand or run collMod. This is exactly
the kind of change that looks harmless and fails at startup.
"""

import hashlib
import re
import uuid
from datetime import UTC, datetime

from config import (
    ANSWER_CACHE_TTL_SECONDS,
    CACHE_ENABLED,
    PROMPT_VERSION,
)
from llm import get_provider
from mongo import get_collection

_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Fold trivial differences so "How much PTO?" and "how much  pto?" agree."""
    return _WHITESPACE.sub(" ", text.strip().lower())


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def question_hash(question: str) -> str:
    """Stable identity for a question, used to group repeats in query logs."""
    return _digest(normalize(question))


# ── Corpus version ────────────────────────────────────────────────────────────


def get_corpus_version() -> str:
    """Current corpus version, creating it on first call.

    Read once per chat request. That is a single point lookup on `_id`, around a
    millisecond, and it is deliberately not memoized in-process: a stale version
    means serving answers from a corpus that has already been replaced, and the
    read is far too cheap to be worth that risk.
    """
    doc = get_collection("meta").find_one_and_update(
        {"_id": "corpus"},
        {"$setOnInsert": {"version": uuid.uuid4().hex, "updated_at": datetime.now(UTC)}},
        upsert=True,
        return_document=True,
    )
    return doc["version"]


def bump_corpus_version() -> str:
    """Invalidate every cached answer. Called after ingestion or a reindex."""
    version = uuid.uuid4().hex
    get_collection("meta").update_one(
        {"_id": "corpus"},
        {"$set": {"version": version, "updated_at": datetime.now(UTC)}},
        upsert=True,
    )
    return version


# ── Embedding cache ───────────────────────────────────────────────────────────


def embedding_cache_key(text: str) -> str:
    return _digest(normalize(text), get_provider().embedding_fingerprint())


def get_cached_embedding(text: str) -> list[float] | None:
    if not CACHE_ENABLED:
        return None
    doc = get_collection("embedding_cache").find_one(
        {"_id": embedding_cache_key(text)}, {"embedding": 1}
    )
    return doc["embedding"] if doc else None


def put_cached_embedding(text: str, embedding: list[float]) -> None:
    if not CACHE_ENABLED:
        return
    get_collection("embedding_cache").update_one(
        {"_id": embedding_cache_key(text)},
        {"$set": {"embedding": embedding, "created_at": datetime.now(UTC)}},
        upsert=True,
    )


def embed_cached(text: str) -> tuple[list[float], bool]:
    """Embed a query, using the cache. Returns (embedding, was_cache_hit)."""
    cached = get_cached_embedding(text)
    if cached is not None:
        return cached, True
    embedding = get_provider().embed(text)
    put_cached_embedding(text, embedding)
    return embedding, False


# ── Answer cache ──────────────────────────────────────────────────────────────


def answer_cache_key(question: str, corpus_version: str) -> str:
    """Key an answer to everything that could change it.

    Corpus version so re-ingestion invalidates. Answer-model fingerprint so a
    model swap invalidates. Prompt version so editing the system prompt
    invalidates — otherwise a prompt fix would be masked until the TTL expired.
    """
    return _digest(
        normalize(question),
        corpus_version,
        get_provider().answer_fingerprint(),
        PROMPT_VERSION,
    )


def get_cached_answer(question: str, corpus_version: str) -> dict | None:
    """Return a cached answer, or None.

    Callers must only use this on the first turn of a conversation — see
    `is_cacheable_turn`.
    """
    if not CACHE_ENABLED:
        return None

    key = answer_cache_key(question, corpus_version)
    doc = get_collection("answer_cache").find_one_and_update(
        {"_id": key},
        {"$inc": {"hits": 1}, "$set": {"last_hit_at": datetime.now(UTC)}},
        return_document=True,
    )
    if not doc:
        return None
    return {
        "answer": doc["answer"],
        "sources": doc.get("sources", []),
        "confidence": doc.get("confidence"),
        "follow_ups": doc.get("follow_ups", []),
        "refused": doc.get("refused", False),
    }


def put_cached_answer(question: str, corpus_version: str, result: dict) -> None:
    """Store an answer. Refusals are cached too — a re-ingestion that adds the
    missing policy changes the corpus version, so the refusal stops matching."""
    if not CACHE_ENABLED:
        return

    get_collection("answer_cache").update_one(
        {"_id": answer_cache_key(question, corpus_version)},
        {
            "$set": {
                "question_sample": question[:300],
                "corpus_version": corpus_version,
                "answer": result["answer"],
                "sources": result.get("sources", []),
                "confidence": result.get("confidence"),
                "follow_ups": result.get("follow_ups", []),
                "refused": result.get("refused", False),
                "created_at": datetime.now(UTC),
            },
            "$setOnInsert": {"hits": 0},
        },
        upsert=True,
    )


def is_cacheable_turn(history: list) -> bool:
    """Only the first question of a conversation may be served from cache.

    With history in play, the prompt carries prior turns and the citation
    manifest, so the same standalone question can legitimately produce a
    different answer. Restricting the cache to first turns makes correctness
    obvious by construction, and first turns are where the repeated-FAQ traffic
    lives anyway.

    The simplicity of this rule is the point. Resist extending it.
    """
    return not history


__all__ = [
    "ANSWER_CACHE_TTL_SECONDS",
    "answer_cache_key",
    "bump_corpus_version",
    "embed_cached",
    "embedding_cache_key",
    "get_cached_answer",
    "get_corpus_version",
    "is_cacheable_turn",
    "normalize",
    "put_cached_answer",
    "question_hash",
]
