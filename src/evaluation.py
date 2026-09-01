"""Run and score the labeled AI evaluation set.

The unit tests exercise dataset validation and metric calculations without any
network access. The command line runner uses the configured MongoDB and model
provider, so it is intentionally separate from ``make check``.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ALLOWED_CATEGORIES = {
    "answerable",
    "unanswerable",
    "ambiguous",
    "prompt_injection",
}
ALLOWED_OUTCOMES = {"answer", "clarify", "refuse"}
REQUIRED_FIELDS = {
    "id",
    "category",
    "question",
    "expected_sources",
    "expected_outcome",
    "expected_behavior",
}


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    """Load a JSON evaluation set and reject incomplete or duplicate cases."""
    dataset_path = Path(path)
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError("Evaluation dataset must be a JSON list")

    seen: set[str] = set()
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"Evaluation case {index} must be an object")

        missing = REQUIRED_FIELDS - case.keys()
        if missing:
            raise ValueError(f"Evaluation case {index} is missing: {', '.join(sorted(missing))}")

        case_id = case["id"]
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"Evaluation case {index} has an invalid id")
        if case_id in seen:
            raise ValueError(f"Duplicate evaluation case id: {case_id}")
        seen.add(case_id)

        if case["category"] not in ALLOWED_CATEGORIES:
            raise ValueError(f"Evaluation case {case_id} has an invalid category")
        if case["expected_outcome"] not in ALLOWED_OUTCOMES:
            raise ValueError(f"Evaluation case {case_id} has an invalid outcome")
        if not isinstance(case["question"], str) or not case["question"].strip():
            raise ValueError(f"Evaluation case {case_id} has an empty question")
        if not isinstance(case["expected_sources"], list) or not all(
            isinstance(source, str) and source.strip() for source in case["expected_sources"]
        ):
            raise ValueError(f"Evaluation case {case_id} has invalid expected sources")
        if not isinstance(case["expected_behavior"], str) or not case["expected_behavior"].strip():
            raise ValueError(f"Evaluation case {case_id} has empty expected behavior")

    return cases


def _percentage(outcomes: Iterable[bool]) -> float | None:
    values = list(outcomes)
    if not values:
        return None
    return round(100 * sum(values) / len(values), 1)


def score_results(
    cases: list[dict[str, Any]], results: list[dict[str, Any]]
) -> dict[str, float | int | None]:
    """Calculate the four Week 3 metrics from case level results.

    Recall@5 and answer quality use only cases whose expected outcome is an
    answer. Ambiguous questions are inspected separately because a useful
    clarification cannot be judged reliably by source matching alone.
    """
    result_by_id: dict[str, dict[str, Any]] = {}
    for result in results:
        result_id = result.get("id")
        if result_id in result_by_id:
            raise ValueError(f"Duplicate evaluation result id: {result_id}")
        result_by_id[result_id] = result

    missing = [case["id"] for case in cases if case["id"] not in result_by_id]
    if missing:
        raise ValueError(f"Missing evaluation results for: {', '.join(missing)}")

    answer_cases = [case for case in cases if case["expected_outcome"] == "answer"]
    refusal_cases = [case for case in cases if case["expected_outcome"] == "refuse"]

    def source_match(case: dict[str, Any], result_field: str) -> bool:
        expected = set(case["expected_sources"])
        actual = set(result_by_id[case["id"]].get(result_field, []))
        return bool(expected & actual)

    citation_matches = [source_match(case, "cited_sources") for case in answer_cases]

    return {
        "evaluated_cases": len(cases),
        "recall_at_5": _percentage(
            source_match(case, "retrieved_sources") for case in answer_cases
        ),
        "citation_correctness": _percentage(citation_matches),
        "grounded_answer_rate": _percentage(
            not result_by_id[case["id"]].get("refused", False) and citation_matches[index]
            for index, case in enumerate(answer_cases)
        ),
        "refusal_handling": _percentage(
            result_by_id[case["id"]].get("refused", False) for case in refusal_cases
        ),
    }


def run_live_case(case: dict[str, Any]) -> dict[str, Any]:
    """Execute one case through the configured retrieval and answer pipeline."""
    from llm import get_provider
    from rag_chain import (
        build_messages,
        cited_sources,
        confidence_score,
        is_grounded,
        retrieve_passages,
    )

    passages = retrieve_passages(case["question"], k=5)
    retrieved_sources = cited_sources(passages)
    grounded = is_grounded(passages)

    answer = ""
    if grounded:
        answer = get_provider().complete(
            build_messages(case["question"], passages, []),
            role="answer",
            temperature=0,
        )

    return {
        "id": case["id"],
        "category": case["category"],
        "question": case["question"],
        "retrieved_sources": retrieved_sources,
        "cited_sources": retrieved_sources if grounded else [],
        "confidence": confidence_score(passages),
        "refused": not grounded,
        "answer": answer,
    }


def run_evaluation(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run every labeled question using the real configured services."""
    return [run_live_case(case) for case in cases]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evaluation/questions.json"),
        help="Path to the labeled question set",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/results.json"),
        help="Where to write detailed results and metrics",
    )
    args = parser.parse_args()

    cases = load_cases(args.dataset)
    results = run_evaluation(cases)
    report = {"metrics": score_results(cases, results), "results": results}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["metrics"], indent=2))
    print(f"Detailed results written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
