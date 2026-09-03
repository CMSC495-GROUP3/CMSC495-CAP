"""Regression tests for the labeled AI evaluation set and its metrics."""

from pathlib import Path

import pytest

from policy_assistant.rag.evaluation import load_cases, score_results

DATASET = Path(__file__).resolve().parent.parent / "evaluation" / "questions.json"


def test_dataset_has_required_twenty_case_mix():
    cases = load_cases(DATASET)

    assert len(cases) == 20
    assert len({case["id"] for case in cases}) == 20
    assert {
        category: sum(case["category"] == category for case in cases)
        for category in {case["category"] for case in cases}
    } == {
        "answerable": 10,
        "unanswerable": 4,
        "ambiguous": 3,
        "prompt_injection": 3,
    }


def test_every_case_records_expected_source_and_behavior():
    cases = load_cases(DATASET)

    for case in cases:
        assert case["question"].strip()
        assert isinstance(case["expected_sources"], list)
        assert case["expected_behavior"].strip()
        assert case["expected_outcome"] in {"answer", "clarify", "refuse"}


def test_loader_rejects_duplicate_ids(tmp_path):
    dataset = tmp_path / "duplicate.json"
    dataset.write_text(
        """[
          {"id":"q1","category":"answerable","question":"One?","expected_sources":["A"],"expected_outcome":"answer","expected_behavior":"Answer."},
          {"id":"q1","category":"unanswerable","question":"Two?","expected_sources":[],"expected_outcome":"refuse","expected_behavior":"Refuse."}
        ]""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate evaluation case id"):
        load_cases(dataset)


def test_score_results_reports_required_metrics():
    cases = [
        {
            "id": "a1",
            "category": "answerable",
            "expected_sources": ["Policy A"],
            "expected_outcome": "answer",
        },
        {
            "id": "a2",
            "category": "answerable",
            "expected_sources": ["Policy B"],
            "expected_outcome": "answer",
        },
        {
            "id": "u1",
            "category": "unanswerable",
            "expected_sources": [],
            "expected_outcome": "refuse",
        },
    ]
    results = [
        {
            "id": "a1",
            "retrieved_sources": ["Policy A", "Policy X"],
            "cited_sources": ["Policy A"],
            "refused": False,
        },
        {
            "id": "a2",
            "retrieved_sources": ["Policy X"],
            "cited_sources": ["Policy X"],
            "refused": True,
        },
        {
            "id": "u1",
            "retrieved_sources": [],
            "cited_sources": [],
            "refused": True,
        },
    ]

    report = score_results(cases, results)

    assert report["recall_at_5"] == 50.0
    assert report["citation_correctness"] == 50.0
    assert report["grounded_answer_rate"] == 50.0
    assert report["refusal_handling"] == 100.0
    assert report["evaluated_cases"] == 3


def test_score_results_uses_none_when_a_metric_has_no_eligible_cases():
    cases = [
        {
            "id": "amb1",
            "category": "ambiguous",
            "expected_sources": ["Policy A"],
            "expected_outcome": "clarify",
        }
    ]
    results = [
        {
            "id": "amb1",
            "retrieved_sources": ["Policy A"],
            "cited_sources": ["Policy A"],
            "refused": False,
        }
    ]

    report = score_results(cases, results)

    assert report["recall_at_5"] is None
    assert report["citation_correctness"] is None
    assert report["grounded_answer_rate"] is None
    assert report["refusal_handling"] is None
