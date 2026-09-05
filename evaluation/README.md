# AI Evaluation

This folder holds the labeled evaluation sets used to measure retrieval and
refusal quality against the sample policy corpus.

## Tiers

| Tier | File | Purpose |
|---|---|---|
| `smoke` | `questions.json` | Bounded routine set (20 cases). Inexpensive for repeated live checks. |
| `full` | `questions_full.json` | One supported retrieval question for every sample policy, plus unanswerable, ambiguous, and prompt-injection coverage. |

| Category | Cases | Expected behavior |
|---|---:|---|
| Answerable | 12 | Retrieve the expected policy and provide a supported answer |
| Unanswerable | 2 | Decline instead of guessing |
| Ambiguous | 3 | Identify the relevant policy and request the missing detail |
| Prompt injection | 3 | Reject the instruction and provide no unsupported answer |

The full tier always treats tuition and dress-code questions as answerable because those policies are in the sample corpus.

## Automated checks

`make check` validates both datasets: structure, unique identifiers, metric
calculations, and that every non-empty `expected_sources` title exists in
`data/sample-policies/`. Those tests use no external service and make no paid
calls.

## Run the evaluation

The live evaluation requires the same `.env`, seeded policy corpus, MongoDB
Atlas connection, and model provider used by the application. Choose the tier
explicitly. The runner prints the case count and asks for confirmation before
any paid provider call (`--yes` skips the prompt for CI).

```bash
.venv/bin/python -m policy_assistant.rag.evaluation --tier smoke
.venv/bin/python -m policy_assistant.rag.evaluation --tier full --yes
```

The GitHub Actions workflow "Live evaluation" takes the same `tier` input and
prints the selected case count in the job log before execution.

The command prints the summary metrics and writes detailed answers to
`evaluation/results.json`. That output is intentionally excluded from Git
because results depend on the configured models, corpus, and retrieval index.

## Metric definitions

| Metric | Calculation |
|---|---|
| Recall@5 | Percentage of answerable cases whose expected policy appears among the five retrieved sources |
| Citation correctness | Percentage of answerable cases that cite an expected policy |
| Grounded answer rate | Percentage of answerable cases that answer and cite an expected policy |
| Unsupported refusal handling | Percentage of `unanswerable` cases that the grounding gate declines |
| Prompt-injection grounding-gate refusal | Percentage of `prompt_injection` cases stopped for insufficient grounding; this is not a prompt-resistance score |
| Ambiguous review | Count and case ids of `ambiguous` cases; clarification quality is manual |


Retrieval, displayed attribution, and answer citations are measured separately:

- `retrieved_sources` — titles returned by vector search (Recall@5 input).
- `displayed_sources` — titles the chat API would attach to an answered turn
  (currently the retrieved set when grounded; empty when refused).
- `cited_sources` — titles from that retrieved set that also appear in the
  generated answer text. Citation correctness uses this field only.

The former aggregate `refusal_handling` metric mixed unsupported-policy
refusals with prompt-injection cases. Grounding-gate outcomes are reported
separately, while generated-prose resistance remains an explicit human review.
Empty categories yield `null` rates and
a zero count rather than a misleading 0% or 100%.

Ambiguous cases are never auto-scored for clarification quality. The runner
records only grounding/refusal and source lists, which cannot tell whether the
assistant asked for the right missing detail. The report therefore lists those
case identities under `ambiguous_review` with
`clarification_scoring: "manual"`.

Prompt-injection cases are likewise listed under `prompt_injection_review`.
The automated rate says only whether the grounding gate stopped the case; it
does not claim that a generated response resisted the injected instruction.

The automated citation check confirms that the expected document was cited.
Before reporting final numbers, a team member must also review each detailed
answer and confirm that the cited passage supports the specific claim.

These are evaluation measurements, not guarantees of production correctness.
If a result misses its target, preserve the result and use it to tune chunking,
retrieval count, or the grounding threshold. Do not rewrite the expected answer
to make the score look better.
