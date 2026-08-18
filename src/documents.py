"""Document parsing — the abstraction layer over source formats.

Every source document, whatever its original format, is reduced to one shape:

    {doc_id, title, category, owner, effective_date, body}

which the embedding step then splits into passages that each carry a copy of
that metadata. Downstream code never has to know where a passage came from or
what format it started in.

Documents are plain UTF-8 text with a short header block, blank line, then body:

    Title: Paid Time Off (PTO) Policy
    Category: Time Off & Leave
    Owner: People Operations
    Effective: 2026-01-01

    Full-time employees accrue...

Only Title is required. To support a new input format (PDF, DOCX, Confluence
export), convert it to this shape and the rest of the pipeline is unchanged.
"""
import os
import re

# Header keys we recognise, mapped to the field name used everywhere downstream.
_HEADER_FIELDS = {
    "title": "title",
    "category": "category",
    "owner": "owner",
    "effective": "effective_date",
}

_HEADER_LINE = re.compile(r"^([A-Za-z]+):\s*(.*)$")


def doc_id_from_key(key: str) -> str:
    """Stable identifier derived from the storage key.

    "documents/pto-policy.md" → "pto-policy"
    """
    return os.path.splitext(key.split("/")[-1])[0]


def parse_document(key: str, raw: str) -> dict:
    """Split a raw document into metadata and body.

    Unrecognised or missing headers degrade gracefully: the title falls back to
    a readable form of the filename, and absent fields are simply None.
    """
    lines = raw.lstrip().split("\n")

    metadata: dict[str, str] = {}
    body_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            # Blank line ends the header block — but only once we've seen a header.
            if metadata:
                body_start = i + 1
                break
            continue

        match = _HEADER_LINE.match(stripped)
        field = _HEADER_FIELDS.get(match.group(1).lower()) if match else None
        if field is None:
            # First non-header line: everything from here is body.
            body_start = i
            break
        metadata[field] = match.group(2).strip()
    else:
        body_start = len(lines)

    body = "\n".join(lines[body_start:]).strip()
    doc_id = doc_id_from_key(key)

    return {
        "doc_id": doc_id,
        "title": metadata.get("title") or doc_id.replace("-", " ").replace("_", " ").title(),
        "category": metadata.get("category"),
        "owner": metadata.get("owner"),
        "effective_date": metadata.get("effective_date"),
        "body": body,
    }


def passage_records(document: dict, key: str, chunks: list[str]) -> list[dict]:
    """Build the MongoDB records for one document's passages.

    Text, metadata, and (once embedded) the vector live together in a single
    record — the single-database design the proposal argues for. Filtering by
    category and searching by vector therefore hit the same collection.
    """
    return [
        {
            "source": key,
            "doc_id": document["doc_id"],
            "title": document["title"],
            "category": document["category"],
            "owner": document["owner"],
            "effective_date": document["effective_date"],
            "chunk_index": index,
            "text": chunk,
        }
        for index, chunk in enumerate(chunks)
    ]
