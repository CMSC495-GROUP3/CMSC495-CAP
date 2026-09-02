# Week 4 Project Status

**Status date:** September 2, 2026

**Overall condition:** At risk, but recoverable

**Alpha Release target:** September 15, 2026

The application runs locally with its safe offline provider, and the automated
quality gates are operating. The immediate risk is deployment readiness. The
public domain reaches the EC2 host, but the site currently returns a 502
response because the application stack behind Caddy is not yet available.

## Evidence completed

| Result | Evidence |
| --- | --- |
| Document ingestion behavior covered | [Pull Request 2](https://github.com/DanielTsang26/CMSC495-CAP/pull/2) |
| AI evaluation harness established | [Pull Request 3](https://github.com/DanielTsang26/CMSC495-CAP/pull/3) |
| Ingestion and evaluation instructions documented | [Pull Request 20](https://github.com/DanielTsang26/CMSC495-CAP/pull/20) |
| Local smoke test and OpenAI readiness evidence documented | [Pull Request 21](https://github.com/DanielTsang26/CMSC495-CAP/pull/21) |
| Complete repository checks | `make check` passed locally and required GitHub checks passed |
| Normal application path | 13 of 13 smoke checks passed |
| Forced refusal and escalation path | 5 of 5 smoke checks passed |

The 18 smoke checks cover authentication, conversation creation, supported
answers, citations, confidence reporting, persistence, refusal behavior, human
escalation, duplicate escalation protection, and an offline prompt injection
scenario. The offline provider does not establish live OpenAI quality.

## Work in review

| Item | Condition | Next decision |
| --- | --- | --- |
| Pull Request 20 | Awaiting team review | Reviewer approves or requests corrections |
| Pull Request 21 | Awaiting team review | Reviewer approves or requests corrections |
| Week 3 design terminology | Partially corrected | Editable Word source is required for the remaining repair |

Only a maintainer should merge approved work into the team repository.

## Team responsibilities

These entries reflect assignments or assistance volunteered in team
communications. They do not create new commitments.

| Team member | Primary responsibility | Current focus |
| --- | --- | --- |
| Taylor Shahan | Architecture and technical design | Architecture, CI, deployment configuration, and technical coordination |
| Daniel Tsang | Backend routing and rate limiting | Backend development, review, and repository administration |
| Dominick Reba | User interface and user experience | Frontend design and implementation |
| George Struder | AWS and MongoDB administration | EC2, Docker, network access, SSH access, MongoDB, and deployment |
| Christopher Richardson | AI integration, testing, and documentation | Ingestion tests, AI evaluation, smoke verification, documentation, and later OpenAI activation |
| Gavin W | Frontend and general development support | Frontend implementation, styling, and available support |
| RobN | Testing, documentation, code review, and proxy server support | Design review, synthetic employee data support, testing, code and pull request review, and the former proxy server responsibility |
| ~~Troy Shurn~~ | ~~Former proxy server responsibility~~ | ~~No longer enrolled; responsibility reassigned to RobN~~ |

## Deployment readiness

| Control | Current condition | Responsible area |
| --- | --- | --- |
| Elastic IP | Confirmed | AWS administration |
| DuckDNS record | Confirmed | Architecture and deployment |
| Ports 80 and 443 | Reported open | AWS administration |
| Port 22 | Access setup remains | AWS administration |
| Ports 3000 and 8000 | Must remain closed publicly | AWS administration |
| Latest `main` on EC2 | Not yet confirmed | Deployment |
| `SITE_ADDRESS` in private EC2 environment | Not yet confirmed | Deployment |
| `JWT_SECRET_KEY` in private EC2 environment | Not yet confirmed | Deployment |
| Application password hash | Not yet confirmed | Deployment |
| MongoDB connection and network access | Not yet confirmed | MongoDB administration |
| S3 configuration | In progress | AWS administration |
| Docker Compose application health | Not ready; public site returns 502 | Deployment |
| OpenAI project and key | Deliberately deferred | AI integration |

Actual credentials belong only in the untracked `.env` file on EC2.
`.env.example` must contain placeholders. Public SSH keys may be provided to
the EC2 administrator; private SSH keys must never be shared.

## Decisions and dependencies

1. The EC2 application stack must become healthy before deployed testing begins.
2. Pull Requests 20 and 21 require team review before integration.
3. The editable Week 3 design source is required before its remaining terminology
   and section placement can be repaired safely.
4. Live OpenAI activation remains last so infrastructure problems cannot consume
   paid model usage.

## Next actions

1. Confirm the EC2 checkout is synchronized with the latest `main`.
2. Complete the private EC2 environment configuration.
3. Run `docker compose up -d --build`.
4. Inspect `docker compose ps` and the Caddy, frontend, and backend logs.
5. Confirm HTTPS, login, chat, citation, refusal, and escalation behavior.
6. Obtain team review for Pull Requests 20 and 21.
7. Configure the dedicated OpenAI project, project key, and spending controls.
8. Run the integrated AI evaluation against the approved corpus.
9. Publish the defect report, retest corrections, and assemble Alpha evidence.

## Release gate

The Alpha evidence must show that the integrated system can retrieve approved
policy material, answer with citations, refuse unsupported questions, preserve
the prompt injection protections, and provide a human escalation route. Any
remaining limitation must be named rather than concealed.
