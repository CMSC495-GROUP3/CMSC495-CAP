"""Escalation delivery — makes a stored escalation reach a person.

The database is the system of record; this module only carries the record to
wherever someone will see it. Delivery runs as a FastAPI background task after
the response is sent, so the employee never waits on a third-party HTTP call,
and a failure here is logged rather than raised. The escalation is already
stored, and a webhook outage must not turn a successful hand-off into an error.

The payload has a top-level `text` field so Slack and Teams incoming webhooks
render it as a message with no adapter. Any other receiver can read the full
`escalation` object beside it.
"""

import json
import logging
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from config import (
    ESCALATION_WEBHOOK_TIMEOUT_SECONDS,
    ESCALATION_WEBHOOK_URL,
)

logger = logging.getLogger(__name__)

# The chat message shows the question in full and the note in full; the answer
# is trimmed because a handler wants to know what was asked, not reread the
# assistant's reply.
_SUMMARY_ANSWER_LENGTH = 300


def format_summary(record: dict) -> str:
    """One readable message for a chat channel or an inbox."""
    what = "Assistant declined to answer" if record.get("refused") else "Answer did not help"
    lines = [
        f"Policy question escalated to {record.get('contact', 'a person')} "
        f"(ref {record['escalation_id'][:8]})",
        f"{what}.",
        f"Question: {record.get('question', '')}",
    ]
    if record.get("note"):
        lines.append(f"Employee note: {record['note']}")
    if not record.get("refused") and record.get("answer_excerpt"):
        lines.append(f"Assistant said: {record['answer_excerpt'][:_SUMMARY_ANSWER_LENGTH]}")
    if record.get("sources"):
        lines.append("Cited: " + ", ".join(record["sources"]))
    return "\n".join(lines)


def deliver_escalation(record: dict, webhook_url: str | None = None) -> bool:
    """POST the escalation to the webhook. Returns True only on a 2xx.

    `webhook_url` defaults to the configured one; tests pass it explicitly.
    Never raises.
    """
    url = ESCALATION_WEBHOOK_URL if webhook_url is None else webhook_url
    if not url:
        return False

    payload = {"text": format_summary(record), "escalation": record}
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, default=str).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        # urlopen raises HTTPError for any status outside 2xx, so reaching the
        # end of the block means the receiver accepted it.
        with urllib.request.urlopen(request, timeout=ESCALATION_WEBHOOK_TIMEOUT_SECONDS):
            return True
    except urllib.error.HTTPError as error:
        logger.error("Escalation %s webhook returned %s", record["escalation_id"], error.code)
    except (urllib.error.URLError, OSError, ValueError):
        logger.exception(
            "Escalation %s stored but webhook delivery failed", record["escalation_id"]
        )
    return False
