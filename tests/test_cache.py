"""Answer and embedding caches, and the corpus version that keys them."""

from conftest import FAKE_DB

from policy_assistant.rag import cache
from policy_assistant.rag.cache import (
    answer_cache_key,
    bump_corpus_version,
    embed_cached,
    get_cached_answer,
    get_corpus_version,
    is_cacheable_turn,
    normalize,
    put_cached_answer,
    question_hash,
)

RESULT = {
    "answer": "15 days.",
    "sources": ["PTO Policy"],
    "confidence": 78,
    "follow_ups": ["a", "b", "c"],
    "refused": False,
}


def test_normalize_folds_case_and_whitespace():
    assert normalize("  How much   PTO?\n") == "how much pto?"


def test_question_hash_groups_trivial_variants():
    assert question_hash("How much PTO?") == question_hash("how  much pto?")
    assert question_hash("How much PTO?") != question_hash("How much sick leave?")


def test_corpus_version_is_stable_until_bumped():
    first = get_corpus_version()
    assert get_corpus_version() == first
    bumped = bump_corpus_version()
    assert bumped != first
    assert get_corpus_version() == bumped


def test_answer_cache_round_trip_counts_hits():
    version = get_corpus_version()
    assert get_cached_answer("How much PTO?", version) is None

    put_cached_answer("How much PTO?", version, RESULT)
    assert get_cached_answer("how much  pto?", version) == RESULT
    get_cached_answer("How much PTO?", version)

    stored = FAKE_DB["answer_cache"].find_one({})
    assert stored["hits"] == 2
    assert stored["question_sample"] == "How much PTO?"


def test_reingestion_invalidates_by_changing_the_key():
    version = get_corpus_version()
    put_cached_answer("How much PTO?", version, RESULT)
    assert get_cached_answer("How much PTO?", bump_corpus_version()) is None


def test_refusals_are_cached_too():
    version = get_corpus_version()
    put_cached_answer("q", version, {**RESULT, "refused": True, "sources": []})
    assert get_cached_answer("q", version)["refused"] is True


def test_only_first_turns_are_cacheable():
    assert is_cacheable_turn([]) is True
    assert is_cacheable_turn([{"role": "user", "content": "q"}]) is False


def test_embedding_cache_hits_on_second_call():
    vector, hit = embed_cached("How much PTO?")
    assert hit is False and len(vector) > 0
    again, hit = embed_cached("how much  pto?")
    assert hit is True and again == vector


def test_cache_can_be_disabled(monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ENABLED", False)
    version = get_corpus_version()
    put_cached_answer("q", version, RESULT)
    assert get_cached_answer("q", version) is None
    assert FAKE_DB["answer_cache"].count_documents({}) == 0


def test_answer_cache_key_stable_when_config_constant():
    version = get_corpus_version()
    assert answer_cache_key("How much PTO?", version) == answer_cache_key("how much  pto?", version)


def test_answer_cache_key_changes_with_similarity_threshold(monkeypatch):
    version = get_corpus_version()
    before = answer_cache_key("How much PTO?", version)
    monkeypatch.setattr(cache, "SIMILARITY_THRESHOLD", 0.0)
    assert answer_cache_key("How much PTO?", version) != before


def test_answer_cache_key_changes_with_retrieval_k(monkeypatch):
    version = get_corpus_version()
    before = answer_cache_key("How much PTO?", version)
    monkeypatch.setattr(cache, "RETRIEVAL_K", 10)
    assert answer_cache_key("How much PTO?", version) != before
