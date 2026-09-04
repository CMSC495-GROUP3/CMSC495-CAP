"""Central configuration for the retrieval and generation pipeline.

This is the single source of truth for tuning knobs that both the ingestion
scripts (policy_assistant/rag/) and the API (policy_assistant/api/) need to
agree on.

Every value can be overridden with an environment variable so the pilot can be
retuned without a code change.
"""

import os

# ── Product identity ──────────────────────────────────────────────────────────
# The product name. Change it here and in web/src/config.ts (plus the <title>
# in web/index.html) to rebrand the whole application.
APP_NAME = os.getenv("APP_NAME", "Sourcebook")

# ── Storage ───────────────────────────────────────────────────────────────────
# S3 key prefix the ingestion pipeline reads from and the seed script writes to.
S3_DOCUMENT_PREFIX = os.getenv("S3_DOCUMENT_PREFIX", "documents/")

# MongoDB collection holding one record per passage: text + metadata + embedding.
# Keeping all three in one collection is the design decision described in the
# proposal — it avoids pairing a vector store with a separate document store.
PASSAGES_COLLECTION = os.getenv("PASSAGES_COLLECTION", "passages")

# Connections held per process. Total load on the cluster is
# (uvicorn workers x this), which must stay under the Atlas connection cap —
# see the arithmetic in policy_assistant/rag/mongo.py before raising either number.
MONGO_MAX_POOL_SIZE = int(os.getenv("MONGO_MAX_POOL_SIZE", "20"))

# ── Chunking ──────────────────────────────────────────────────────────────────
# Overlap preserves context across boundaries so a sentence split down the middle
# still appears whole in one of the two neighbouring chunks.
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "900"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "150"))

# ── Retrieval ─────────────────────────────────────────────────────────────────
# How many passages to feed the model, and how wide the approximate-nearest-
# neighbour search casts before narrowing to that number.
RETRIEVAL_K = int(os.getenv("RETRIEVAL_K", "5"))
NUM_CANDIDATES = int(os.getenv("NUM_CANDIDATES", "100"))

# Name of the Atlas Vector Search index. Must be created in the Atlas UI or CLI —
# the driver cannot create search indexes on a free-tier cluster.
VECTOR_INDEX_NAME = os.getenv("VECTOR_INDEX_NAME", "vector_index")

# ── Grounding threshold ───────────────────────────────────────────────────────
# Hallucination mitigation. Atlas returns a cosine similarity mapped into [0, 1]
# as (1 + cosine) / 2, so 0.5 means "unrelated" and 1.0 means "identical". If the
# single best passage scores below this line, the corpus almost certainly does
# not cover the question, and we decline instead of asking the model to answer
# from weak context.
#
# Tune this against the real corpus before the pilot: too high and legitimate
# questions get refused, too low and the refusal never fires. Measure by logging
# top scores for a set of known-answerable and known-unanswerable questions.
#
# Changing this (or RETRIEVAL_K) invalidates cached answers via the answer
# cache key — no need to bump PROMPT_VERSION by hand for a threshold tweak.
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.62"))

# Shown to the user when retrieval falls below the threshold. Deliberately points
# at a human rather than guessing.
REFUSAL_MESSAGE = (
    "I don't have a policy document that covers that question, so I can't answer "
    "it without guessing. Please check with People Operations directly — and if "
    "this is something the handbook should cover, it's worth flagging to them."
)

# ── Concurrency ───────────────────────────────────────────────────────────────
# Size of the thread pool FastAPI uses to run sync routes and to iterate the SSE
# generator. Starlette calls next() on that generator through the pool, so a
# streaming request consumes roughly (generation duration) of thread-time. The
# pool size is therefore the throughput ceiling for chat.
#
# Measured on the stubbed load test (scripts/loadtest/, ~2.5s generations):
#
#     tokens   throughput
#         40      15 req/s   <- anyio default
#        160      54 req/s
#        320      99 req/s
#
# Roughly 0.31 req/s per thread, at about 105 KB of RSS per thread. Raise this
# if chat throughput is the constraint; see scripts/loadtest/RESULTS.md before
# changing it, and re-measure rather than guessing.
THREADPOOL_TOKENS = int(os.getenv("THREADPOOL_TOKENS", "100"))

# ── Caching ───────────────────────────────────────────────────────────────────
# The product premise is that the same questions get asked repeatedly, so the
# answer cache is the main lever on both cost and latency. Both caches live in
# MongoDB rather than Redis: they are shared across workers, survive restarts,
# and expire for free via TTL indexes, with no extra service to operate.
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "1") not in ("0", "false", "False")

# Answers are cached under a key that includes the corpus version, so
# re-ingestion invalidates every entry passively — there is no cache-clearing
# code to get wrong. Short TTL on top of that as a cheap staleness bound.
ANSWER_CACHE_TTL_SECONDS = int(os.getenv("ANSWER_CACHE_TTL_SECONDS", str(24 * 3600)))

# A query's embedding does not depend on the corpus, so these survive
# re-ingestion and can live much longer.
EMBEDDING_CACHE_TTL_SECONDS = int(os.getenv("EMBEDDING_CACHE_TTL_SECONDS", str(30 * 86400)))

# Bump this whenever ANSWER_SYSTEM_PROMPT changes. It is part of the answer
# cache key, so without a bump a prompt fix would keep serving pre-fix answers
# until the TTL expired.
PROMPT_VERSION = os.getenv("PROMPT_VERSION", "v1")

# ── Analytics ─────────────────────────────────────────────────────────────────
# Every chat request writes one query_logs record. This is the substrate for
# "learn from interaction patterns": refusals cluster into content gaps, and
# repeated questions rank into the FAQ list.
#
# At the 83 req/s target this collection grows by roughly 7M documents a day,
# so the TTL is mandatory rather than tidy-up.
QUERY_LOG_TTL_SECONDS = int(os.getenv("QUERY_LOG_TTL_SECONDS", str(90 * 86400)))

# ── Conversation limits ───────────────────────────────────────────────────────
# Turns of history replayed to the model, and turns used to rewrite a follow-up
# into a standalone retrieval query.
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "20"))
CONDENSE_TURNS = int(os.getenv("CONDENSE_TURNS", "6"))

# ── Escalation ────────────────────────────────────────────────────────────────
# When the assistant refuses, or an answer does not help, the employee can hand
# the question to a person. The record lands in the `escalations` collection
# and, if a webhook is configured, is posted there too so it reaches an inbox
# or a chat channel without anyone polling the database.
#
# Who the request goes to. Shown on the button in the UI and in the webhook text.
ESCALATION_CONTACT = os.getenv("ESCALATION_CONTACT", "People Operations")

# Optional. Any URL that accepts a JSON POST. The payload carries a top-level
# `text` field, so a Slack or Teams incoming webhook renders it without an
# adapter. Empty disables delivery; the record is still stored.
ESCALATION_WEBHOOK_URL = os.getenv("ESCALATION_WEBHOOK_URL", "")
ESCALATION_WEBHOOK_TIMEOUT_SECONDS = float(os.getenv("ESCALATION_WEBHOOK_TIMEOUT_SECONDS", "5"))
# Initial background attempt plus authenticated retries. Once this many
# attempts have been recorded, further retries are rejected.
ESCALATION_WEBHOOK_MAX_ATTEMPTS = int(os.getenv("ESCALATION_WEBHOOK_MAX_ATTEMPTS", "5"))

# Longest note an employee may attach. It is free text, so it is bounded.
ESCALATION_NOTE_MAX_LENGTH = int(os.getenv("ESCALATION_NOTE_MAX_LENGTH", "2000"))
