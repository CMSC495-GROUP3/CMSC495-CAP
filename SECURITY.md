# Security

## Reporting a vulnerability

Use GitHub's private reporting form:
<https://github.com/CMSC495-GROUP3/Sourcebook/security/advisories/new>

Do not open a public issue. Say what you found, how to reproduce it, and what
you think the impact is. A maintainer will reply on the advisory thread.

## What counts

This is a pilot internal tool with a single shared password, a JWT session,
and a policy corpus that is not confidential in the sample data but would be
in a real deployment. Reports we want:

- Authentication or rate-limit bypass on any `/api` route
- Reading another session's conversation, escalation, or cached answer
- Prompt injection that makes the assistant cite a source it did not retrieve,
  or answer without a citation (the evaluation set has three such cases;
  a fourth that gets through is a bug)
- A secret, hash, or real document committed to the repository
- Server-side request forgery through `ESCALATION_WEBHOOK_URL` or S3 keys

Out of scope: the fake provider and stub server (`LLM_PROVIDER=fake`), which
refuse to run in production by design, and anything that needs the operator's
own `.env`.

## What runs automatically

The Security workflow (`.github/workflows/security.yml`) runs CodeQL for
Python and TypeScript, `pip-audit` and `npm audit` against both dependency
trees, a dependency review on every PR, and a full-history secret scan. It
runs on every PR and push to `main`, and again every Monday so new advisories
surface without a code change.

Accepted advisories are listed, with the reason and the way out, in
`scripts/audit.sh`.
