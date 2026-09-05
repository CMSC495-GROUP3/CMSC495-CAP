"""Conversation CRUD endpoints."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from pymongo import DESCENDING

from policy_assistant.api.db import conversations_col, projects_col
from policy_assistant.api.routes.deps import require_auth

router = APIRouter()

# Visible label bound: enough for a short subject line, not a pasted essay.
CONVERSATION_TITLE_MAX_LENGTH = 200
DEFAULT_CONVERSATION_TITLE = "New conversation"


def _normalize_label(value: str, *, field_name: str, max_length: int) -> str:
    """Strip and collapse whitespace; reject empty or oversized labels."""
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} must be at most {max_length} characters")
    return normalized


class CreateConversationRequest(BaseModel):
    title: str = Field(default=DEFAULT_CONVERSATION_TITLE, max_length=CONVERSATION_TITLE_MAX_LENGTH)
    project_id: str | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        return _normalize_label(
            value,
            field_name="title",
            max_length=CONVERSATION_TITLE_MAX_LENGTH,
        )


class UpdateConversationRequest(BaseModel):
    title: str | None = Field(default=None, max_length=CONVERSATION_TITLE_MAX_LENGTH)
    # project_id is intentionally absent from defaults — we use model_fields_set
    # to distinguish "explicitly set to null (unassign)" from "not included in request".
    project_id: str | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_label(
            value,
            field_name="title",
            max_length=CONVERSATION_TITLE_MAX_LENGTH,
        )


def _serialize(doc: dict) -> dict:
    doc.pop("_id", None)
    return doc


def _require_project(project_id: str | None) -> None:
    """Reject unknown project ids before create/reassign writes.

    This is a point-in-time existence check, not a transactional lock. Between
    this find_one and the later insert_one/update_one, a concurrent delete can
    remove the project (validate+create / validate+reassign TOCTOU). Stub mode
    has no Mongo sessions/transactions; the pilot accepts that race rather than
    claiming atomic referential integrity. See projects.delete_project.
    """
    if project_id is not None and projects_col.find_one({"project_id": project_id}) is None:
        raise HTTPException(status_code=404, detail="Project not found.")


@router.get("/conversations", dependencies=[Depends(require_auth)])
def list_conversations():
    docs = conversations_col.find(
        {},
        {"session_id": 1, "title": 1, "project_id": 1, "updated_at": 1, "_id": 0},
    ).sort("updated_at", DESCENDING)
    return [_serialize(d) for d in docs]


@router.post("/conversations", dependencies=[Depends(require_auth)])
def create_conversation(body: CreateConversationRequest):
    _require_project(body.project_id)
    now = datetime.now(UTC)
    doc = {
        "session_id": str(uuid.uuid4()),
        "title": body.title,
        "project_id": body.project_id,
        "messages": [],
        "created_at": now,
        "updated_at": now,
    }
    conversations_col.insert_one(doc)
    return _serialize(doc)


@router.get("/conversations/{session_id}", dependencies=[Depends(require_auth)])
def get_conversation(session_id: str):
    doc = conversations_col.find_one({"session_id": session_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return _serialize(doc)


@router.patch("/conversations/{session_id}", dependencies=[Depends(require_auth)])
def update_conversation(session_id: str, body: UpdateConversationRequest):
    updates: dict = {"updated_at": datetime.now(UTC)}

    if "title" in body.model_fields_set and body.title is not None:
        updates["title"] = body.title

    # project_id in model_fields_set means the client explicitly sent it.
    # body.project_id == None means "unassign" (remove from project).
    if "project_id" in body.model_fields_set:
        _require_project(body.project_id)
        updates["project_id"] = body.project_id

    result = conversations_col.update_one({"session_id": session_id}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"ok": True}


@router.delete("/conversations/{session_id}", dependencies=[Depends(require_auth)])
def delete_conversation(session_id: str):
    result = conversations_col.delete_one({"session_id": session_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"ok": True}
