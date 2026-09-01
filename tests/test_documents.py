"""Source-format abstraction: header parsing and passage records."""

from documents import doc_id_from_key, parse_document, passage_records

RAW = """Title: Paid Time Off (PTO) Policy
Category: Time Off & Leave
Owner: People Operations
Effective: 2026-01-01

## Overview
Full-time employees accrue...
"""


def test_doc_id_is_the_filename_stem():
    assert doc_id_from_key("documents/pto-policy.md") == "pto-policy"
    assert doc_id_from_key("pto-policy") == "pto-policy"


def test_parses_headers_and_body():
    doc = parse_document("documents/pto-policy.md", RAW)
    assert doc == {
        "doc_id": "pto-policy",
        "title": "Paid Time Off (PTO) Policy",
        "category": "Time Off & Leave",
        "owner": "People Operations",
        "effective_date": "2026-01-01",
        "body": "## Overview\nFull-time employees accrue...",
    }


def test_only_title_is_required_and_it_falls_back_to_the_filename():
    doc = parse_document("documents/remote_work-policy.md", "Just a body.\nSecond line.")
    assert doc["title"] == "Remote Work Policy"
    assert doc["category"] is None and doc["owner"] is None and doc["effective_date"] is None
    assert doc["body"] == "Just a body.\nSecond line."


def test_unknown_header_key_starts_the_body():
    doc = parse_document("k.md", "Title: T\nAuthor: someone\n\nbody")
    assert doc["title"] == "T"
    assert doc["body"] == "Author: someone\n\nbody"


def test_leading_blank_lines_and_header_only_documents():
    assert parse_document("k.md", "\n\nTitle: T\n\nbody")["body"] == "body"
    assert parse_document("k.md", "Title: T\n")["body"] == ""
    assert parse_document("k.md", "")["title"] == "K"


def test_passage_records_carry_metadata_on_every_chunk():
    doc = parse_document("documents/pto-policy.md", RAW)
    records = passage_records(doc, "documents/pto-policy.md", ["one", "two"])
    assert [r["chunk_index"] for r in records] == [0, 1]
    assert [r["text"] for r in records] == ["one", "two"]
    for record in records:
        assert record["source"] == "documents/pto-policy.md"
        assert record["title"] == "Paid Time Off (PTO) Policy"
        assert record["category"] == "Time Off & Leave"
        assert "body" not in record
