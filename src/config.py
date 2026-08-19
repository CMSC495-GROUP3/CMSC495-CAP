"""Central configuration for the retrieval and generation pipeline.

This is the single source of truth for tuning knobs that both the ingestion
scripts (src/) and the API (backend/) need to agree on. The backend reaches
this module because it adds src/ to sys.path at import time.

Every value can be overridden with an environment variable so the pilot can be
retuned without a code change.
"""
import os

# ── Product identity ──────────────────────────────────────────────────────────
# The product name is still pending. Change it here and in frontend/src/config.ts
# (plus the <title> in frontend/index.html) to rebrand the whole application.
APP_NAME = os.getenv("APP_NAME", "Policy Assistant")

# ── Storage ───────────────────────────────────────────────────────────────────
# S3 key prefix the ingestion pipeline reads from and the seed script writes to.
S3_DOCUMENT_PREFIX = os.getenv("S3_DOCUMENT_PREFIX", "documents/")

# MongoDB collection holding one record per passage: text + metadata + embedding.
# Keeping all three in one collection is the design decision described in the
# proposal — it avoids pairing a vector store with a separate document store.
PASSAGES_COLLECTION = os.getenv("PASSAGES_COLLECTION", "passages")

# Connections held per process. Total load on the cluster is
# (uvicorn workers x this), which must stay under the Atlas connection cap —
# see the arithmetic in src/mongo.py before raising either number.
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

# ── Conversation limits ───────────────────────────────────────────────────────
# Turns of history replayed to the model, and turns used to rewrite a follow-up
# into a standalone retrieval query.
HISTORY_TURNS = int(os.getenv("HISTORY_TURNS", "20"))
CONDENSE_TURNS = int(os.getenv("CONDENSE_TURNS", "6"))
