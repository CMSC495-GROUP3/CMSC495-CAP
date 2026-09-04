"""Handing a question to a person."""

import json
import os
import subprocess
import sys
import urllib.error
from datetime import UTC, datetime, timedelta

import pytest
from conftest import FAKE_DB, make_passages
from pymongo.errors import DuplicateKeyError

from policy_assistant.api import notify
from policy_assistant.api.limiter import limiter
from policy_assistant.api.routes import escalations as escalations


@pytest.fixture
def delivered(monkeypatch) -> list[dict]:
    """Capture scheduled deliveries without touching delivery status fields."""
    sent: list[dict] = []

    def capture(record):
        sent.append(record)

    monkeypatch.setattr(escalations, "_deliver_in_background", capture)
    return sent


class _FakeWebhookResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


WEBHOOK_URL = "https://hooks.example/test-receiver"


@pytest.fixture
def refused(client, auth, retrieval, conversation) -> str:
    """A conversation whose one exchange was refused. Returns the session id."""
    retrieval.passages = make_passages(0.30)
    client.post(
        "/api/chat",
        json={"question": "Can I bring my dog?", "session_id": conversation},
        headers=auth,
    )
    return conversation


@pytest.fixture
def answered(client, auth, retrieval, conversation) -> str:
    client.post(
        "/api/chat", json={"question": "How much PTO?", "session_id": conversation}, headers=auth
    )
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

    def test_records_the_refused_exchange_from_the_server_side_copy(
        self, client, auth, refused, delivered
    ):
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

    def test_unhelpful_answer_carries_an_excerpt_and_sources(
        self, client, auth, answered, delivered
    ):
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

    def test_losing_a_race_returns_the_winner_without_a_second_webhook(
        self, client, auth, refused, delivered, monkeypatch
    ):
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
            sid = client.post("/api/conversations", json={"title": question}, headers=auth).json()[
                "session_id"
            ]
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

        mine = client.get(
            "/api/escalations", params={"session_id": sessions[1]}, headers=auth
        ).json()
        assert [e["escalation_id"] for e in mine["items"]] == [ids[1]]

        assert (
            len(client.get("/api/escalations", params={"limit": 2}, headers=auth).json()["items"])
            == 2
        )
        assert (
            client.get("/api/escalations", params={"status": "weird"}, headers=auth).status_code
            == 422
        )

    def test_get_one(self, client, auth, refused, delivered):
        record = _create(client, auth, refused).json()
        assert (
            client.get(f"/api/escalations/{record['escalation_id']}", headers=auth).json() == record
        )
        assert client.get("/api/escalations/missing", headers=auth).status_code == 404

    def test_resolve_and_reopen(self, client, auth, refused, delivered):
        escalation_id = _create(client, auth, refused).json()["escalation_id"]

        resolved = client.patch(
            f"/api/escalations/{escalation_id}",
            json={
                "status": "resolved",
                "resolution": "Assistance animals are allowed; policy added.",
            },
            headers=auth,
        ).json()
        assert resolved["status"] == "resolved"
        assert resolved["resolution"] == "Assistance animals are allowed; policy added."
        assert resolved["resolved_at"] is not None

        reopened = client.patch(
            f"/api/escalations/{escalation_id}", json={"status": "open"}, headers=auth
        ).json()
        assert reopened["status"] == "open" and reopened["resolved_at"] is None
        assert reopened["resolution"] == resolved["resolution"]  # untouched when absent

        assert (
            client.patch(
                "/api/escalations/missing", json={"status": "open"}, headers=auth
            ).status_code
            == 404
        )
        assert (
            client.patch(
                f"/api/escalations/{escalation_id}", json={"status": "closed"}, headers=auth
            ).status_code
            == 422
        )


class TestDeliveryStatus:
    """Synthetic webhook receivers only — never contact a real endpoint."""

    def test_create_starts_pending_and_success_marks_delivered(
        self, client, auth, refused, monkeypatch
    ):
        monkeypatch.setattr(notify, "ESCALATION_WEBHOOK_URL", WEBHOOK_URL)
        monkeypatch.setattr(
            notify.urllib.request, "urlopen", lambda *a, **k: _FakeWebhookResponse()
        )

        response = _create(client, auth, refused)
        assert response.status_code == 200
        record = response.json()
        assert record["delivery_status"] == "pending"
        assert record["delivery_attempts"] == 0
        assert record["delivery_last_attempt_at"] is None
        assert WEBHOOK_URL not in response.text

        stored = FAKE_DB["escalations"].find_one({"escalation_id": record["escalation_id"]})
        assert stored["delivery_status"] == "delivered"
        assert stored["delivery_attempts"] == 1
        assert isinstance(stored["delivery_last_attempt_at"], datetime)
        assert WEBHOOK_URL not in json.dumps(stored, default=str)

    def test_failed_delivery_is_recorded_without_delaying_create(
        self, client, auth, refused, monkeypatch, caplog
    ):
        monkeypatch.setattr(notify, "ESCALATION_WEBHOOK_URL", WEBHOOK_URL)

        def down(*args, **kwargs):
            raise urllib.error.URLError("refused")

        monkeypatch.setattr(notify.urllib.request, "urlopen", down)

        response = _create(client, auth, refused)
        assert response.status_code == 200
        record = response.json()
        assert record["delivery_status"] == "pending"
        assert WEBHOOK_URL not in response.text
        assert WEBHOOK_URL not in caplog.text

        stored = FAKE_DB["escalations"].find_one({"escalation_id": record["escalation_id"]})
        assert stored["delivery_status"] == "failed"
        assert stored["delivery_attempts"] == 1

    def test_empty_webhook_leaves_pending_without_an_attempt(
        self, client, auth, refused, monkeypatch
    ):
        """No receiver configured means store-only, not a failed delivery."""
        monkeypatch.setattr(notify, "ESCALATION_WEBHOOK_URL", "")

        response = _create(client, auth, refused)
        assert response.status_code == 200
        record = response.json()
        assert record["delivery_status"] == "pending"
        assert record["delivery_attempts"] == 0

        stored = FAKE_DB["escalations"].find_one({"escalation_id": record["escalation_id"]})
        assert stored["delivery_status"] == "pending"
        assert stored["delivery_attempts"] == 0
        assert stored["delivery_last_attempt_at"] is None

    def test_retry_failed_delivery_until_success(self, client, auth, refused, monkeypatch):
        monkeypatch.setattr(notify, "ESCALATION_WEBHOOK_URL", WEBHOOK_URL)
        monkeypatch.setattr(
            notify.urllib.request,
            "urlopen",
            lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("down")),
        )
        escalation_id = _create(client, auth, refused).json()["escalation_id"]
        assert (
            FAKE_DB["escalations"].find_one({"escalation_id": escalation_id})["delivery_status"]
            == "failed"
        )

        monkeypatch.setattr(
            notify.urllib.request, "urlopen", lambda *a, **k: _FakeWebhookResponse()
        )
        retried = client.post(f"/api/escalations/{escalation_id}/retry-delivery", headers=auth)
        assert retried.status_code == 200
        body = retried.json()
        assert body["delivery_status"] == "delivered"
        assert body["delivery_attempts"] == 2
        assert WEBHOOK_URL not in retried.text

    def test_retry_requires_auth(self, client, auth, refused, monkeypatch):
        monkeypatch.setattr(notify, "ESCALATION_WEBHOOK_URL", WEBHOOK_URL)
        monkeypatch.setattr(
            notify.urllib.request,
            "urlopen",
            lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("down")),
        )
        escalation_id = _create(client, auth, refused).json()["escalation_id"]
        assert client.post(f"/api/escalations/{escalation_id}/retry-delivery").status_code in (
            401,
            403,
        )

    def test_retry_rejects_delivered_and_exhausted_attempts(
        self, client, auth, refused, monkeypatch
    ):
        monkeypatch.setattr(notify, "ESCALATION_WEBHOOK_URL", WEBHOOK_URL)
        monkeypatch.setattr(
            notify.urllib.request, "urlopen", lambda *a, **k: _FakeWebhookResponse()
        )
        escalation_id = _create(client, auth, refused).json()["escalation_id"]
        assert (
            client.post(
                f"/api/escalations/{escalation_id}/retry-delivery", headers=auth
            ).status_code
            == 409
        )

        FAKE_DB["escalations"].update_one(
            {"escalation_id": escalation_id},
            {
                "$set": {
                    "delivery_status": "failed",
                    "delivery_attempts": escalations.ESCALATION_WEBHOOK_MAX_ATTEMPTS,
                }
            },
        )
        exhausted = client.post(f"/api/escalations/{escalation_id}/retry-delivery", headers=auth)
        assert exhausted.status_code == 409
        assert "Maximum delivery attempts" in exhausted.json()["detail"]
        assert WEBHOOK_URL not in exhausted.text

    def test_concurrent_retry_only_one_sends(self, client, auth, refused, monkeypatch):
        monkeypatch.setattr(notify, "ESCALATION_WEBHOOK_URL", WEBHOOK_URL)
        monkeypatch.setattr(
            notify.urllib.request,
            "urlopen",
            lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("down")),
        )
        escalation_id = _create(client, auth, refused).json()["escalation_id"]

        sends: list[dict] = []
        losing_claims: list[dict | None] = []

        def fake_deliver(record, webhook_url=None):
            sends.append(record)
            # The active request still owns a pending claim here. A competing
            # worker must lose before it reaches the webhook.
            losing_claims.append(escalations._claim_delivery(escalation_id))
            return True

        monkeypatch.setattr(notify, "deliver_escalation", fake_deliver)

        first = client.post(f"/api/escalations/{escalation_id}/retry-delivery", headers=auth)
        second = client.post(f"/api/escalations/{escalation_id}/retry-delivery", headers=auth)
        assert first.status_code == 200
        assert first.json()["delivery_status"] == "delivered"
        assert second.status_code == 409
        assert losing_claims == [None]
        assert len(sends) == 1

    def test_unconfigured_pending_delivery_can_be_claimed_after_configuration(
        self, client, auth, refused, monkeypatch
    ):
        monkeypatch.setattr(notify, "ESCALATION_WEBHOOK_URL", "")
        escalation_id = _create(client, auth, refused).json()["escalation_id"]

        monkeypatch.setattr(notify, "ESCALATION_WEBHOOK_URL", WEBHOOK_URL)
        monkeypatch.setattr(notify, "deliver_escalation", lambda _record: True)
        retried = client.post(f"/api/escalations/{escalation_id}/retry-delivery", headers=auth)

        assert retried.status_code == 200
        assert retried.json()["delivery_status"] == "delivered"
        assert retried.json()["delivery_attempts"] == 1

    def test_retry_without_webhook_does_not_mutate_store_only_record(
        self, client, auth, refused, monkeypatch
    ):
        monkeypatch.setattr(notify, "ESCALATION_WEBHOOK_URL", "")
        escalation_id = _create(client, auth, refused).json()["escalation_id"]

        retried = client.post(f"/api/escalations/{escalation_id}/retry-delivery", headers=auth)

        assert retried.status_code == 409
        assert retried.json()["detail"] == "Webhook delivery is not configured."
        stored = FAKE_DB["escalations"].find_one({"escalation_id": escalation_id})
        assert stored["delivery_status"] == "pending"
        assert stored["delivery_attempts"] == 0
        assert stored["delivery_claimed_at"] is None

    def test_stale_pending_claim_can_be_recovered(self, client, auth, refused, monkeypatch):
        monkeypatch.setattr(notify, "ESCALATION_WEBHOOK_URL", "")
        escalation_id = _create(client, auth, refused).json()["escalation_id"]
        FAKE_DB["escalations"].update_one(
            {"escalation_id": escalation_id},
            {
                "$set": {
                    "delivery_claimed_at": datetime.now(UTC)
                    - timedelta(seconds=escalations.ESCALATION_WEBHOOK_LEASE_SECONDS + 1)
                }
            },
        )
        monkeypatch.setattr(notify, "ESCALATION_WEBHOOK_URL", WEBHOOK_URL)
        monkeypatch.setattr(notify, "deliver_escalation", lambda _record: True)

        retried = client.post(f"/api/escalations/{escalation_id}/retry-delivery", headers=auth)
        assert retried.status_code == 200
        assert retried.json()["delivery_status"] == "delivered"

    def test_stale_worker_cannot_complete_after_claim_is_recovered(
        self, client, auth, refused, monkeypatch
    ):
        monkeypatch.setattr(notify, "ESCALATION_WEBHOOK_URL", "")
        escalation_id = _create(client, auth, refused).json()["escalation_id"]

        first = escalations._claim_delivery(escalation_id)
        assert first is not None
        first_claimed_at = datetime.now(UTC) - timedelta(
            seconds=escalations.ESCALATION_WEBHOOK_LEASE_SECONDS + 1
        )
        FAKE_DB["escalations"].update_one(
            {"escalation_id": escalation_id},
            {"$set": {"delivery_claimed_at": first_claimed_at}},
        )

        second = escalations._claim_delivery(escalation_id)
        assert second is not None
        second_claimed_at = second["delivery_claimed_at"]
        assert second_claimed_at != first_claimed_at

        assert escalations._apply_delivery_result(escalation_id, first_claimed_at, False) is None
        current = FAKE_DB["escalations"].find_one({"escalation_id": escalation_id})
        assert current["delivery_status"] == "pending"
        assert current["delivery_claimed_at"] == second_claimed_at
        assert current["delivery_attempts"] == 0

        updated = escalations._apply_delivery_result(escalation_id, second_claimed_at, True)
        assert updated is not None
        assert updated["delivery_status"] == "delivered"
        assert updated["delivery_attempts"] == 1

    @pytest.mark.parametrize(
        ("timeout", "lease"),
        [("30", "30"), ("NaN", "30"), ("Infinity", "30"), ("0", "30"), ("5", "0")],
    )
    def test_webhook_timing_must_be_positive_finite_and_lease_longer(self, timeout, lease):
        env = os.environ.copy()
        env["ESCALATION_WEBHOOK_TIMEOUT_SECONDS"] = timeout
        env["ESCALATION_WEBHOOK_LEASE_SECONDS"] = lease

        result = subprocess.run(
            [sys.executable, "-c", "from policy_assistant.rag import config"],
            cwd=os.getcwd(),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode != 0
        assert "ESCALATION_WEBHOOK_" in result.stderr

    def test_legacy_record_without_delivery_fields_can_be_claimed(
        self, client, auth, refused, monkeypatch
    ):
        monkeypatch.setattr(notify, "ESCALATION_WEBHOOK_URL", "")
        escalation_id = _create(client, auth, refused).json()["escalation_id"]
        stored = next(
            doc for doc in FAKE_DB["escalations"]._docs if doc["escalation_id"] == escalation_id
        )
        for field in (
            "delivery_status",
            "delivery_attempts",
            "delivery_last_attempt_at",
            "delivery_claimed_at",
        ):
            stored.pop(field, None)

        monkeypatch.setattr(notify, "ESCALATION_WEBHOOK_URL", WEBHOOK_URL)
        monkeypatch.setattr(notify, "deliver_escalation", lambda _record: True)
        retried = client.post(f"/api/escalations/{escalation_id}/retry-delivery", headers=auth)

        assert retried.status_code == 200
        assert retried.json()["delivery_status"] == "delivered"
        assert retried.json()["delivery_attempts"] == 1

    def test_missing_escalation_retry_is_404(self, client, auth):
        assert (
            client.post("/api/escalations/missing/retry-delivery", headers=auth).status_code == 404
        )
