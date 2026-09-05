"""Run and score the labeled AI evaluation set.

The unit tests exercise dataset validation and metric calculations without any
network access. The command line runner uses the configured MongoDB and model
provider, so it is intentionally separate from ``make check``.

Two tiers are supported:

* ``smoke`` — the bounded routine set in ``evaluation/questions.json``
* ``full`` — one supported retrieval question per sample policy, plus refusal,
  ambiguity, and prompt-injection coverage
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from policy_assistant.rag.documents import parse_document

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

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS_DIR = REPO_ROOT / "data" / "sample-policies"
EVALUATION_TIERS = {
    "smoke": REPO_ROOT / "evaluation" / "questions.json",
    "full": REPO_ROOT / "evaluation" / "questions_full.json",
}
# Smoke stays at 20 cases with the corpus-reconciled category mix.
SMOKE_CASE_COUNT = 20
SMOKE_CATEGORY_MIX = {
    "answerable": 12,
    "unanswerable": 2,
    "ambiguous": 3,
    "prompt_injection": 3,
}


def sample_policy_titles(corpus_dir: str | Path | None = None) -> set[str]:
    """Return Title headers from the sample policy corpus."""
    root = Path(corpus_dir) if corpus_dir is not None else DEFAULT_CORPUS_DIR
    if not root.is_dir():
        raise ValueError(f"Sample policy corpus not found: {root}")

    titles: set[str] = set()
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        document = parse_document(path.name, path.read_text(encoding="utf-8"))
        title = document.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"Sample policy {path.name} is missing a Title header")
        titles.add(title.strip())
    if not titles:
        raise ValueError(f"No sample policies found in {root}")
    return titles


def validate_sources_against_corpus(
    cases: list[dict[str, Any]],
    corpus_dir: str | Path | None = None,
) -> None:
    """Reject expected_sources that do not match a sample policy Title."""
    titles = sample_policy_titles(corpus_dir)
    unknown: list[str] = []
    for case in cases:
        for source in case["expected_sources"]:
            if source not in titles:
                unknown.append(f"{case['id']}: {source}")
    if unknown:
        raise ValueError(
            "Evaluation expected_sources missing from sample corpus: " + "; ".join(unknown)
        )


def load_cases(
    path: str | Path,
    *,
    corpus_dir: str | Path | None = None,
    require_corpus_titles: bool = True,
) -> list[dict[str, Any]]:
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

    if require_corpus_titles:
        validate_sources_against_corpus(cases, corpus_dir=corpus_dir)

    return cases


def resolve_dataset(tier: str | None = None, dataset: Path | None = None) -> tuple[str, Path]:
    """Map an explicit tier or dataset path to ``(tier_label, path)``."""
    if tier is not None and dataset is not None:
        raise ValueError("Specify either --tier or --dataset, not both")
    if tier is not None:
        if tier not in EVALUATION_TIERS:
            raise ValueError(f"Unknown evaluation tier: {tier}")
        return tier, EVALUATION_TIERS[tier]
    if dataset is not None:
        return "custom", Path(dataset)
    return "smoke", EVALUATION_TIERS["smoke"]


def _percentage(outcomes: Iterable[bool]) -> float | None:
    values = list(outcomes)
    if not values:
        return None
    return round(100 * sum(values) / len(values), 1)


def extract_answer_citations(answer: str, known_titles: Iterable[str]) -> list[str]:
    """Return known policy titles that appear in the generated answer text.

    Matching is case-insensitive and prefers longer titles first so a short
    title cannot claim a hit inside a longer one. Only titles from the
    retrieved set are considered; this measures whether the answer named a
    retrieved policy, not whether the UI listed retrieval hits.
    """
    titles = [title for title in known_titles if isinstance(title, str) and title.strip()]
    if not answer or not titles:
        return []

    haystack = answer.casefold()
    matched: list[tuple[int, str]] = []
    occupied: list[tuple[int, int]] = []

    for title in sorted(titles, key=len, reverse=True):
        needle = title.casefold()
        start = 0
        while True:
            index = haystack.find(needle, start)
            if index < 0:
                break
            end = index + len(needle)
            overlaps = any(
                index < occupied_end and end > occupied_start
                for occupied_start, occupied_end in occupied
            )
            if not overlaps:
                matched.append((index, title))
                occupied.append((index, end))
                break
            start = index + 1

    matched.sort(key=lambda item: item[0])
    return [title for _, title in matched]


def score_results(
    cases: list[dict[str, Any]], results: list[dict[str, Any]]
) -> dict[str, float | int | None]:
    """Calculate the four Week 3 metrics from case level results.

    Recall@5 uses ``retrieved_sources`` only. Citation correctness and grounded
    answer rate use ``cited_sources``, which must be titles found in the
    generated answer rather than the retrieved or displayed attribution lists.
    Ambiguous questions remain a manual review: source matching alone cannot
    judge whether a clarification asked for the right missing detail.
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
    from policy_assistant.rag.llm import get_provider
    from policy_assistant.rag.rag_chain import (
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

    # Displayed attribution mirrors what the chat API attaches to an answered
    # turn (retrieved titles). Answer citations are derived separately.
    displayed_sources = retrieved_sources if grounded else []
    answer_citations = extract_answer_citations(answer, retrieved_sources)

    return {
        "id": case["id"],
        "category": case["category"],
        "question": case["question"],
        "retrieved_sources": retrieved_sources,
        "displayed_sources": displayed_sources,
        "cited_sources": answer_citations,
        "confidence": confidence_score(passages),
        "refused": not grounded,
        "answer": answer,
    }


def run_evaluation(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run every labeled question using the real configured services."""
    return [run_live_case(case) for case in cases]


def _confirm_paid_run(tier: str, case_count: int, dataset: Path, assume_yes: bool) -> bool:
    """Show the selected tier and case count before any paid provider call."""
    print(f"Evaluation tier: {tier}")
    print(f"Dataset: {dataset}")
    print(f"Cases selected: {case_count}")
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(
            "Refusing to start a paid evaluation without an interactive confirmation or --yes.",
            file=sys.stderr,
        )
        return False
    try:
        reply = (
            input("Continue with live evaluation against paid providers? [y/N] ").strip().lower()
        )
    except EOFError:
        print(
            "Refusing to start a paid evaluation without an interactive confirmation or --yes.",
            file=sys.stderr,
        )
        return False
    return reply in {"y", "yes"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--tier",
        choices=sorted(EVALUATION_TIERS),
        help="Named evaluation tier (smoke or full). Preferred over --dataset.",
    )
    source.add_argument(
        "--dataset",
        type=Path,
        help="Explicit path to a labeled question set (mutually exclusive with --tier)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/results.json"),
        help="Where to write detailed results and metrics",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation after printing the case count",
    )
    args = parser.parse_args(argv)

    # Argparse owns CLI exclusivity; resolve_dataset still rejects both for
    # library callers and defaults to the smoke tier when neither is set.
    tier, dataset = resolve_dataset(args.tier, args.dataset)

    cases = load_cases(dataset)
    if not _confirm_paid_run(tier, len(cases), dataset, assume_yes=args.yes):
        print("Aborted before paid execution.")
        return 1

    results = run_evaluation(cases)
    report = {
        "tier": tier,
        "dataset": str(dataset),
        "metrics": score_results(cases, results),
        "results": results,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["metrics"], indent=2))
    print(f"Detailed results written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
