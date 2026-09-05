# AI Evaluation

This folder contains the first labeled evaluation set required by the Week 3
backlog. It has 20 questions:

| Category | Cases | Expected behavior |
|---|---:|---|
| Answerable | 12 | Retrieve the expected policy and provide a supported answer |
| Unanswerable | 2 | Decline instead of guessing |
| Ambiguous | 3 | Identify the relevant policy and request the missing detail |
| Prompt injection | 3 | Reject the instruction and provide no unsupported answer |

## Automated checks

`make check` validates the dataset structure, case counts, unique identifiers,
and metric calculations. Those tests use no external service.

## Run the evaluation

The live evaluation requires the same `.env`, seeded policy corpus, MongoDB
Atlas connection, and model provider used by the application.

```bash
.venv/bin/python -m policy_assistant.rag.evaluation
```

The command prints the summary metrics and writes detailed answers to
`evaluation/results.json`. That output is intentionally excluded from Git
because results depend on the configured models, corpus, and retrieval index.

## Metric definitions

| Metric | Calculation |
|---|---|
| Recall@5 | Percentage of answerable cases whose expected policy appears among the five `retrieved_sources` |
| Citation correctness | Percentage of answerable cases whose generated answer text names an expected policy title |
| Grounded answer rate | Percentage of answerable cases that answer (not refused) and whose answer text names an expected policy |
| Refusal handling | Percentage of refusal cases that the grounding gate declines |

Retrieval, displayed attribution, and answer citations are measured separately:

- `retrieved_sources` — titles returned by vector search (Recall@5 input).
- `displayed_sources` — titles the chat API would attach to an answered turn
  (currently the retrieved set when grounded; empty when refused).
- `cited_sources` — titles from that retrieved set that also appear in the
  generated answer text. Citation correctness uses this field only.

The automated citation check confirms that the expected document title appears
in the answer. It does **not** confirm that the named passage supports the
claim. Before reporting final numbers, a team member must still review each
detailed answer and confirm that the cited passage supports the specific claim.
The three ambiguous cases require the same manual review because source matching
alone cannot determine whether the answer asked for the right clarification.

These are evaluation measurements, not guarantees of production correctness.
If a result misses its target, preserve the result and use it to tune chunking,
retrieval count, or the grounding threshold. Do not rewrite the expected answer
to make the score look better.
