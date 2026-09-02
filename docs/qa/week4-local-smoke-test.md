# Week 4 Local Smoke Test Record

## Purpose

This record captures the first repeatable integration check of the Policy Assistant after the Week 3 baseline. The run used the repository's offline stub configuration, so it exercised the real FastAPI routes and React build without contacting AWS, MongoDB Atlas, or OpenAI.

## Test configuration

| Field | Value |
| --- | --- |
| Test date | September 2, 2026 |
| Code baseline | `a6fc412` on upstream `main` |
| Backend | FastAPI through `make stub` |
| Frontend | Vite development server through `make web` |
| Model | Deterministic fake provider |
| Database | In memory fake MongoDB implementation |
| Credentials | Local development password only; no cloud secrets used |
| Primary command | `make check` |

## Automated baseline

`make check` completed successfully before the smoke test.

| Gate | Result |
| --- | --- |
| Python tests | Pass, 97 tests |
| Ruff lint | Pass |
| Ruff formatting check | Pass |
| ESLint | Pass |
| TypeScript compilation | Pass |
| Vite production build | Pass |

## Normal answer path

| ID | Verification | Result | Evidence |
| --- | --- | --- | --- |
| SMK 01 | Frontend application shell is served | Pass | HTTP 200 and React root element present |
| SMK 02 | Backend health endpoint responds | Pass | HTTP 200 with `status: ok` |
| SMK 03 | Invalid password is rejected | Pass | HTTP 401 |
| SMK 04 | Valid local password is accepted | Pass | HTTP 200 and bearer token returned |
| SMK 05 | Protected route rejects a missing token | Pass | HTTP 401 |
| SMK 06 | Conversation can be created | Pass | Session identifier returned |
| SMK 07 | Supported policy question receives an answer | Pass | Response was not refused |
| SMK 08 | Supported answer includes a citation | Pass | `Paid Time Off (PTO) Policy` returned |
| SMK 09 | Supported answer includes confidence | Pass | Confidence value 76 |
| SMK 10 | Conversation messages are persisted | Pass | User and assistant messages returned |
| SMK 11 | Unhelpful answer can be escalated | Pass | Open escalation record created |
| SMK 12 | Duplicate escalation does not create a duplicate record | Pass | Original escalation identifier returned |
| SMK 13 | Stub path does not execute a prompt injection instruction | Pass | Deterministic provider returned its controlled response |

## Refusal and escalation path

The backend was restarted with `make stub REFUSE=1` to force the insufficient evidence path.

| ID | Verification | Result | Evidence |
| --- | --- | --- | --- |
| SMK 14 | Insufficient evidence produces a refusal | Pass | `refused` returned as true |
| SMK 15 | Refusal contains no citation | Pass | Empty source list |
| SMK 16 | Refusal contains no generated follow up questions | Pass | Empty follow up list |
| SMK 17 | Refusal is persisted for later escalation | Pass | Refused assistant message stored in the conversation |
| SMK 18 | Refused answer can be escalated to a person | Pass | Open escalation record created with reason `refused` |

## Result

All 18 local smoke checks passed. The result supports moving to interactive user interface verification and deployed HTTPS verification when the relevant environment is available.

## Observations and limitations

1. The current `passlib` and `bcrypt` combination prints a compatibility warning while creating the local password hash. Authentication still starts and both valid and invalid login behavior passed.
2. npm reports that the inherited `http-proxy` environment configuration will be unsupported in a future npm major version. It did not prevent installation, linting, compilation, or build completion.
3. The production build reports a JavaScript chunk above 500 kB. This is a performance optimization opportunity, not an Alpha blocking failure.
4. Frontend availability was verified through its HTTP response and the production build. A complete visual clickthrough remains pending on a developer machine or the deployed HTTPS site.
5. The prompt injection check used the deterministic fake provider. It verifies the offline request path, not the behavior of the live OpenAI model. Live model safety must be evaluated after the controlled OpenAI project is configured.
6. This run did not contact AWS, MongoDB Atlas, OpenAI, or the EC2 deployment and makes no claim about those services.

## Next verification gate

Run the same functional sequence through the rendered interface, then repeat it against the deployed HTTPS application. Record defects separately and rerun failed checks after correction.
