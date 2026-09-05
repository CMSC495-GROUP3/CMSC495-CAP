"""The retrieval side of the pipeline: grounding gate, scoring, prompt assembly."""

import pytest
from conftest import make_passages

from policy_assistant.rag import rag_chain
from policy_assistant.rag.rag_chain import (
    build_citation_manifest,
    build_context,
    build_messages,
    cited_sources,
    condense_question,
    confidence_score,
    generate_follow_ups,
    is_grounded,
)


class TestGroundingGate:
    def test_refuses_when_nothing_was_retrieved(self):
        assert is_grounded([], threshold=0.62) is False

    def test_refuses_when_best_passage_is_below_threshold(self):
        assert is_grounded(make_passages(0.61, 0.50), threshold=0.62) is False

    def test_answers_at_the_threshold(self):
        assert is_grounded(make_passages(0.62), threshold=0.62) is True

    def test_gates_on_best_passage_not_mean(self):
        # Mean is 0.50, which would wrongly refuse. One strong hit is enough.
        assert is_grounded(make_passages(0.90, 0.30, 0.30), threshold=0.62) is True

    def test_missing_score_counts_as_zero(self):
        passages = make_passages(0.9)
        del passages[0]["score"]
        assert is_grounded(passages, threshold=0.62) is False


class TestConfidence:
    def test_is_mean_similarity_as_percentage(self):
        assert confidence_score(make_passages(0.80, 0.60)) == 70

    def test_zero_when_nothing_retrieved(self):
        assert confidence_score([]) == 0


class TestSources:
    def test_deduplicates_titles_preserving_order(self):
        passages = (
            make_passages(0.9, title="B")
            + make_passages(0.8, title="A")
            + make_passages(0.7, title="B")
        )
        assert cited_sources(passages) == ["B", "A"]

    def test_falls_back_to_readable_filename(self):
        passage = {"source": "documents/parental_leave-policy.md", "score": 0.9, "text": "x"}
        assert cited_sources([passage]) == ["Parental Leave Policy"]


class TestPromptAssembly:
    def test_context_labels_each_passage_with_title_and_date(self):
        context = build_context(make_passages(0.9))
        assert context.startswith("[Paid Time Off (PTO) Policy (effective 2026-01-01)]\n")
        assert "Passage 0" in context

    def test_exactly_one_system_message_even_with_history(self):
        history = [
            {"role": "user", "content": "q1", "sources": []},
            {"role": "assistant", "content": "a1", "sources": ["Doc A"]},
        ]
        messages = build_messages("q2", make_passages(0.9), history)
        assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
        assert messages[-1]["content"].endswith("Question: q2")
        assert "Passage 0" in messages[-1]["content"]

    def test_history_only_forwards_role_and_content(self):
        history = [{"role": "user", "content": "q1", "sources": [], "secret": "x"}]
        messages = build_messages("q2", make_passages(0.9), history)
        assert messages[1] == {"role": "user", "content": "q1"}

    def test_citation_manifest_lists_previously_cited_documents_once(self):
        history = [
            {"role": "assistant", "content": "a", "sources": ["Doc A", "Doc B"]},
            {"role": "user", "content": "q", "sources": []},
            {"role": "assistant", "content": "a", "sources": ["Doc B", "Doc C"]},
        ]
        manifest = build_citation_manifest(history)
        assert manifest.splitlines() == [
            "Documents already cited in this conversation:",
            "- Doc A",
            "- Doc B",
            "- Doc C",
        ]
        assert manifest in build_messages("q", make_passages(0.9), history)[0]["content"]

    def test_no_manifest_without_prior_citations(self):
        assert build_citation_manifest([{"role": "user", "content": "q"}]) == ""


class _BrokenProvider:
    def complete(self, *args, **kwargs):
        raise RuntimeError("provider down")


class TestConversationHelpers:
    def test_first_turn_is_not_rewritten_and_makes_no_model_call(self, monkeypatch):
        monkeypatch.setattr(rag_chain, "get_provider", _BrokenProvider)
        assert condense_question("how much PTO?", []) == "how much PTO?"

    def test_follow_up_is_rewritten_by_the_utility_model(self):
        history = [{"role": "user", "content": "tell me about parental leave"}]
        rewritten = condense_question("how much do I get?", history)
        assert rewritten and rewritten != "how much do I get?"

    def test_rewrite_falls_back_to_raw_question_when_provider_fails(self, monkeypatch):
        monkeypatch.setattr(rag_chain, "get_provider", _BrokenProvider)
        history = [{"role": "user", "content": "tell me about parental leave"}]
        assert condense_question("how much do I get?", history) == "how much do I get?"

    def test_follow_ups_are_three_lines(self):
        follow_ups = generate_follow_ups("q", "a")
        assert len(follow_ups) == 3
        assert all(f and "\n" not in f for f in follow_ups)

    def test_follow_ups_are_optional(self, monkeypatch):
        monkeypatch.setattr(rag_chain, "get_provider", _BrokenProvider)
        assert generate_follow_ups("q", "a") == []


@pytest.mark.parametrize(
    "source,expected",
    [
        ("documents/pto-policy.md", "Pto Policy"),
        ("a/b/c/remote_work.txt", "Remote Work"),
        ("", ""),
    ],
)
def test_title_from_source(source, expected):
    assert rag_chain._title_from_source(source) == expected
