"""Webhook delivery: best effort, never raising."""

import json
import urllib.error
from datetime import UTC, datetime

from policy_assistant.api import notify
from policy_assistant.api.notify import deliver_escalation, format_summary

RECORD = {
    "escalation_id": "abcdef0123456789",
    "status": "open",
    "reason": "refused",
    "contact": "People Operations",
    "session_id": "s",
    "message_index": 1,
    "question": "Can I bring my dog?",
    "answer_excerpt": "I don't have a policy...",
    "refused": True,
    "confidence": 30,
    "sources": [],
    "note": "Assistance animal.",
    "created_at": datetime(2026, 8, 30, tzinfo=UTC),
}


class _Response:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_summary_for_a_refusal():
    text = format_summary(RECORD)
    assert text.splitlines() == [
        "Policy question escalated to People Operations (ref abcdef01)",
        "Assistant declined to answer.",
        "Question: Can I bring my dog?",
        "Employee note: Assistance animal.",
    ]


def test_summary_for_an_unhelpful_answer_quotes_it_and_its_sources():
    record = {
        **RECORD,
        "refused": False,
        "reason": "unhelpful",
        "note": None,
        "answer_excerpt": "x" * 400,
        "sources": ["Doc A", "Doc B"],
    }
    lines = format_summary(record).splitlines()
    assert lines[1] == "Answer did not help."
    assert lines[3] == "Assistant said: " + "x" * 300
    assert lines[4] == "Cited: Doc A, Doc B"


def test_no_url_means_no_delivery(monkeypatch):
    monkeypatch.setattr(notify, "ESCALATION_WEBHOOK_URL", "")
    monkeypatch.setattr(
        notify.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")),
    )
    assert deliver_escalation(RECORD) is False


def test_posts_json_with_a_text_field(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["body"] = json.loads(request.data)
        seen["content_type"] = request.get_header("Content-type")
        return _Response(200)

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    assert deliver_escalation(RECORD, webhook_url="https://hooks.example/abc") is True
    assert seen["url"] == "https://hooks.example/abc"
    assert seen["content_type"] == "application/json"
    assert seen["body"]["text"].startswith("Policy question escalated")
    assert seen["body"]["escalation"]["escalation_id"] == "abcdef0123456789"
    assert seen["body"]["escalation"]["created_at"] == "2026-08-30 00:00:00+00:00"


def test_network_failure_is_logged_not_raised(monkeypatch, caplog):
    def down(*args, **kwargs):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(notify.urllib.request, "urlopen", down)
    assert deliver_escalation(RECORD, webhook_url="https://hooks.example/abc") is False
    assert "webhook delivery failed" in caplog.text


def test_non_2xx_is_a_failure(monkeypatch, caplog):
    def rejected(*args, **kwargs):
        raise urllib.error.HTTPError("https://hooks.example/abc", 500, "boom", {}, None)

    monkeypatch.setattr(notify.urllib.request, "urlopen", rejected)
    assert deliver_escalation(RECORD, webhook_url="https://hooks.example/abc") is False
    assert "returned 500" in caplog.text
