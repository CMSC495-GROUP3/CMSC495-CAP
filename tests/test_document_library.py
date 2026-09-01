"""The document library: derived from passages, rebuilt when the corpus changes."""

from cache import get_corpus_version
from conftest import FAKE_DB
from routes.documents import _preview


def _seed_passages(*docs: tuple[str, str, str, int]):
    """(source, title, category, chunk count) tuples, with a heading in chunk 0."""
    for source, title, category, count in docs:
        for i in range(count):
            FAKE_DB["passages"].insert_one(
                {
                    "source": source,
                    "doc_id": source.split("/")[-1][:-3],
                    "title": title,
                    "category": category,
                    "owner": None,
                    "effective_date": None,
                    "chunk_index": i,
                    "text": f"# {title}\nParagraph {i} of {title}. " + "words " * 60,
                    "embedding": [0.0] * 4,
                }
            )


def test_preview_skips_headings_and_cuts_on_a_word():
    assert _preview("# Heading\n\nShort body.") == "Short body."
    long = _preview("# H\n" + "word " * 100)
    assert len(long) <= 201 and long.endswith("…") and not long.endswith(" …")


def test_library_is_built_from_passages_without_vectors(client, auth):
    _seed_passages(
        ("documents/pto.md", "PTO", "Leave", 3), ("documents/expenses.md", "Expenses", "Finance", 2)
    )

    body = client.get("/api/documents", headers=auth).json()
    assert body["total"] == 2
    assert [d["title"] for d in body["items"]] == ["Expenses", "PTO"]
    pto = body["items"][1]
    assert pto["passage_count"] == 3
    assert pto["preview"].startswith("Paragraph 0 of PTO.")
    assert "embedding" not in pto

    assert client.get("/api/documents/categories", headers=auth).json() == ["Finance", "Leave"]


def test_search_and_filters_and_paging(client, auth):
    _seed_passages(("documents/a.md", "Alpha (x)", "One", 1), ("documents/b.md", "Beta", "Two", 1))

    assert client.get("/api/documents", params={"q": "(x)"}, headers=auth).json()["total"] == 1
    assert client.get("/api/documents", params={"q": "ALPHA"}, headers=auth).json()["total"] == 1
    assert (
        client.get("/api/documents", params={"category": "Two"}, headers=auth).json()["items"][0][
            "title"
        ]
        == "Beta"
    )
    page = client.get("/api/documents", params={"limit": 1, "skip": 1}, headers=auth).json()
    assert page["total"] == 2 and [d["title"] for d in page["items"]] == ["Beta"]
    assert len(client.get("/api/documents", params={"limit": 0}, headers=auth).json()["items"]) == 1


def test_passages_are_returned_in_order(client, auth):
    _seed_passages(("documents/pto.md", "PTO", "Leave", 3))
    texts = client.get(
        "/api/documents/passages", params={"source": "documents/pto.md"}, headers=auth
    ).json()
    assert [t.split("\n")[1][:11] for t in texts] == ["Paragraph 0", "Paragraph 1", "Paragraph 2"]
    assert (
        client.get("/api/documents/passages", params={"source": "nope"}, headers=auth).status_code
        == 404
    )


def test_reingestion_rebuilds_the_library_once(client, auth):
    _seed_passages(("documents/pto.md", "PTO", "Leave", 1))
    assert client.get("/api/documents", headers=auth).json()["total"] == 1

    # A stale library is served until the corpus version moves.
    FAKE_DB["passages"].delete_many({})
    _seed_passages(("documents/new.md", "New", "Leave", 1))
    assert [d["title"] for d in client.get("/api/documents", headers=auth).json()["items"]] == [
        "PTO"
    ]

    before = get_corpus_version()
    result = client.post("/api/documents/reindex", headers=auth).json()
    assert result["documents"] == 1 and result["corpus_version"] != before
    assert [d["title"] for d in client.get("/api/documents", headers=auth).json()["items"]] == [
        "New"
    ]


def test_empty_corpus(client, auth):
    assert client.get("/api/documents", headers=auth).json() == {"items": [], "total": 0}
    assert client.post("/api/documents/reindex", headers=auth).json()["documents"] == 0
