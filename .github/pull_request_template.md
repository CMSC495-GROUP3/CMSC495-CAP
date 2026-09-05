<!-- Title: `type: what changed`, lower-case, same types as commits
     (feat, fix, refactor, docs, test, chore, perf, ci). CI checks it. -->

## What and why

<!--
Issue Description: State the observed defect, risk, or requirement; expected
versus actual behavior; impact; and scope. Link the issue with "Closes #N" only
when this PR should satisfy all of its acceptance criteria.

Root Cause: State the traced technical or process cause, not just the symptom.
Label an unproven cause as an inference and identify the missing evidence.

Corrective Action: Explain where the repair or enforcement now lives, what
behavior it preserves, and what related work is deliberately out of scope.

Keep the visible write-up concise, but include enough detail that a reviewer
does not need chat history to reconstruct the decision.
-->

## How to check it

<!--
Objective Evidence: Give reproducible commands and observable results tied to
the current head SHA. Distinguish passed, pending, skipped, not run, and
inconclusive checks. `make stub` + `make web` is enough for most UI changes.
Never claim manual, production, or CI evidence that was not actually obtained.
-->

- [ ] `make check` passes locally (tests, ruff, ESLint, tsc, build)
- [ ] Tests added or updated for the behaviour that changed; `make cov` is still over 80%
- [ ] Tested by hand: <!-- what, how -->
- [ ] README or CONTRIBUTING updated if this changes setup, config, or behaviour worth knowing
- [ ] If answers or refusals change: `evaluation/questions.json` updated, or a note here on why not
- [ ] No `.env`, key, password hash, or real policy document in the diff

## Anything the reviewer should look at closely

<!--
Risks and Review Gate: Identify the part you are least sure about, tradeoffs,
residual risks, dependencies, merge order, required reviewers, deployment or
on-host validation, and the exact next action. Delete this section if none.
-->
