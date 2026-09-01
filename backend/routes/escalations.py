"""Escalation endpoints — hand a question to a person.

The refusal message tells the employee to check with People Operations. This
is the path that does it. An escalation is tied to one assistant turn in a
stored conversation, and the question is copied from that record rather than
taken from the request. The server already holds what was asked and answered,
and a client that could supply its own text could file a complaint about an
exchange that never happened.

Two callers:

- The chat UI creates one from the refusal card, or from under an answer that
  did not help. `reason` records which.
- Whoever handles them lists the open ones and marks them resolved. There is
  no dedicated UI for that yet; the endpoints are enough for a script or for a
  webhook-fed channel.

Escalating the same message twice returns the first record rather than
creating a second. A double click should not file two tickets. The check on
the message is the fast path; the unique index on (session_id, message_index)
is what holds when two requests race past it.
"""

import os
import sys
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from pymongo import DESCENDING
from pymongo.errors import DuplicateKeyError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from config import ESCALATION_CONTACT, ESCALATION_NOTE_MAX_LENGTH
from db import conversations_col, escalations_col
from limiter import limiter
from notify import deliver_escalation
from routes.deps import require_auth

router = APIRouter()

EscalationReason = Literal["refused", "unhelpful"]
EscalationStatus = Literal["open", "resolved"]

# Stored with the escalation so a handler sees the exchange without opening the
# conversation. Bounded because answers can be long.
ANSWER_EXCERPT_LENGTH = 1000

# A conversation's first turn is the user's, so the earliest assistant turn is
# at index 1 and every assistant turn is preceded by a user turn.
FIRST_ASSISTANT_INDEX = 1


class CreateEscalationRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64)
    message_index: int = Field(..., ge=FIRST_ASSISTANT_INDEX)
    reason: EscalationReason
    note: str | None = Field(None, max_length=ESCALATION_NOTE_MAX_LENGTH)


class UpdateEscalationRequest(BaseModel):
    status: EscalationStatus
    resolution: str | None = Field(None, max_length=ESCALATION_NOTE_MAX_LENGTH)


def _escalated_turn(session_id: str, message_index: int) -> tuple[dict, dict]:
    """Return (user turn, assistant turn) for the message being escalated,
    validating that the pair exists and has the expected roles."""
    conversation = conversations_col.find_one({"session_id": session_id}, {"_id": 0, "messages": 1})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    messages = conversation.get("messages", [])
    if message_index >= len(messages):
        raise HTTPException(status_code=400, detail="No message at that position.")

    assistant = messages[message_index]
    asked = messages[message_index - 1]
    if assistant.get("role") != "assistant" or asked.get("role") != "user":
        raise HTTPException(
            status_code=400,
            detail="Only an assistant reply to a question can be escalated.",
        )
    return asked, assistant


def _existing_escalation(assistant: dict, session_id: str, message_index: int) -> dict | None:
    """The record already filed for this message, if any."""
    existing_id = assistant.get("escalation_id")
    if existing_id:
        found = escalations_col.find_one({"escalation_id": existing_id}, {"_id": 0})
        if found:
            return found
    return escalations_col.find_one(
        {"session_id": session_id, "message_index": message_index}, {"_id": 0}
    )


@router.post("/escalations", dependencies=[Depends(require_auth)])
@limiter.limit("5/minute")
def create_escalation(request: Request, body: CreateEscalationRequest, background: BackgroundTasks):
    asked, assistant = _escalated_turn(body.session_id, body.message_index)

    existing = _existing_escalation(assistant, body.session_id, body.message_index)
    if existing:
        return existing

    now = datetime.now(UTC)
    record = {
        "escalation_id": uuid.uuid4().hex,
        "status": "open",
        "reason": body.reason,
        "contact": ESCALATION_CONTACT,
        "session_id": body.session_id,
        "message_index": body.message_index,
        "question": asked.get("content", ""),
        "answer_excerpt": (assistant.get("content") or "")[:ANSWER_EXCERPT_LENGTH],
        "refused": bool(assistant.get("refused", False)),
        "confidence": assistant.get("confidence"),
        "sources": assistant.get("sources") or [],
        "note": (body.note or "").strip() or None,
        "resolution": None,
        "created_at": now,
        "updated_at": now,
        "resolved_at": None,
    }
    # insert_one adds _id to the dict it is given; insert a copy so the record
    # returned to the client stays free of it.
    try:
        escalations_col.insert_one(dict(record))
    except DuplicateKeyError:
        # A concurrent request won the race. Return its record; it also owns
        # the webhook delivery, so nothing is sent from here.
        return escalations_col.find_one(
            {"session_id": body.session_id, "message_index": body.message_index},
            {"_id": 0},
        )

    # Mark the message so the UI can show "already sent" when the conversation
    # is reopened, and so a repeat request finds the record above.
    conversations_col.update_one(
        {"session_id": body.session_id},
        {"$set": {f"messages.{body.message_index}.escalation_id": record["escalation_id"]}},
    )

    # Runs after the response is sent. See backend/notify.py.
    background.add_task(deliver_escalation, record)
    return record


@router.get("/escalations", dependencies=[Depends(require_auth)])
def list_escalations(
    status: EscalationStatus | None = None,
    session_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    """Newest first. Filter by status for the open queue, or by session to
    show what a conversation already sent."""
    query: dict = {}
    if status:
        query["status"] = status
    if session_id:
        query["session_id"] = session_id
    items = list(
        escalations_col.find(query, {"_id": 0}).sort("created_at", DESCENDING).limit(limit)
    )
    return {"items": items, "total": escalations_col.count_documents(query)}


@router.get("/escalations/{escalation_id}", dependencies=[Depends(require_auth)])
def get_escalation(escalation_id: str):
    doc = escalations_col.find_one({"escalation_id": escalation_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Escalation not found.")
    return doc


@router.patch("/escalations/{escalation_id}", dependencies=[Depends(require_auth)])
def update_escalation(escalation_id: str, body: UpdateEscalationRequest):
    now = datetime.now(UTC)
    updates: dict = {
        "status": body.status,
        "updated_at": now,
        "resolved_at": now if body.status == "resolved" else None,
    }
    # Sent explicitly (even as null) means "set it"; absent means "leave it".
    if "resolution" in body.model_fields_set:
        updates["resolution"] = body.resolution

    result = escalations_col.update_one({"escalation_id": escalation_id}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Escalation not found.")
    return escalations_col.find_one({"escalation_id": escalation_id}, {"_id": 0})
