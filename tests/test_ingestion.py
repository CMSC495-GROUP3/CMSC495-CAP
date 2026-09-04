"""Offline ingestion behavior with AWS, MongoDB, and the model provider stubbed."""

import importlib
import sys
from pathlib import Path

import boto3
import pytest
from conftest import FAKE_DB

from policy_assistant.rag.cache import get_corpus_version


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
    sys.modules.pop("policy_assistant.rag.embed_documents", None)
    return importlib.import_module("policy_assistant.rag.embed_documents")


class _UploadOnlyS3:
    """Records put_object calls; the seeding script needs nothing else."""

    def __init__(self):
        self.uploads: list[tuple[str, str]] = []

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str):
        self.uploads.append((Bucket, Key))


SEEDING_MODULE = "policy_assistant.rag.seed_documents"


@pytest.fixture
def seeding(monkeypatch):
    """The seeding script imported with S3 stubbed and a bucket configured.

    Unloaded afterwards so the stubbed module does not outlive the test."""
    s3 = _UploadOnlyS3()
    monkeypatch.setattr(boto3, "client", lambda *_args, **_kwargs: s3)
    monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")
    sys.modules.pop(SEEDING_MODULE, None)
    try:
        yield importlib.import_module(SEEDING_MODULE), s3
    finally:
        sys.modules.pop(SEEDING_MODULE, None)


def test_seeding_uploads_the_sample_corpus_from_the_repository_root(seeding):
    """The default source directory is resolved relative to the module's
    location, which the package move once broke. Guard the real path."""
    module, s3 = seeding
    sample_dir = Path(__file__).resolve().parent.parent / "data" / "sample-policies"
    expected = sorted(
        p.name for p in sample_dir.iterdir() if p.is_file() and not p.name.startswith(".")
    )
    assert sample_dir == module.SAMPLE_DIR
    assert len(expected) > 0

    module.upload_documents()

    assert [bucket for bucket, _ in s3.uploads] == ["test-bucket"] * len(expected)
    assert [key for _, key in s3.uploads] == [
        f"{module.S3_DOCUMENT_PREFIX}{name}" for name in expected
    ]


def test_seeding_without_a_bucket_stops_with_actionable_message(seeding, monkeypatch):
    module, s3 = seeding
    monkeypatch.delenv("S3_BUCKET_NAME")

    with pytest.raises(SystemExit, match="S3_BUCKET_NAME is not set"):
        module.upload_documents()
    assert s3.uploads == []


def test_seeding_from_a_missing_directory_names_it(seeding, tmp_path):
    module, s3 = seeding

    with pytest.raises(SystemExit, match="No such directory"):
        module.upload_documents(tmp_path / "does-not-exist")
    assert s3.uploads == []


def test_seeding_from_an_empty_directory_uploads_nothing(seeding, tmp_path):
    module, s3 = seeding
    (tmp_path / ".hidden").write_text("ignored")

    with pytest.raises(SystemExit, match="No documents found"):
        module.upload_documents(tmp_path)
    assert s3.uploads == []


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
    monkeypatch.setattr(ingestion, "get_provider", Provider)

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
