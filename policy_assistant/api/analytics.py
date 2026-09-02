"""Query logging — the substrate for "learn from interaction patterns".

One record per chat request. Nothing here changes an answer; it records what
happened so the system can be improved from evidence rather than from guesses.

Three things it is designed to answer:

1. **What can the assistant not answer?** Refusals grouped by question hash are
   a ranked list of the documents HR should write next. This is the closest
   thing the system has to learning: the corpus improves because the logs said
   where it was thin.
2. **What gets asked repeatedly?** High-count question hashes are the FAQ, and
   the questions worth curating a canonical answer for.
3. **Is the threshold set correctly?** The score distribution of answered versus
   refused questions is exactly what SIMILARITY_THRESHOLD should be tuned
   against, and it cannot be collected any other way.

## Logging never breaks a chat request

Every write is wrapped. An analytics failure must not turn a working answer into
an error, so failures are logged and swallowed. That is a deliberate exception
to the project's usual rule against swallowing errors: the alternative is losing
a user's answer to a bookkeeping problem.
"""

import logging
from datetime import UTC, datetime

from policy_assistant.api.db import query_logs_col
from policy_assistant.rag.cache import question_hash

logger = logging.getLogger(__name__)

# Questions are stored to build the content-gap and FAQ lists. Truncated because
# the useful signal is in the first line, and the raw text is employee-entered.
MAX_QUESTION_LENGTH = 500


def log_query(
    *,
    session_id: str | None,
    question: str,
    condensed_question: str,
    passages: list[dict],
    refused: bool,
    sources: list[str],
    cache_hit: str | None,
    latency_ms: int,
) -> None:
    """Record one chat request. Never raises.

    `cache_hit` is "answer", "embedding", or None, so the measured hit rate can
    be reported — that number is what makes the cost projection in
    scripts/loadtest/RESULTS.md defensible.
    """
    try:
        scores = [p.get("score", 0.0) for p in passages]
        query_logs_col.insert_one(
            {
                "created_at": datetime.now(UTC),
                "session_id": session_id,
                "question_raw": question[:MAX_QUESTION_LENGTH],
                "question_condensed": condensed_question[:MAX_QUESTION_LENGTH],
                # Groups repeats of the same question regardless of casing/spacing.
                "question_hash": question_hash(condensed_question),
                "best_score": max(scores) if scores else None,
                "mean_score": (sum(scores) / len(scores)) if scores else None,
                "passage_count": len(passages),
                "refused": refused,
                "sources": sources,
                "cache_hit": cache_hit,
                "latency_ms": latency_ms,
            }
        )
    except Exception:
        logger.exception("Failed to write query log for session %s", session_id)
