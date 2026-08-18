"""Document library endpoints — browse and search the indexed corpus.

Employees use this to confirm what the assistant actually knows about. If a
policy is not listed here, the assistant cannot answer questions about it, and
seeing that directly is faster than discovering it through a refusal.

Reads from the denormalized documents collection rather than passages, so
listing the corpus never touches the embedding vectors.
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from pymongo import ASCENDING, UpdateOne

from db import documents_col, passages_col
from routes.deps import require_auth

router = APIRouter()

_index_ready = False

MAX_PAGE_SIZE = 200
PREVIEW_LENGTH = 200


def _preview(text: str) -> str:
    """First ~200 characters of readable content, minus markdown headings."""
    content = " ".join(
        line.strip()
        for line in text.split("\n")
        if line.strip() and not line.lstrip().startswith("#")
    )
    if len(content) <= PREVIEW_LENGTH:
        return content
    return content[:PREVIEW_LENGTH].rsplit(" ", 1)[0] + "…"


def ensure_document_index() -> None:
    """Build the documents collection from passages if it is empty.

    Idempotent and cheap after the first call. The ingestion script writes only
    passages, so this is what turns them into a browsable library without
    requiring a second pipeline.
    """
    global _index_ready
    if _index_ready:
        return

    if documents_col.count_documents({}) == 0:
        rebuild_document_index()

    _index_ready = True


def rebuild_document_index() -> int:
    """Recompute one document record per source. Returns the document count."""
    by_source: dict[str, dict] = {}

    cursor = passages_col.find(
        {},
        {
            "_id": 0,
            "embedding": 0,  # never pull vectors into application memory
        },
    ).sort([("source", ASCENDING), ("chunk_index", ASCENDING)])

    for passage in cursor:
        source = passage["source"]
        entry = by_source.setdefault(source, {
            "source": source,
            "doc_id": passage.get("doc_id"),
            "title": passage.get("title") or source,
            "category": passage.get("category"),
            "owner": passage.get("owner"),
            "effective_date": passage.get("effective_date"),
            "passage_count": 0,
            "preview": "",
        })
        entry["passage_count"] += 1
        if passage.get("chunk_index") == 0:
            entry["preview"] = _preview(passage.get("text", ""))

    if not by_source:
        return 0

    documents_col.bulk_write([
        UpdateOne({"source": source}, {"$set": doc}, upsert=True)
        for source, doc in by_source.items()
    ])

    # Drop records for documents removed from the corpus since the last build
    documents_col.delete_many({"source": {"$nin": list(by_source)}})

    return len(by_source)


@router.get("/documents", dependencies=[Depends(require_auth)])
def list_documents(q: str = "", category: str = "", limit: int = 50, skip: int = 0):
    """Paginated, searchable list of indexed documents.

    q        — case-insensitive substring match on title
    category — exact category filter
    limit    — page size (default 50, capped at 200)
    skip     — pagination offset
    """
    ensure_document_index()
    limit = min(max(limit, 1), MAX_PAGE_SIZE)

    query: dict = {}
    if q.strip():
        # re.escape() keeps a crafted search string from becoming a costly pattern
        query["title"] = {"$regex": re.escape(q.strip()), "$options": "i"}
    if category.strip():
        query["category"] = category.strip()

    total = documents_col.count_documents(query)
    items = list(
        documents_col.find(query, {"_id": 0})
        .sort("title", ASCENDING)
        .skip(max(skip, 0))
        .limit(limit)
    )

    return {"items": items, "total": total}


@router.get("/documents/categories", dependencies=[Depends(require_auth)])
def list_categories():
    """Distinct categories present in the corpus, for filter controls."""
    ensure_document_index()
    values = documents_col.distinct("category")
    return sorted(v for v in values if v)


@router.get("/documents/passages", dependencies=[Depends(require_auth)])
def get_document_passages(source: str):
    """Ordered passages for one document — powers the expand-to-read view.

    This is the citation audit trail: it shows the exact text the assistant
    retrieves from, not a re-rendering of the original file.
    """
    passages = list(
        passages_col.find(
            {"source": source},
            {"_id": 0, "text": 1, "chunk_index": 1},
        ).sort("chunk_index", ASCENDING)
    )
    if not passages:
        raise HTTPException(status_code=404, detail="Document not found.")
    return [p["text"] for p in passages]


@router.post("/documents/reindex", dependencies=[Depends(require_auth)])
def reindex_documents():
    """Rebuild the document library after re-running ingestion."""
    count = rebuild_document_index()
    global _index_ready
    _index_ready = True
    return {"ok": True, "documents": count}
