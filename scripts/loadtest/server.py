"""Load-test server: the real application with its external services stubbed.

Run it:

    ./.venv/bin/uvicorn scripts.loadtest.server:app --port 8001
    ./.venv/bin/uvicorn scripts.loadtest.server:app --port 8001 --workers 4

## What this measures, and what it does not

It measures the **concurrency architecture** — how many simultaneous SSE
streams one process can serve, and what happens to latency past that point.
That is the property the async conversion changes, and it is reproducible on a
laptop with no cloud account.

It does **not** measure end-to-end production latency. Stubbed out:

- **The model.** `LLM_PROVIDER=fake` generates a canned answer at a configurable
  per-token delay, so a request occupies a worker for a realistic duration
  without any spend. Real OpenAI latency has a long tail this does not
  reproduce.
- **MongoDB.** Replaced with an in-memory dict plus a fixed simulated latency.
  Real Atlas round-trips vary and would themselves consume threads.
- **Atlas Vector Search.** Replaced with canned passages. `$vectorSearch` is an
  Atlas-only aggregation stage and cannot run locally at all.

The stubs live in this file rather than behind flags in `src/` so that no
test-only branch can ever be reached in production.

## Reading the gate

Fake embeddings are meaningless, so a similarity score computed from them is
noise. Rather than fake the gate, bracket it — run the whole test twice:

    SIMILARITY_THRESHOLD=0.0   every request generates      (worst case)
    SIMILARITY_THRESHOLD=1.0   every request refuses        (best case)

Real traffic sits between the two, weighted by the actual refusal rate.
"""
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "backend"))
sys.path.insert(0, str(_ROOT / "src"))

# Must be set before importing main, which validates them at import.
os.environ.setdefault("JWT_SECRET_KEY", "loadtest-secret-not-for-real-use")
os.environ.setdefault("MONGODB_URI", "mongodb://stubbed-never-contacted")
os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.pop("APP_ENV", None)  # FakeProvider refuses to run as production

# Simulated database round-trip. Real Atlas is slower and more variable; this
# at least stops the stub from being free.
DB_LATENCY_MS = int(os.getenv("FAKE_DB_LATENCY_MS", "15"))

# Score attached to every canned passage. Compared against SIMILARITY_THRESHOLD
# by the real, unmodified grounding gate.
PASSAGE_SCORE = float(os.getenv("FAKE_PASSAGE_SCORE", "0.78"))

CANNED_PASSAGES = [
    {
        "source": "documents/pto-policy.md",
        "doc_id": "pto-policy",
        "title": "Paid Time Off (PTO) Policy",
        "category": "Time Off & Leave",
        "effective_date": "2026-01-01",
        "chunk_index": i,
        "score": PASSAGE_SCORE - (i * 0.01),
        "text": (
            "Full-time employees accrue paid time off each pay period based on "
            "length of service. Employees with up to two years of service accrue "
            "15 days per year, rising to 20 days from year three and 25 days from "
            "year six. Accrual begins on the first day of employment and there is "
            "no waiting period before accrued time may be used. " * 2
        ),
    }
    for i in range(5)
]


def _sleep_db() -> None:
    if DB_LATENCY_MS > 0:
        time.sleep(DB_LATENCY_MS / 1000)


class InMemoryConversations:
    """Stands in for `conversations_col`.

    Implements only the two operations the chat path uses. Deliberately not a
    general Mongo emulator — a partial fake that is obviously partial is safer
    than one that looks complete and diverges under load.
    """

    def __init__(self) -> None:
        self._docs: dict[str, dict] = {}

    def find_one(self, filt: dict, projection: dict | None = None) -> dict | None:
        _sleep_db()
        return self._docs.get(filt.get("session_id"))

    def update_one(self, filt: dict, update: dict, upsert: bool = False) -> None:
        _sleep_db()
        session_id = filt.get("session_id")
        doc = self._docs.get(session_id)
        if doc is None:
            if not upsert:
                return
            doc = {"session_id": session_id, "messages": []}
            self._docs[session_id] = doc

        for field, value in update.get("$set", {}).items():
            doc[field] = value
        for field, spec in update.get("$push", {}).items():
            doc.setdefault(field, []).extend(spec["$each"] if "$each" in spec else [spec])


def _fake_retrieve(query: str, k: int = 5) -> list[dict]:
    """Stand in for Atlas Vector Search, at roughly its latency."""
    _sleep_db()
    return [dict(p) for p in CANNED_PASSAGES[:k]]


import main  # noqa: E402
import routes.chat as chat_routes  # noqa: E402

def _startup() -> None:
    """Replaces ensure_indexes in the lifespan.

    Index creation would try to reach a real cluster. This also doubles as the
    hook for resizing the thread pool, because lifespan runs inside the event
    loop and `current_default_thread_limiter()` raises outside one.
    """
    tokens = int(os.getenv("LOADTEST_THREAD_TOKENS", "0"))
    if tokens > 0:
        import anyio.to_thread

        anyio.to_thread.current_default_thread_limiter().total_tokens = tokens


# Index creation would try to reach a real cluster during startup.
main.ensure_indexes = _startup

# routes/chat.py binds these names at import, so patch them there rather than
# on the modules they came from.
chat_routes.conversations_col = InMemoryConversations()
chat_routes.retrieve_passages = _fake_retrieve

app = main.app
