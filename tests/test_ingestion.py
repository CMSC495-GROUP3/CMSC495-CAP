"""Offline ingestion behavior with AWS, MongoDB, and the model provider stubbed."""

import importlib
import sys

import boto3
import pytest

from cache import get_corpus_version
from conftest import FAKE_DB


class _Body:
    def __init__(self, text: str):
        self._text = text

    def read(self) -> bytes:
        return self._text.encode("utf-8")


class _Paginator:
    def __init__(self, pages: list[dict]):
        self._pages = pages

    def paginate(self, **_kwargs):
        return iter(self._pages)


class _S3:
    def __init__(self, pages: list[dict], objects: dict[str, str]):
        self._pages = pages
        self._objects = objects

    def get_paginator(self, _operation: str):
        return _Paginator(self._pages)

    def get_object(self, *, Bucket: str, Key: str):
        return {"Body": _Body(self._objects[Key])}


def _load_ingestion(monkeypatch, s3):
    """Import the script only after replacing boto3's network client."""
    monkeypatch.setattr(boto3, "client", lambda *_args, **_kwargs: s3)
    sys.modules.pop("embed_documents", None)
    return importlib.import_module("embed_documents")


def test_fetches_all_s3_pages_and_skips_directory_placeholders(monkeypatch):
    s3 = _S3(
        pages=[
            {"Contents": [{"Key": "documents/"}, {"Key": "documents/pto.md"}]},
            {"Contents": [{"Key": "documents/conduct.txt"}]},
        ],
        objects={
            "documents/pto.md": "Title: PTO\n\nPTO body.",
            "documents/conduct.txt": "Title: Conduct\n\nConduct body.",
        },
    )
    ingestion = _load_ingestion(monkeypatch, s3)
    monkeypatch.setenv("S3_BUCKET_NAME", "test-policies")

    assert ingestion.fetch_documents_from_s3() == [
        ("documents/pto.md", "Title: PTO\n\nPTO body."),
        ("documents/conduct.txt", "Title: Conduct\n\nConduct body."),
    ]


def test_missing_bucket_stops_with_actionable_message(monkeypatch):
    ingestion = _load_ingestion(monkeypatch, _S3([], {}))
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)

    with pytest.raises(SystemExit, match="S3_BUCKET_NAME is not set"):
        ingestion.fetch_documents_from_s3()


def test_reingestion_replaces_stale_passages_and_invalidates_cache(monkeypatch):
    ingestion = _load_ingestion(monkeypatch, _S3([], {}))
    documents = [
        (
            "documents/pto.md",
            "Title: Paid Time Off\nCategory: Leave\nOwner: HR\n"
            "Effective: 2026-01-01\n\nEmployees accrue 15 PTO days.",
        ),
        (
            "documents/conduct.md",
            "Title: Code of Conduct\nCategory: Workplace\n\nEmployees act professionally.",
        ),
    ]

    class Provider:
        def embed(self, _text: str) -> list[float]:
            return [0.25, 0.75]

    monkeypatch.setattr(ingestion, "fetch_documents_from_s3", lambda: documents)
    monkeypatch.setattr(ingestion, "get_collection", lambda name: FAKE_DB[name])
    monkeypatch.setattr(ingestion, "get_provider", lambda: Provider())

    FAKE_DB["passages"].insert_one({"source": "documents/stale.md", "text": "stale"})
    initial_version = get_corpus_version()

    ingestion.embed_and_store()

    first_version = get_corpus_version()
    first_passages = list(FAKE_DB["passages"].find({}))
    assert first_version != initial_version
    assert len(first_passages) == 2
    assert [passage["source"] for passage in first_passages] == [
        "documents/pto.md",
        "documents/conduct.md",
    ]
    assert [passage["chunk_index"] for passage in first_passages] == [0, 0]
    assert first_passages[0]["title"] == "Paid Time Off"
    assert first_passages[0]["category"] == "Leave"
    assert first_passages[0]["owner"] == "HR"
    assert first_passages[0]["effective_date"] == "2026-01-01"
    assert first_passages[0]["text"] == "Employees accrue 15 PTO days."
    assert first_passages[0]["embedding"] == [0.25, 0.75]

    ingestion.embed_and_store()

    assert get_corpus_version() != first_version
    assert FAKE_DB["passages"].count_documents({}) == 2
    assert FAKE_DB["passages"].count_documents({"source": "documents/stale.md"}) == 0
