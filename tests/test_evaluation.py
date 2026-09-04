"""Regression tests for the labeled AI evaluation set and its metrics."""

from pathlib import Path

import pytest

from policy_assistant.rag.evaluation import (
    extract_answer_citations,
    load_cases,
    score_results,
)

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


def test_extract_answer_citations_finds_known_titles_in_answer_order():
    answer = (
        "Per the Remote and Hybrid Work Policy, Tuesday is an anchor day. "
        "The Paid Time Off (PTO) Policy also applies."
    )
    known = [
        "Paid Time Off (PTO) Policy",
        "Remote and Hybrid Work Policy",
        "Parental Leave Policy",
    ]

    assert extract_answer_citations(answer, known) == [
        "Remote and Hybrid Work Policy",
        "Paid Time Off (PTO) Policy",
    ]


def test_extract_answer_citations_is_case_insensitive_and_ignores_unknown_titles():
    answer = "According to the paid time off (pto) policy, you receive 15 days."
    known = ["Paid Time Off (PTO) Policy", "Code of Conduct"]

    assert extract_answer_citations(answer, known) == ["Paid Time Off (PTO) Policy"]
    assert extract_answer_citations(answer, ["Code of Conduct"]) == []
    assert extract_answer_citations("", known) == []


def test_citation_correctness_fails_when_answer_omits_retrieved_policy():
    """Retrieval can succeed while the answer never names the expected policy."""
    cases = [
        {
            "id": "a1",
            "category": "answerable",
            "expected_sources": ["Paid Time Off (PTO) Policy"],
            "expected_outcome": "answer",
        }
    ]
    retrieved = ["Paid Time Off (PTO) Policy", "Code of Conduct"]
    answer = "Full-time employees with two years of service receive 15 PTO days."
    results = [
        {
            "id": "a1",
            "retrieved_sources": retrieved,
            "displayed_sources": retrieved,
            "cited_sources": extract_answer_citations(answer, retrieved),
            "refused": False,
            "answer": answer,
        }
    ]

    report = score_results(cases, results)

    assert report["recall_at_5"] == 100.0
    assert results[0]["cited_sources"] == []
    assert report["citation_correctness"] == 0.0
    assert report["grounded_answer_rate"] == 0.0


def test_citation_correctness_fails_when_answer_names_wrong_retrieved_policy():
    """Naming a different retrieved title must not count as a correct citation."""
    cases = [
        {
            "id": "a1",
            "category": "answerable",
            "expected_sources": ["Paid Time Off (PTO) Policy"],
            "expected_outcome": "answer",
        }
    ]
    retrieved = ["Paid Time Off (PTO) Policy", "Code of Conduct"]
    answer = "See the Code of Conduct for leave details."
    results = [
        {
            "id": "a1",
            "retrieved_sources": retrieved,
            "displayed_sources": retrieved,
            "cited_sources": extract_answer_citations(answer, retrieved),
            "refused": False,
            "answer": answer,
        }
    ]

    report = score_results(cases, results)

    assert report["recall_at_5"] == 100.0
    assert results[0]["cited_sources"] == ["Code of Conduct"]
    assert report["citation_correctness"] == 0.0
    assert report["grounded_answer_rate"] == 0.0


def test_citation_correctness_passes_only_when_answer_names_expected_policy():
    cases = [
        {
            "id": "a1",
            "category": "answerable",
            "expected_sources": ["Paid Time Off (PTO) Policy"],
            "expected_outcome": "answer",
        }
    ]
    retrieved = ["Paid Time Off (PTO) Policy", "Code of Conduct"]
    answer = "The Paid Time Off (PTO) Policy grants 15 days after two years."
    results = [
        {
            "id": "a1",
            "retrieved_sources": retrieved,
            "displayed_sources": retrieved,
            "cited_sources": extract_answer_citations(answer, retrieved),
            "refused": False,
            "answer": answer,
        }
    ]

    report = score_results(cases, results)

    assert report["recall_at_5"] == 100.0
    assert results[0]["cited_sources"] == ["Paid Time Off (PTO) Policy"]
    assert report["citation_correctness"] == 100.0
    assert report["grounded_answer_rate"] == 100.0
