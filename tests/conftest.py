"""Test harness: the real application with its external services stubbed.

Same approach as scripts/loadtest/server.py, and it shares that script's
in-memory Mongo. Everything that would reach the network is replaced before
`main` is imported, because db.py binds collection handles at import:

- MongoDB      -> FakeDB from scripts/loadtest/fakemongo.py
- the model    -> LLM_PROVIDER=fake with every delay set to zero
- vector search-> `retrieval` fixture, which returns whatever a test hands it
- indexes      -> skipped; they would try to reach a cluster

No test-only branch exists in policy_assistant/. If something cannot be stubbed
from here, that is a design problem to fix in the application, not in tests.
"""

import os
from datetime import timedelta

import pytest

# main.py and llm.py read these at import, so they are set before either loads.
os.environ["JWT_SECRET_KEY"] = "test-secret-not-for-real-use"
os.environ["MONGODB_URI"] = "mongodb://stubbed-never-contacted"
os.environ["LLM_PROVIDER"] = "fake"
os.environ["FAKE_STREAM_DELAY_MS"] = "0"
os.environ["FAKE_UTILITY_DELAY_MS"] = "0"
os.environ["FAKE_EMBED_DELAY_MS"] = "0"
os.environ["CACHE_ENABLED"] = "1"
os.environ.pop("APP_ENV", None)  # FakeProvider refuses to run as production

TEST_PASSWORD = "correct-horse-battery-staple"

import bcrypt  # noqa: E402

os.environ["APP_PASSWORD_HASH"] = bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt(4)).decode()

from policy_assistant.rag import mongo  # noqa: E402
from scripts.loadtest.fakemongo import FakeDB  # noqa: E402

FAKE_DB = FakeDB()
mongo.get_db = lambda: FAKE_DB
mongo.get_collection = lambda name: FAKE_DB[name]

from policy_assistant.rag import cache  # noqa: E402

cache.get_collection = mongo.get_collection

from policy_assistant.api import main  # noqa: E402

main.ensure_indexes = lambda: None

from fastapi.testclient import TestClient  # noqa: E402

from policy_assistant.api.limiter import limiter  # noqa: E402
from policy_assistant.api.routes import chat as chat_routes  # noqa: E402
from policy_assistant.api.routes.auth import create_access_token  # noqa: E402

# ── Data helpers ──────────────────────────────────────────────────────────────


def make_passages(*scores: float, title: str = "Paid Time Off (PTO) Policy") -> list[dict]:
    """Canned retrieval results carrying the given similarity scores."""
    return [
        {
            "source": "documents/pto-policy.md",
            "doc_id": "pto-policy",
            "title": title,
            "category": "Time Off & Leave",
            "effective_date": "2026-01-01",
            "chunk_index": i,
            "score": score,
            "text": f"Passage {i}: employees accrue 15 days of PTO per year.",
        }
        for i, score in enumerate(scores)
    ]


def sse_events(body: str) -> list[dict]:
    """Parse a text/event-stream body into its JSON payloads, in order."""
    import json

    return [
        json.loads(line[len("data: ") :]) for line in body.split("\n") if line.startswith("data: ")
    ]


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_db():
    """Empty every collection in place. The handles in db.py were bound at
    import, so the collection objects must survive; only their contents go."""
    for collection in FAKE_DB._collections.values():
        collection._docs.clear()
    yield


@pytest.fixture(autouse=True)
def no_rate_limit():
    """Rate limits are per process and would trip across tests. A dedicated
    test re-enables the limiter to check it."""
    limiter.enabled = False
    yield
    limiter.enabled = False


@pytest.fixture(scope="session")
def client():
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def auth() -> dict:
    token = create_access_token({"sub": "user"}, timedelta(hours=1))
    return {"Authorization": f"Bearer {token}"}


class Retrieval:
    """Controls what vector search returns and counts how often it is asked."""

    def __init__(self):
        self.passages: list[dict] = make_passages(0.80, 0.75, 0.70)
        self.calls: list[str] = []

    def __call__(self, query: str, k: int = 5) -> list[dict]:
        self.calls.append(query)
        return [dict(p) for p in self.passages[:k]]


@pytest.fixture
def retrieval(monkeypatch) -> Retrieval:
    stub = Retrieval()
    monkeypatch.setattr(chat_routes, "retrieve_passages", stub)
    return stub


@pytest.fixture
def conversation(client, auth) -> str:
    """A fresh, empty conversation. Returns its session_id."""
    return client.post("/api/conversations", json={"title": "test"}, headers=auth).json()[
        "session_id"
    ]
