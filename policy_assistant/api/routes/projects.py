"""Project CRUD endpoints."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from policy_assistant.api.db import conversations_col, projects_col
from policy_assistant.api.routes.deps import require_auth

router = APIRouter()

PROJECT_NAME_MAX_LENGTH = 100


def _normalize_project_name(value: str) -> str:
    """Strip and collapse whitespace; reject empty or oversized names."""
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("name must not be blank")
    if len(normalized) > PROJECT_NAME_MAX_LENGTH:
        raise ValueError(f"name must be at most {PROJECT_NAME_MAX_LENGTH} characters")
    return normalized


class CreateProjectRequest(BaseModel):
    name: str = Field(max_length=PROJECT_NAME_MAX_LENGTH)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _normalize_project_name(value)


@router.get("/projects", dependencies=[Depends(require_auth)])
def list_projects():
    docs = projects_col.find({}, {"_id": 0}).sort("created_at", 1)
    return list(docs)


@router.post("/projects", dependencies=[Depends(require_auth)])
def create_project(body: CreateProjectRequest):
    doc = {
        "project_id": str(uuid.uuid4()),
        "name": body.name,
        "created_at": datetime.now(UTC),
    }
    projects_col.insert_one(doc)
    doc.pop("_id", None)
    return doc


@router.delete("/projects/{project_id}", dependencies=[Depends(require_auth)])
def delete_project(project_id: str):
    """Delete a project after releasing its conversations.

    Order is confirm-exists → unassign conversations → delete project so a
    missing id is a side-effect-free 404 for unknown projects.

    Pilot limitation (no multi-document transaction): these three steps are not
    atomic. Known TOCTOU windows under concurrent writers:

    - delete+unassign vs assign: another request may pass `_require_project` and
      insert/update a conversation onto this project_id after find_one succeeds
      and before (or after) update_many, leaving an assignment to a project that
      is about to be (or already was) deleted.
    - concurrent deletes: a second deleter may return 404 after conversations
      were already released (idempotent for assignments).

    Stub/fakemongo and the shared `MongoClient` in `rag/mongo.py` do not expose
    Atlas transactions; this pilot does not claim strict referential integrity
    under concurrency. See the follow-up issue linked from PR #133.
    """
    if projects_col.find_one({"project_id": project_id}) is None:
        raise HTTPException(status_code=404, detail="Project not found.")

    conversations_col.update_many(
        {"project_id": project_id},
        {"$set": {"project_id": None}},
    )
    result = projects_col.delete_one({"project_id": project_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Project not found.")
    return {"ok": True}
