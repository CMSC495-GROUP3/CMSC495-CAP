"""Conversation CRUD endpoints."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from pymongo import DESCENDING

from db import conversations_col
from routes.deps import require_auth

router = APIRouter()


class CreateConversationRequest(BaseModel):
    title: str = "New conversation"
    project_id: str | None = None


class UpdateConversationRequest(BaseModel):
    title: str | None = None
    # project_id is intentionally absent from defaults — we use model_fields_set
    # to distinguish "explicitly set to null (unassign)" from "not included in request".
    project_id: str | None = None


def _serialize(doc: dict) -> dict:
    doc.pop("_id", None)
    return doc


@router.get("/conversations", dependencies=[Depends(require_auth)])
def list_conversations():
    docs = conversations_col.find(
        {},
        {"session_id": 1, "title": 1, "project_id": 1, "updated_at": 1, "_id": 0},
    ).sort("updated_at", DESCENDING)
    return [_serialize(d) for d in docs]


@router.post("/conversations", dependencies=[Depends(require_auth)])
def create_conversation(body: CreateConversationRequest):
    now = datetime.now(timezone.utc)
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
    updates: dict = {"updated_at": datetime.now(timezone.utc)}

    if "title" in body.model_fields_set and body.title is not None:
        updates["title"] = body.title

    # project_id in model_fields_set means the client explicitly sent it.
    # body.project_id == None means "unassign" (remove from project).
    if "project_id" in body.model_fields_set:
        updates["project_id"] = body.project_id

    result = conversations_col.update_one(
        {"session_id": session_id}, {"$set": updates}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"ok": True}


@router.delete("/conversations/{session_id}", dependencies=[Depends(require_auth)])
def delete_conversation(session_id: str):
    result = conversations_col.delete_one({"session_id": session_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"ok": True}
