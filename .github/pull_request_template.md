<!-- Title: `type: what changed`, lower-case, same types as commits
     (feat, fix, refactor, docs, test, chore, perf, ci). CI checks it. -->

## What and why

<!-- One paragraph. What changed, and what problem it solves. Link the issue or
     requirement if there is one. -->

## How to check it

<!-- Steps a reviewer can follow. `make stub` + `make web` is enough for most
     UI changes. Say which of these you ran. -->

- [ ] `make check` passes locally (tests, ruff, ESLint, tsc, build)
- [ ] Tests added or updated for the behaviour that changed; `make cov` is still over 80%
- [ ] Tested by hand: <!-- what, how -->
- [ ] README or CONTRIBUTING updated if this changes setup, config, or behaviour worth knowing
- [ ] If answers or refusals change: `evaluation/questions.json` updated, or a note here on why not
- [ ] No `.env`, key, password hash, or real policy document in the diff

## Anything the reviewer should look at closely

<!-- The part you're least sure about, or a tradeoff you made. Delete if none. -->
