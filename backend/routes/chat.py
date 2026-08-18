"""Chat endpoints — streaming and non-streaming grounded question answering.

Both paths enforce the same grounding rule: retrieve first, check the best
passage against the similarity threshold, and only call the model if retrieval
cleared it. A refusal costs no generation tokens.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Generator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from config import REFUSAL_MESSAGE  # noqa: E402
from llm import get_provider  # noqa: E402
from rag_chain import (  # noqa: E402
    answer_question,
    build_messages,
    cited_sources,
    condense_question,
    confidence_score,
    generate_follow_ups,
    is_grounded,
    retrieve_passages,
)

from db import conversations_col
from routes.deps import require_auth

logger = logging.getLogger(__name__)
router = APIRouter()


class Message(BaseModel):
    role: str
    content: str
    sources: list[str] = []


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=5000)
    session_id: str | None = None
    chat_history: list[Message] = Field(default=[], max_length=100)


class ChatResponse(BaseModel):
    answer: str
    sources: list[str]
    confidence: int | None
    follow_ups: list[str]
    refused: bool
    session_id: str | None


def _history(body: ChatRequest) -> list[dict]:
    return [
        {"role": m.role, "content": m.content, "sources": m.sources}
        for m in body.chat_history
    ]


def _persist(session_id: str | None, question: str, answer: str,
             sources: list[str], confidence: int | None, refused: bool) -> None:
    """Append one exchange to the conversation record.

    Sources and confidence are stored with the assistant message so that
    reopening a past conversation restores its citations, not just its text.
    """
    if not session_id:
        return

    conversations_col.update_one(
        {"session_id": session_id},
        {
            "$push": {
                "messages": {
                    "$each": [
                        {"role": "user", "content": question},
                        {
                            "role": "assistant",
                            "content": answer,
                            "sources": sources,
                            "confidence": confidence,
                            "refused": refused,
                        },
                    ]
                }
            },
            "$set": {"updated_at": datetime.now(timezone.utc)},
        },
        upsert=True,
    )


# ── Non-streaming ─────────────────────────────────────────────────────────────

@router.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_auth)])
def chat(body: ChatRequest):
    result = answer_question(body.question, chat_history=_history(body))

    _persist(
        body.session_id,
        body.question,
        result["answer"],
        result["sources"],
        result["confidence"],
        result["refused"],
    )

    return ChatResponse(
        answer=result["answer"],
        sources=result["sources"],
        confidence=result["confidence"],
        follow_ups=result["follow_ups"],
        refused=result["refused"],
        session_id=body.session_id,
    )


# ── Streaming ─────────────────────────────────────────────────────────────────

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _stream(body: ChatRequest) -> Generator[str, None, None]:
    """Sync SSE generator.

    FastAPI runs sync routes in a thread pool, so blocking here does not stall
    the event loop. Each yield pushes one SSE message to the browser.
    """
    history = _history(body)

    retrieval_query = condense_question(body.question, history)
    passages = retrieve_passages(retrieval_query)
    confidence = confidence_score(passages)

    # Grounding gate — below the threshold we decline without generating.
    if not is_grounded(passages):
        logger.info(
            "Refused: best score %.3f below threshold for session %s",
            max((p.get("score", 0.0) for p in passages), default=0.0),
            body.session_id,
        )
        yield _sse({"chunk": REFUSAL_MESSAGE})
        yield _sse({"done": True, "sources": [], "confidence": confidence, "refused": True})
        _persist(body.session_id, body.question, REFUSAL_MESSAGE, [], confidence, True)
        return

    sources = cited_sources(passages)
    messages = build_messages(body.question, passages, history)

    full_answer = ""
    try:
        for delta in get_provider().stream(messages, role="answer", temperature=0):
            full_answer += delta
            yield _sse({"chunk": delta})
    except Exception:
        logger.exception("Generation failed for session %s", body.session_id)
        yield _sse({"error": "An error occurred while generating the response."})
        return

    # Sources and confidence are already known — send them the moment the
    # answer finishes rather than waiting on the follow-up call.
    yield _sse({"done": True, "sources": sources, "confidence": confidence, "refused": False})

    # Follow-ups need a second model call, so they arrive as their own event.
    yield _sse({"follow_ups": generate_follow_ups(body.question, full_answer)})

    _persist(body.session_id, body.question, full_answer, sources, confidence, False)


@router.post("/chat/stream", dependencies=[Depends(require_auth)])
def chat_stream(body: ChatRequest):
    return StreamingResponse(
        _stream(body),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # tell Nginx not to buffer the stream
        },
    )


class FollowUpsRequest(BaseModel):
    question: str = Field(..., max_length=5000)
    answer: str = Field(..., max_length=10000)


@router.post("/chat/follow-ups", dependencies=[Depends(require_auth)])
def get_follow_ups(body: FollowUpsRequest):
    """Regenerate follow-ups — used when reopening a past conversation."""
    return {"follow_ups": generate_follow_ups(body.question, body.answer)}
