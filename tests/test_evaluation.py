"""Regression tests for the labeled AI evaluation set and its metrics."""

from collections import Counter
from pathlib import Path

import pytest

from policy_assistant.rag.evaluation import (
    EVALUATION_TIERS,
    SMOKE_CASE_COUNT,
    SMOKE_CATEGORY_MIX,
    extract_answer_citations,
    load_cases,
    main,
    resolve_dataset,
    sample_policy_titles,
    score_results,
    validate_sources_against_corpus,
)

SMOKE_DATASET = EVALUATION_TIERS["smoke"]
FULL_DATASET = EVALUATION_TIERS["full"]


def test_smoke_dataset_has_required_twenty_case_mix():
    cases = load_cases(SMOKE_DATASET)

    assert len(cases) == SMOKE_CASE_COUNT
    assert len({case["id"] for case in cases}) == SMOKE_CASE_COUNT
    mix = {
        category: sum(case["category"] == category for case in cases)
        for category in {case["category"] for case in cases}
    }
    assert mix == SMOKE_CATEGORY_MIX


def test_full_dataset_covers_every_sample_policy():
    cases = load_cases(FULL_DATASET)
    titles = sample_policy_titles()
    answerable = [case for case in cases if case["category"] == "answerable"]
    covered = {source for case in answerable for source in case["expected_sources"]}

    assert len(titles) >= 37
    assert len(answerable) >= len(titles)
    assert titles <= covered

    counts = Counter(case["category"] for case in cases)
    assert counts["unanswerable"] >= 1
    assert counts["ambiguous"] >= 1
    assert counts["prompt_injection"] >= 1


def test_named_tiers_resolve_to_checked_in_datasets():
    assert resolve_dataset("smoke") == ("smoke", SMOKE_DATASET)
    assert resolve_dataset("full") == ("full", FULL_DATASET)
    assert resolve_dataset(None, None) == ("smoke", SMOKE_DATASET)


def test_resolve_dataset_rejects_tier_and_path_together(tmp_path):
    with pytest.raises(ValueError, match="either --tier or --dataset"):
        resolve_dataset("smoke", tmp_path / "custom.json")


def test_cli_rejects_tier_and_dataset_together(tmp_path, capsys):
    """Argparse mutually exclusive group owns CLI exclusivity before resolve."""
    custom = tmp_path / "custom.json"
    custom.write_text("[]", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        main(["--tier", "smoke", "--dataset", str(custom)])

    assert exc_info.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_expected_sources_must_exist_in_sample_corpus():
    cases = load_cases(SMOKE_DATASET)
    validate_sources_against_corpus(cases)

    cases[0] = {
        **cases[0],
        "expected_sources": ["Not A Real Policy Title"],
    }
    with pytest.raises(ValueError, match="missing from sample corpus"):
        validate_sources_against_corpus(cases)


def test_corpus_aligned_answerable_labels():
    """Tuition and dress-code questions stay answerable while those policies exist."""
    cases = {case["id"]: case for case in load_cases(SMOKE_DATASET)}

    tuition = cases["answerable_11"]
    assert tuition["category"] == "answerable"
    assert tuition["expected_outcome"] == "answer"
    assert tuition["expected_sources"] == [
        "Tuition Reimbursement and Professional Development Policy"
    ]
    assert "tuition" in tuition["question"].lower()

    dress = cases["answerable_12"]
    assert dress["category"] == "answerable"
    assert dress["expected_outcome"] == "answer"
    assert dress["expected_sources"] == ["Dress Code and Workplace Appearance Policy"]
    assert "dress" in dress["question"].lower()


def test_every_case_records_expected_source_and_behavior():
    for dataset in (SMOKE_DATASET, FULL_DATASET):
        for case in load_cases(dataset):
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
        load_cases(dataset, require_corpus_titles=False)


def test_loader_rejects_unknown_policy_titles(tmp_path):
    dataset = tmp_path / "unknown.json"
    dataset.write_text(
        """[
          {"id":"q1","category":"answerable","question":"One?","expected_sources":["Missing Policy"],"expected_outcome":"answer","expected_behavior":"Answer."}
        ]""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing from sample corpus"):
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


def test_hr_lifecycle_policies_resolve_cross_policy_conflicts():
    """Conflict gate: combined PTO bank, parental bonding control, severance/PTO, injury/safety, COBRA, FMLA."""
    policy_dir = Path(__file__).resolve().parent.parent / "data" / "sample-policies"
    texts = {path.name: path.read_text(encoding="utf-8") for path in policy_dir.glob("*.md")}
    pto = texts["pto-policy.md"]
    sick = texts["sick-and-safe-leave.md"]
    severance = texts["separation-and-severance.md"]
    injury = texts["workplace-injury-and-workers-compensation.md"]
    safety = texts["workplace-safety.md"]
    cobra = texts["benefits-continuation-cobra.md"]
    medical = texts["medical-and-family-leave.md"]
    parental = texts["parental-leave.md"]

    new_policy_blob = sick + severance + injury + cobra + medical
    assert "Meridian Technologies" not in new_policy_blob
    assert "Meridian Systems" in sick
    assert "combined" in sick.casefold() and "pto" in sick.casefold()
    assert "56 hours" not in sick.casefold()
    assert "no second company accrual bank" in sick.casefold()
    overview = pto.split("## Overview", 1)[1].split("## Accrual", 1)[0].casefold()
    assert "short-term illness in a" in overview and "single balance" in overview
    assert "separate sick and safe leave policy bank" not in overview

    assert "PTO Policy" in severance
    assert "Workplace Health and Safety Policy" in injury
    assert "meridian.io/safety-report" in safety
    assert "later of" in cobra.casefold()
    assert "election notice is delivered" in cobra.casefold()
    assert "distinct from statutory fmla" in medical.casefold()
    assert "not add a second company bonding-leave" in medical.casefold()
    assert "company bonding leave and pay" in parental.casefold()
    assert "not stack additional company bonding" in parental.casefold()

    cases = {case["id"]: case for case in load_cases(FULL_DATASET)}
    for case_id in (
        "full_answerable_39",
        "full_answerable_43",
        "full_answerable_44",
        "full_answerable_45",
        "full_answerable_46",
        "full_answerable_41",
    ):
        assert case_id in cases
    assert "Paid Time Off (PTO) Policy" in cases["full_answerable_43"]["expected_sources"]
    assert "combined pto bank" in cases["full_answerable_43"]["expected_behavior"].casefold()
    assert "56" not in cases["full_answerable_39"]["expected_behavior"]
    assert "Parental Leave Policy" in cases["full_answerable_46"]["expected_sources"]
    assert "does not stack" in cases["full_answerable_46"]["expected_behavior"].casefold()
    assert "later of coverage loss" in cases["full_answerable_41"]["expected_behavior"].casefold()
    validate_sources_against_corpus(load_cases(FULL_DATASET))
