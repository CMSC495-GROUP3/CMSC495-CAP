"""Ingestion — chunk, embed, and store the policy corpus.

Run once after the documents are in S3, and again whenever they change:

    python src/embed_documents.py

This is the "embed documents once into Atlas" half of the design. Nothing here
runs at query time.
"""
import os
import sys

import boto3
from dotenv import load_dotenv
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pymongo import MongoClient

from config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    PASSAGES_COLLECTION,
    S3_DOCUMENT_PREFIX,
)
from documents import parse_document, passage_records
from llm import get_provider

load_dotenv()

s3 = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION"),
)
mongo_client = MongoClient(os.getenv("MONGODB_URI"))
collection = mongo_client[os.getenv("MONGODB_DB", "policy_assistant")][PASSAGES_COLLECTION]

# Splits on paragraph, then line, then sentence boundaries before resorting to a
# hard character cut, so a chunk usually ends somewhere meaningful. The overlap
# keeps a clause that straddles a boundary intact in at least one chunk.
splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
)


def fetch_documents_from_s3() -> list[tuple[str, str]]:
    """Return (key, contents) for every document under the configured prefix."""
    bucket = os.getenv("S3_BUCKET_NAME")
    if not bucket:
        sys.exit("S3_BUCKET_NAME is not set. Copy .env.example to .env and fill it in.")

    documents: list[tuple[str, str]] = []
    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket, Prefix=S3_DOCUMENT_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue  # directory placeholder
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
            documents.append((key, body))

    return documents


def embed_and_store() -> None:
    documents = fetch_documents_from_s3()
    if not documents:
        sys.exit(
            f"No documents found under s3://{os.getenv('S3_BUCKET_NAME')}/{S3_DOCUMENT_PREFIX} — "
            f"run python src/seed_documents.py first."
        )

    # Replace the corpus wholesale so re-runs never leave stale or duplicated
    # passages behind. Small corpus, so a full rebuild is cheaper than diffing.
    deleted = collection.delete_many({}).deleted_count
    print(f"Cleared {deleted} existing passages.\n")

    total = 0
    for key, raw in documents:
        document = parse_document(key, raw)
        chunks = splitter.split_text(document["body"])
        records = passage_records(document, key, chunks)

        for record in records:
            record["embedding"] = get_provider().embed(record["text"])

        if records:
            collection.insert_many(records)
            total += len(records)

        print(f"  {document['title']:<45} {len(records):>3} passages")

    print(f"\nDone. {len(documents)} documents → {total} passages in MongoDB.")
    print(
        "\nIf you have not created it yet, add an Atlas Vector Search index named "
        f"'{os.getenv('VECTOR_INDEX_NAME', 'vector_index')}' on the "
        f"'{PASSAGES_COLLECTION}' collection — see the README."
    )


if __name__ == "__main__":
    embed_and_store()
