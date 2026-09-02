"""Upload the sample policy corpus to S3.

    python -m policy_assistant.rag.seed_documents

Reads every file in data/sample-policies/ and writes it to S3 under the
configured prefix. Replace that directory with real HR documents and this same
script uploads those instead — nothing here is specific to the samples.

Keeping the pilot corpus small is deliberate: Atlas free tier allows 512 MB, and
embeddings are the bulk of the storage. See the README for the sizing note.
"""

import os
import sys
from pathlib import Path

import boto3
from dotenv import load_dotenv

from policy_assistant.rag.config import S3_DOCUMENT_PREFIX

load_dotenv()

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample-policies"

s3 = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION"),
)


def upload_documents(source_dir: Path = SAMPLE_DIR) -> None:
    bucket = os.getenv("S3_BUCKET_NAME")
    if not bucket:
        sys.exit("S3_BUCKET_NAME is not set. Copy .env.example to .env and fill it in.")

    if not source_dir.is_dir():
        sys.exit(f"No such directory: {source_dir}")

    files = sorted(p for p in source_dir.iterdir() if p.is_file() and not p.name.startswith("."))
    if not files:
        sys.exit(f"No documents found in {source_dir}")

    for path in files:
        key = f"{S3_DOCUMENT_PREFIX}{path.name}"
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=path.read_text(encoding="utf-8").encode("utf-8"),
            ContentType="text/markdown; charset=utf-8",
        )
        print(f"Uploaded {key}")

    print(f"\nDone. {len(files)} documents in s3://{bucket}/{S3_DOCUMENT_PREFIX}")
    print("Next: python -m policy_assistant.rag.embed_documents")


if __name__ == "__main__":
    upload_documents()
