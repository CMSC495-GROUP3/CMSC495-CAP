"""Handing a question to a person."""
from datetime import datetime

import pytest
from pymongo.errors import DuplicateKeyError

import routes.escalations as escalations
from conftest import FAKE_DB, make_passages
from limiter import limiter


@pytest.fixture
def delivered(monkeypatch) -> list[dict]:
    """Capture webhook deliveries instead of making them."""
    sent: list[dict] = []
    monkeypatch.setattr(escalations, "deliver_escalation", lambda record: sent.append(record))
    return sent


@pytest.fixture
def refused(client, auth, retrieval, conversation) -> str:
    """A conversation whose one exchange was refused. Returns the session id."""
    retrieval.passages = make_passages(0.30)
    client.post("/api/chat", json={"question": "Can I bring my dog?", "session_id": conversation}, headers=auth)
    return conversation


@pytest.fixture
def answered(client, auth, retrieval, conversation) -> str:
    client.post("/api/chat", json={"question": "How much PTO?", "session_id": conversation}, headers=auth)
    return conversation


def _create(client, auth, session_id, index=1, reason="refused", **extra):
    return client.post(
        "/api/escalations",
        json={"session_id": session_id, "message_index": index, "reason": reason, **extra},
        headers=auth,
    )


class TestCreate:
    def test_requires_auth(self, client):
        assert client.post("/api/escalations", json={}).status_code in (401, 403)

    def test_records_the_refused_exchange_from_the_server_side_copy(self, client, auth, refused, delivered):
        response = _create(client, auth, refused, note="  It's for an assistance animal.  ")
        assert response.status_code == 200
        record = response.json()

        assert record["status"] == "open"
        assert record["reason"] == "refused"
        assert record["refused"] is True
        assert record["question"] == "Can I bring my dog?"
        assert record["note"] == "It's for an assistance animal."
        assert record["sources"] == [] and record["confidence"] == 30
        assert record["contact"] == "People Operations"
        assert record["resolution"] is None and record["resolved_at"] is None
        assert "_id" not in record

        stored = FAKE_DB["escalations"].find_one({"escalation_id": record["escalation_id"]})
        assert isinstance(stored["created_at"], datetime)

        message = FAKE_DB["conversations"].find_one({"session_id": refused})["messages"][1]
        assert message["escalation_id"] == record["escalation_id"]

        assert [d["escalation_id"] for d in delivered] == [record["escalation_id"]]

    def test_unhelpful_answer_carries_an_excerpt_and_sources(self, client, auth, answered, delivered):
        record = _create(client, auth, answered, reason="unhelpful").json()
        assert record["refused"] is False
        assert record["answer_excerpt"].startswith("Based on the policy documents")
        assert record["sources"] == ["Paid Time Off (PTO) Policy"]
        assert record["note"] is None

    def test_escalating_twice_returns_the_first_record(self, client, auth, refused, delivered):
        first = _create(client, auth, refused).json()
        second = _create(client, auth, refused, note="again").json()
        assert second["escalation_id"] == first["escalation_id"]
        assert FAKE_DB["escalations"].count_documents({}) == 1
        assert len(delivered) == 1

    def test_losing_a_race_returns_the_winner_without_a_second_webhook(self, client, auth, refused, delivered, monkeypatch):
        """Two requests pass the pre-check together and the unique index
        rejects the second insert. The fake enforces no indexes, so the
        collection below behaves as one where the winner landed in between."""
        winner = _create(client, auth, refused).json()
        # The loser read the conversation before the winner marked the message.
        FAKE_DB["conversations"].update_one(
            {"session_id": refused}, {"$set": {"messages.1.escalation_id": None}}
        )

        real = escalations.escalations_col

        class RacingCollection:
            checked = False

            def find_one(self, query, *args, **kwargs):
                if "message_index" in query and not self.checked:
                    self.checked = True  # pre-check: nothing filed yet
                    return None
                return real.find_one(query, *args, **kwargs)

            def insert_one(self, doc):
                raise DuplicateKeyError("E11000 duplicate key")  # winner landed

            def __getattr__(self, name):
                return getattr(real, name)

        monkeypatch.setattr(escalations, "escalations_col", RacingCollection())

        loser = _create(client, auth, refused, note="me too").json()
        assert loser["escalation_id"] == winner["escalation_id"]
        assert len(delivered) == 1

    def test_unknown_conversation(self, client, auth, delivered):
        assert _create(client, auth, "nope").status_code == 404

    def test_index_past_the_end(self, client, auth, refused, delivered):
        assert _create(client, auth, refused, index=5).status_code == 400

    def test_index_must_point_at_an_assistant_turn(self, client, auth, refused, delivered):
        FAKE_DB["conversations"].update_one(
            {"session_id": refused},
            {"$push": {"messages": {"role": "user", "content": "another"}}},
        )
        assert _create(client, auth, refused, index=2).status_code == 400
        assert _create(client, auth, refused, index=0).status_code == 422

    def test_reason_is_constrained(self, client, auth, refused, delivered):
        assert _create(client, auth, refused, reason="angry").status_code == 422

    def test_note_is_bounded(self, client, auth, refused, delivered):
        assert _create(client, auth, refused, note="x" * 2001).status_code == 422

    def test_rate_limited_per_client(self, client, auth, refused, delivered):
        limiter.enabled = True
        limiter.reset()
        statuses = [_create(client, auth, refused).status_code for _ in range(6)]
        assert statuses == [200] * 5 + [429]


class TestQueue:
    def test_lists_newest_first_with_filters(self, client, auth, retrieval, delivered):
        sessions = []
        for question in ("q1", "q2", "q3"):
            sid = client.post("/api/conversations", json={"title": question}, headers=auth).json()["session_id"]
            retrieval.passages = make_passages(0.30)
            client.post("/api/chat", json={"question": question, "session_id": sid}, headers=auth)
            sessions.append(sid)
        ids = [_create(client, auth, sid).json()["escalation_id"] for sid in sessions]

        client.patch(f"/api/escalations/{ids[1]}", json={"status": "resolved"}, headers=auth)

        everything = client.get("/api/escalations", headers=auth).json()
        assert everything["total"] == 3
        assert [e["escalation_id"] for e in everything["items"]] == ids[::-1]

        open_queue = client.get("/api/escalations", params={"status": "open"}, headers=auth).json()
        assert [e["escalation_id"] for e in open_queue["items"]] == [ids[2], ids[0]]

        mine = client.get("/api/escalations", params={"session_id": sessions[1]}, headers=auth).json()
        assert [e["escalation_id"] for e in mine["items"]] == [ids[1]]

        assert len(client.get("/api/escalations", params={"limit": 2}, headers=auth).json()["items"]) == 2
        assert client.get("/api/escalations", params={"status": "weird"}, headers=auth).status_code == 422

    def test_get_one(self, client, auth, refused, delivered):
        record = _create(client, auth, refused).json()
        assert client.get(f"/api/escalations/{record['escalation_id']}", headers=auth).json() == record
        assert client.get("/api/escalations/missing", headers=auth).status_code == 404

    def test_resolve_and_reopen(self, client, auth, refused, delivered):
        escalation_id = _create(client, auth, refused).json()["escalation_id"]

        resolved = client.patch(
            f"/api/escalations/{escalation_id}",
            json={"status": "resolved", "resolution": "Assistance animals are allowed; policy added."},
            headers=auth,
        ).json()
        assert resolved["status"] == "resolved"
        assert resolved["resolution"] == "Assistance animals are allowed; policy added."
        assert resolved["resolved_at"] is not None

        reopened = client.patch(f"/api/escalations/{escalation_id}", json={"status": "open"}, headers=auth).json()
        assert reopened["status"] == "open" and reopened["resolved_at"] is None
        assert reopened["resolution"] == resolved["resolution"]  # untouched when absent

        assert client.patch("/api/escalations/missing", json={"status": "open"}, headers=auth).status_code == 404
        assert client.patch(f"/api/escalations/{escalation_id}", json={"status": "closed"}, headers=auth).status_code == 422
