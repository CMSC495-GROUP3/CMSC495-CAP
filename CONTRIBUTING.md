# Working on this project

The [README](README.md) explains what the system is and why it is built the way
it is. This page is the practical side: getting it running, checking a change,
and getting that change merged. If something here is wrong or missing, fix it in
the same PR as the change that made it wrong.

`.agents/skills/CMSC495-CAP/SKILL.md` is the condensed version of this page
for coding agents: stack, layout, commands, conventions, and the rules that
are easy to break. Keep the two in step.

## Ten minutes to a running app

No cloud accounts, API keys, or `.env` needed. This runs the real application
with a fake model and an in-memory database.

Prerequisites: Python 3.11+, Node 20+, and `make`. Docker is only needed for the
full stack.

```bash
git clone https://github.com/CMSC495-GROUP3/Sourcebook.git
cd Sourcebook
make setup          # .venv, Python deps, npm install
make stub           # terminal 1: API on :8000, fake model, in-memory Mongo
make web            # terminal 2: React on :5173 with hot reload
```

Open http://localhost:5173 and log in with the password `dev`.

What is fake in this mode, so you are not surprised:

- **The model.** Every answer is the same canned PTO text, and follow-up
  suggestions are canned too. Retrieval scores are fixed, so every question
  either answers or every question refuses. `make stub REFUSE=1` flips it to
  refusing, which is how you see the refusal card and the escalation button.
- **The database.** Conversations, escalations, and caches live in memory and
  vanish when the API stops.
- **Vector search.** Replaced with canned passages. Atlas Vector Search cannot
  run locally at all.

The stub lives in `scripts/loadtest/server.py`. It patches the real app from the
outside; there is no test-only branch in application code, and there should not
be one.

## Running against the real services

Needed for anything touching retrieval quality, ingestion, or the provider.

1. `cp .env.example .env` and fill it in. The comments in that file say what
   each value is for. You need an OpenAI key, a MongoDB Atlas cluster with
   Vector Search, and an S3 bucket.
2. Generate the two secrets:
   ```bash
   openssl rand -hex 32                                   # JWT_SECRET_KEY
   .venv/bin/python -c "import bcrypt; print(bcrypt.hashpw(b'your-password', bcrypt.gensalt()).decode())"   # APP_PASSWORD_HASH
   ```
   A bcrypt hash contains `$`. Paste it into `.env` with an editor, not `echo`.
   An optional second password goes in `APP_PASSWORD_HASH_2`, hashed the same
   way; the README's Configure section, under the hash one-liner, says what that
   implies before you hand one out.
3. Load the corpus, then create the vector index in the Atlas UI (the README's
   "Load the corpus" section has the exact JSON). The driver cannot create a
   search index; this step is manual and it is the one people forget.
   ```bash
   .venv/bin/python -m policy_assistant.rag.seed_documents     # documents -> S3
   .venv/bin/python -m policy_assistant.rag.embed_documents    # chunk, embed, store
   ```
4. Run it either way:

   | | Command | Use when |
   |---|---|---|
   | Dev mode | `.venv/bin/uvicorn policy_assistant.api.main:app --reload` plus `make web` | changing code |
   | Full stack | `make compose` | checking the Nginx proxy, the Docker build, or what a deploy will run |

   In dev mode the API is at http://localhost:8000 and the OpenAPI console at
   http://localhost:8000/docs, which is the fastest way to poke an endpoint by
   hand. In Compose the app is at http://localhost, served by Caddy in front
   of Nginx; only Caddy publishes ports.

## Checking a change

```bash
make test     # Python tests, about a second
make cov      # same, with a per-file coverage report
make lint     # ruff on Python; ESLint and tsc on the web app
make fmt      # fix what ruff can fix, then format; run before committing
make build    # production web build
make check    # test, lint, build; this is what the CI workflow runs
make audit    # known vulnerabilities in both dependency trees (the Security workflow)
```

Python formatting is enforced. `make fmt` before you commit and CI will not
complain. The rules are in `pyproject.toml`; the version of ruff is pinned in
`requirements/lint.txt` because the formatter's output changes between releases.

### What CI runs

Every PR and every push to `main` triggers three workflows. None needs a secret,
so they run on fork PRs too.

| Workflow | Job | What fails it |
|---|---|---|
| CI | Python lint and format | a `ruff check` finding or an unformatted file |
| CI | Python tests (3.11 through 3.14) | a failing test, or coverage under 80% on any version |
| CI | Evaluation dataset | `evaluation/questions.json` that `load_cases` rejects |
| CI | Web lint, types, build | ESLint, `tsc -b`, or `vite build` |
| CI | Docker images and Compose | either image failing to build, the API image failing to import `policy_assistant.api.main`, an invalid `docker-compose.yml`, or `scripts/test_proxy_chain.py` failing the live Caddy → Nginx → Uvicorn client-IP / rate-limit check |
| CI | Shell, Dockerfile, workflow lint | shellcheck on `scripts/*.sh`, hadolint on both Dockerfiles, actionlint on the workflows, or a `.env`, key, or build output that got committed |
| Security | CodeQL, dependency advisories, dependency review, leaked secrets | a new finding; the accepted-advisory list is in `scripts/audit.sh` |
| PR checks | title, description, labels | a title not in `type: what changed` form, or an empty "What and why" |

The Security workflow also runs every Monday, so a new advisory in an existing
dependency shows up as a failed scheduled run rather than in someone's
unrelated PR.

Branch protection on `main` should require the single check named **CI
status**, which fails unless every CI job succeeded. A job that is skipped
counts as a failure here, so a wrong `if:` or `paths:` condition on a required
job shows up as a red check rather than a silent pass. Requiring that one name
means a job added or renamed in `ci.yml` cannot quietly stop being required.

A fourth workflow, **Live evaluation**, runs the labeled question set against
the real provider and index. It costs money, so it only runs when a maintainer
starts it from the Actions tab, and it needs a repository environment named
`evaluation` holding `OPENAI_API_KEY`, `MONGODB_URI`, and `MONGODB_DB`. See
`evaluation/README.md` for what the numbers mean.

Dependabot opens one grouped PR per ecosystem on Mondays (pip, npm, GitHub
Actions, Docker base images). Review them like any other PR; CI runs on them.

### Writing tests

The suite is in `tests/`. `conftest.py` stubs every external service before the
app is imported, the same way the stub server does. Useful fixtures:

| Fixture | Gives you |
|---|---|
| `client` | a `TestClient` for the whole app |
| `auth` | a header dict with a valid bearer token |
| `retrieval` | control over what vector search returns (`retrieval.passages = make_passages(0.9, 0.7)`) and a count of calls |
| `conversation` | a fresh conversation's `session_id` |

Test the behaviour, not the implementation: assert on the HTTP response, the
SSE events, or what ended up in the fake database. Look at
`tests/test_escalations.py` for the shape of a route test and
`tests/test_chat.py::TestDroppedStream` for driving the streaming generator by
hand.

The fake Mongo (`scripts/loadtest/fakemongo.py`) implements only the operations
the app uses. If you use a new query operator or update form, add it there, and
keep it obviously partial rather than pretending to be complete.

## Where things live

| I want to change... | Look in |
|---|---|
| a tuning knob (threshold, chunk size, TTLs, thread pool) | `policy_assistant/rag/config.py`; every value is env-overridable, defaults live here |
| the answer prompt | `policy_assistant/rag/rag_chain.py` `ANSWER_SYSTEM_PROMPT`, then bump `PROMPT_VERSION` in `policy_assistant/rag/config.py` or cached answers keep serving the old prompt |
| retrieval or the grounding gate | `policy_assistant/rag/rag_chain.py` |
| which model or vendor is used | `policy_assistant/rag/llm.py` only. Add a subclass, register it in `_PROVIDERS`, set `LLM_PROVIDER` |
| how a source format is parsed | `policy_assistant/rag/documents.py` |
| an API endpoint | `policy_assistant/api/routes/`; one file per area, mounted in `policy_assistant/api/main.py` |
| a MongoDB collection or index | `policy_assistant/api/db.py` |
| the chat UI | `web/src/components/Chat/`, state in `web/src/hooks/useChat.ts` |
| the product name or the escalation contact | both `policy_assistant/rag/config.py` and `web/src/config.ts`; they are mirrored, change both |
| the sample corpus | `data/sample-policies/`, then re-run ingestion |

`policy_assistant` is one package and every import is absolute
(`from policy_assistant.rag.config import ...`), so run things from the repo
root as modules: `python -m policy_assistant.rag.embed_documents`,
`uvicorn policy_assistant.api.main:app`. Running a file by path
(`python policy_assistant/rag/embed_documents.py`) puts the wrong directory on
`sys.path` and the package import fails.

## Getting a change merged

1. Branch from `main`: `feat/short-name`, `fix/short-name`, `docs/...`. If you
   do not have write access to the repo, work on a fork; `gh repo fork` sets
   one up and PRs open against `CMSC495-GROUP3/Sourcebook` the same way.
2. Commit in pieces that each make sense alone. Messages follow
   `type: what changed` with the types `feat`, `fix`, `refactor`, `docs`,
   `test`, `chore`, `perf`, `ci`. The body is for *why*, and for anything a
   reader would otherwise have to rediscover: what you measured, what you tried
   that did not work, what a future change must not break. Read
   `git log` for the house style.
3. `make check` before pushing. `make fmt` first if ruff complains.
4. Open the PR. Its title follows the same `type: what changed` rule as
   commits, because it becomes the merge commit subject; a check enforces it.
   The template asks for what a reviewer needs. One approval and green CI to
   merge. Prefer squash only when the commits are noise; otherwise keep them.
5. If the change alters setup, configuration, or behaviour someone would need
   to know about, the README or this page changes in the same PR.

Never commit `.env`, a key, a hash, or a real policy document. `.gitignore`
covers `.env`; the rest is on you.

## Things that will bite you

- **Changing a TTL** (`*_TTL_SECONDS` in `config.py`) fails at startup with
  `IndexOptionsConflict` on an existing database. MongoDB will not alter a TTL
  through `create_index`; drop the index by hand or run `collMod` first.
- **Changing the embedding model** changes the vector dimensions, which are
  baked into the Atlas index. Re-run ingestion and recreate the index.
- **`typescript` stays on 6.0.x** in `web/package.json` because `typescript-eslint`
  pins its peer to `<6.1.0`, so `npm ci` refuses 7.x. Dependabot ignores the
  7.x line; lift that in `.github/dependabot.yml` once `typescript-eslint`
  supports TypeScript 7.
- **Do not deploy under gunicorn `--preload`.** `MongoClient` is not fork-safe
  and the collection handles bind at import. `uvicorn --workers N` is fine. The
  reasoning is in `policy_assistant/rag/mongo.py`.
- **`LLM_PROVIDER=fake` refuses to start with `APP_ENV=production`**, on
  purpose. If a deploy fails with that error, the environment is misconfigured,
  not the code.
- **Connections per process** are `MONGO_MAX_POOL_SIZE` and the cluster limit is
  workers times that. Atlas free tier caps in the low hundreds and fails under
  load rather than at startup. Redo the arithmetic in `policy_assistant/rag/mongo.py` before
  raising either number.
- **Cached answers outlive a prompt fix** unless `PROMPT_VERSION` is bumped. It
  is part of the cache key for exactly this reason.
- **`THREADPOOL_TOKENS` is the chat throughput ceiling.** It was measured, not
  guessed; see `scripts/loadtest/RESULTS.md` before changing it, and re-measure
  after.
- **The fake provider's embeddings are meaningless.** Never use `make stub` to
  judge retrieval quality or to tune `SIMILARITY_THRESHOLD`.

## Debugging

- The OpenAPI console at `/docs` lets you call any endpoint with a token. Log in
  at `POST /api/auth/login`, click Authorize, paste the token.
- SSE by hand:
  `curl -N -X POST localhost:8000/api/chat/stream -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"question":"How much PTO do I get?"}'`
- Escalations queue: `GET /api/escalations?status=open`. Retry a failed webhook
  with `POST /api/escalations/{id}/retry-delivery`. There is no UI for it
  yet.
- Query analytics are in the `query_logs` collection: refused questions grouped
  by `question_hash` are the content gaps, and `best_score` on answered versus
  refused rows is what the threshold should be tuned against.
- API logs go to stdout. In Compose: `docker compose logs -f api`.

## Load testing and deployment

- `make stub` in one terminal, `make loadtest` in another. Method, numbers, and
  caveats in `scripts/loadtest/RESULTS.md`.
- The EC2 host deploys itself: a systemd timer runs `scripts/auto_deploy.sh`
  every two minutes, which fast-forwards to upstream `main` and rebuilds only
  the services whose inputs changed. `scripts/deploy.sh` starts that service
  now, over SSH, and prints its log. Both touch real infrastructure, so read
  them before running them. The host setup (Elastic IP, DuckDNS record,
  security group, `SITE_ADDRESS` in `.env`, the timer) is in the README's
  Deployment section.
