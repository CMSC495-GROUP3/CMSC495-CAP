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

Webhook delivery is tracked on the record (`pending` / `delivered` /
`failed`) and updated after each attempt. Create never waits on the webhook;
failed deliveries can be retried through an authenticated, bounded endpoint
that claims the record before sending so concurrent retries cannot duplicate.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from policy_assistant.api import notify
from policy_assistant.api.db import conversations_col, escalations_col
from policy_assistant.api.limiter import limiter
from policy_assistant.api.routes.deps import require_auth
from policy_assistant.rag.config import (
    ESCALATION_CONTACT,
    ESCALATION_NOTE_MAX_LENGTH,
    ESCALATION_WEBHOOK_LEASE_SECONDS,
    ESCALATION_WEBHOOK_MAX_ATTEMPTS,
)

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


def _public_record(doc: dict | None) -> dict | None:
    """Drop Mongo's `_id` so API responses stay free of it."""
    if doc is None:
        return None
    doc.pop("_id", None)
    return doc


def _apply_delivery_result(escalation_id: str, claimed_at: datetime, success: bool) -> dict | None:
    """Persist an attempt only while its delivery claim is still current."""
    now = datetime.now(UTC)
    return _public_record(
        escalations_col.find_one_and_update(
            {
                "escalation_id": escalation_id,
                "delivery_status": "pending",
                "delivery_claimed_at": claimed_at,
            },
            {
                "$set": {
                    "delivery_status": "delivered" if success else "failed",
                    "delivery_last_attempt_at": now,
                    "delivery_claimed_at": None,
                    "updated_at": now,
                },
                "$inc": {"delivery_attempts": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
    )


def _claim_delivery(escalation_id: str) -> dict | None:
    """Atomically claim failed, legacy, unclaimed, or stale-pending delivery."""
    now = datetime.now(UTC)
    stale_before = now - timedelta(seconds=ESCALATION_WEBHOOK_LEASE_SECONDS)
    return _public_record(
        escalations_col.find_one_and_update(
            {
                "escalation_id": escalation_id,
                "$and": [
                    {
                        "$or": [
                            {"delivery_attempts": {"$exists": False}},
                            {"delivery_attempts": {"$lt": ESCALATION_WEBHOOK_MAX_ATTEMPTS}},
                        ]
                    },
                    {
                        "$or": [
                            {"delivery_status": "failed"},
                            {"delivery_status": {"$exists": False}},
                            {"delivery_status": "pending", "delivery_claimed_at": None},
                            {
                                "delivery_status": "pending",
                                "delivery_claimed_at": {"$lt": stale_before},
                            },
                        ]
                    },
                ],
            },
            {
                "$set": {
                    "delivery_status": "pending",
                    "delivery_claimed_at": now,
                    "updated_at": now,
                },
            },
            return_document=ReturnDocument.AFTER,
        )
    )


def _deliver_in_background(record: dict) -> None:
    """Background task: POST the webhook, then write delivery fields.

    Runs after the employee response is sent. A failure here is recorded on
    the escalation; it is never raised to the client. When no webhook is
    configured, leave the record pending with zero attempts — empty URL is
    intentional non-delivery, not a failed send.
    """
    if not notify.ESCALATION_WEBHOOK_URL:
        return
    claimed = _claim_delivery(record["escalation_id"])
    if claimed is None:
        return
    success = notify.deliver_escalation(claimed)
    _apply_delivery_result(record["escalation_id"], claimed["delivery_claimed_at"], success)


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


def _retry_conflict(existing: dict) -> HTTPException:
    """Explain why a retry was rejected without revealing delivery internals."""
    status = existing.get("delivery_status")
    attempts = existing.get("delivery_attempts", 0)
    if status == "delivered":
        detail = "Webhook already delivered."
    elif status == "pending":
        detail = "Delivery already in progress."
    elif attempts >= ESCALATION_WEBHOOK_MAX_ATTEMPTS:
        detail = "Maximum delivery attempts reached."
    else:
        detail = "Delivery cannot be retried in its current state."
    return HTTPException(status_code=409, detail=detail)


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
        # Non-secret delivery bookkeeping. The webhook URL is never stored.
        "delivery_status": "pending",
        "delivery_attempts": 0,
        "delivery_last_attempt_at": None,
        "delivery_claimed_at": None,
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

    # Runs after the response is sent. See policy_assistant/api/notify.py.
    background.add_task(_deliver_in_background, record)
    return record


@router.post("/escalations/{escalation_id}/retry-delivery", dependencies=[Depends(require_auth)])
@limiter.limit("5/minute")
def retry_delivery(request: Request, escalation_id: str):
    """Re-attempt webhook delivery for a failed escalation.

    Claims the record with an atomic failed→pending transition so two
    concurrent retries cannot both send. The attempt runs in this request so
    the operator sees the updated delivery status; create still uses the
    background path so employees never wait on the webhook.
    """
    if not notify.ESCALATION_WEBHOOK_URL:
        existing = escalations_col.find_one({"escalation_id": escalation_id}, {"_id": 0})
        if not existing:
            raise HTTPException(status_code=404, detail="Escalation not found.")
        raise HTTPException(status_code=409, detail="Webhook delivery is not configured.")

    claimed = _claim_delivery(escalation_id)
    if claimed is None:
        existing = escalations_col.find_one({"escalation_id": escalation_id}, {"_id": 0})
        if not existing:
            raise HTTPException(status_code=404, detail="Escalation not found.")
        raise _retry_conflict(existing)

    success = notify.deliver_escalation(claimed)
    updated = _apply_delivery_result(escalation_id, claimed["delivery_claimed_at"], success)
    if updated is not None:
        return updated
    current = escalations_col.find_one({"escalation_id": escalation_id}, {"_id": 0})
    return _public_record(current) or claimed


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
