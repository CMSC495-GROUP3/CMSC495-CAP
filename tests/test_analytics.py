"""Query logging: one record per request, never raising."""

from conftest import FAKE_DB, make_passages

from policy_assistant.api import analytics
from policy_assistant.api.analytics import MAX_QUESTION_LENGTH, log_query
from policy_assistant.rag.cache import question_hash


def _log(**overrides):
    fields = dict(
        session_id="s1",
        question="How much PTO do I get?",
        condensed_question="How much PTO do I get?",
        passages=make_passages(0.80, 0.60),
        refused=False,
        sources=["PTO Policy"],
        cache_hit=None,
        latency_ms=120,
    )
    fields.update(overrides)
    log_query(**fields)


def test_records_scores_and_outcome():
    _log()
    record = FAKE_DB["query_logs"].find_one({})
    assert record["best_score"] == 0.80
    assert record["mean_score"] == 0.70
    assert record["passage_count"] == 2
    assert record["refused"] is False
    assert record["sources"] == ["PTO Policy"]
    assert record["latency_ms"] == 120
    assert record["question_hash"] == question_hash("how much  pto do i get?")


def test_cache_hits_have_no_scores():
    _log(passages=[], cache_hit="answer")
    record = FAKE_DB["query_logs"].find_one({})
    assert record["best_score"] is None and record["mean_score"] is None
    assert record["cache_hit"] == "answer"


def test_questions_are_truncated():
    _log(question="x" * 1000, condensed_question="y" * 1000)
    record = FAKE_DB["query_logs"].find_one({})
    assert len(record["question_raw"]) == MAX_QUESTION_LENGTH
    assert len(record["question_condensed"]) == MAX_QUESTION_LENGTH


class _Broken:
    def insert_one(self, *_):
        raise ConnectionError("cluster unreachable")


def test_logging_failure_is_swallowed(monkeypatch, caplog):
    monkeypatch.setattr(analytics, "query_logs_col", _Broken())
    _log()  # must not raise
    assert "Failed to write query log" in caplog.text
