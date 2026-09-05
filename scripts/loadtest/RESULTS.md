# Load test results — chat streaming throughput

Measured against the requirement *"the system must serve 10,000 concurrent
users."* All numbers reproducible with the harness in this directory.

## Defining the target

"10,000 concurrent users" is ambiguous. Taking it as 10,000 employees with the
app open, each asking a question every ~2 minutes:

    10,000 / 120s = 83 queries/sec

At ~2.5s per generation that is roughly 210 generations in flight at any moment.
**83 req/s is the pass mark used below.**

## Method

`scripts/loadtest/server.py` runs the real application with three things
stubbed: the model (`LLM_PROVIDER=fake`, canned answer at a configurable
per-token delay), MongoDB (in-memory dict, 15 ms simulated latency), and Atlas
Vector Search (canned passages). The stub also disables the SlowAPI limiter so
every virtual user sharing `127.0.0.1` is not capped by `CHAT_RATE_LIMIT`;
production keeps that limit. `scripts/loadtest/run.py` opens N concurrent
SSE streams and records time-to-first-token and total duration.

The grounding gate is **not** stubbed. Fake embeddings produce meaningless
similarity scores, so instead of faking the gate the test brackets it: one run
where everything generates, one where everything refuses.

Hardware: development laptop (macOS), single uvicorn worker unless stated.
Generation is ~104 tokens at 20 ms = ~2.5 s.

## Finding 1 — the ceiling is the thread pool, and it is not what it looked like

Starlette wraps a sync generator with `iterate_in_threadpool`, which calls
`next()` on it through `anyio.to_thread.run_sync`. A thread is therefore
acquired and released **per yield**, not held for the whole stream. The
constraint is aggregate thread-time: each stream needs ~2.5 s of it, so 40
threads support 40 / 2.5 ≈ 16 streams/sec.

Baseline, anyio default of 40 tokens, everything generates:

| concurrency | ok | fail | TTFB p50 | generation p50 | req/s |
|---|---|---|---|---|---|
| 10 | 10 | 0 | 0.07s | 2.55s | 3.8 |
| 20 | 20 | 0 | 0.07s | 2.58s | 7.5 |
| 40 | 40 | 0 | 0.08s | 2.59s | **14.9** |
| 80 | 80 | 0 | 0.15s | 5.23s | 14.9 |
| 160 | 160 | 0 | 0.23s | 10.46s | 14.9 |

Throughput saturates at **14.9 req/s** — within 7% of the predicted 16 — and
past that point generation time stretches linearly (2.6s → 5.2s → 10.5s as
concurrency doubles). Nothing fails; it queues. That is 18% of the 83 req/s
target.

## Finding 2 — the refusal path is 47x cheaper

Same test with `SIMILARITY_THRESHOLD=1.0`, so the grounding gate declines every
request and no generation happens:

| concurrency | ok | TTFB p50 | req/s |
|---|---|---|---|
| 10 | 10 | 0.04s | 212 |
| 40 | 40 | 0.06s | 630 |
| 160 | 160 | 0.15s | 700 |

**~700 req/s.** Refusals are nearly free because the gate runs before
generation. Real traffic sits between 15 and 700 req/s weighted by the refusal
rate, which is a strong argument for measuring that rate in production — it is
the single biggest factor in both capacity and cost.

## Finding 3 — raising the pool size moves the ceiling almost linearly

Same generation-path test, varying only the thread limiter:

| thread tokens | concurrency | generation p50 | req/s | vs. default |
|---|---|---|---|---|
| 40 (default) | 160 | 10.48s | 14.9 | 1.0x |
| 160 | 160 | 2.85s | 53.7 | 3.6x |
| 320 | 320 | 2.98s | **98.7** | 6.6x |
| 640 | 320 | 3.05s | 96.1 | 6.4x |

About **0.31 req/s per thread**. The 640-token run matches the 320-token run
because concurrency was the limit there, not threads.

Memory cost, measured at 320 concurrent streams:

| | RSS | OS threads |
|---|---|---|
| idle | 70 MB | 7 |
| under load | 103 MB | 327 |

**~105 KB of RSS per thread** — far below the 8 MB virtual stack size, because
Python threads only commit the pages they touch.

## Finding 4 — a client that hangs up on `done` lost its exchange entirely

The load harness returns as soon as it sees the `done` event, which closes the
connection. That is reasonable client behaviour: the answer is complete, so
there is nothing left to read except the follow-up suggestions.

Doing so raised `GeneratorExit` at the suspended `yield` inside `_stream`, and
every statement after that yield was skipped — `_persist`, `put_cached_answer`,
and `log_query` all lived there. The user saw a complete, correct answer that
the server had no record of: not saved to the conversation, not cached, and
absent from the analytics this system is supposed to learn from.

It surfaced as "the cache never hits under load" and was initially mistaken for
a cache bug. The bookkeeping now runs from a `finally` block, which executes
during generator close, so an abandoned stream is still recorded.

This is the same hazard that would have appeared — harder to diagnose — after an
async conversion, where a disconnect becomes task cancellation at an `await`.

## Finding 5 — cached answers are roughly an order of magnitude cheaper

Same concurrency, same questions, differing only in whether the answer cache
was warm:

| path | 80 concurrent | 160 concurrent |
|---|---|---|
| first turn, cache miss (generates) | ~15 req/s | ~36 req/s |
| first turn, cache hit | 210 req/s | 343-522 req/s |
| follow-up turn (has history, never cached) | — | ~20 req/s |

Cached responses run **6-14x** the generated path. Measured hit latency was
0.11s against 3.03s for a generation — a 29x improvement for the user.

Follow-up turns are slower than first turns even on a miss, because a turn with
history pays an extra utility model call to rewrite the question into a
standalone retrieval query. That call is skipped on first turns.

Two caveats. The spread in the cached numbers (210-522 req/s) is wide because at
these levels the harness itself becomes a factor — opening 320 sockets at once
from one Python process produced a TTFB spike that the server did not cause.
And the fake model makes a miss cheaper than reality, so the real-world ratio
between hit and miss is larger, not smaller.

The practical consequence is that **cache hit rate is the main lever on cost**.
At 83 req/s and ~$0.01 per generated query, every 10 points of hit rate is
roughly $300/hour. `query_logs.cache_hit` records the achieved rate so this can
be measured rather than assumed.

## Conclusion

**A single worker with a larger thread pool clears the 83 req/s target.** At 320
tokens it sustains 98.7 req/s with zero failures, 0.17s TTFB, and 103 MB
resident. That is a one-line configuration change, not an architecture change.

`THREADPOOL_TOKENS` now defaults to 100 (~31 req/s/worker), which with 4 workers
projects to ~124 req/s — comfortably over target with headroom for the real-world
factors below. Raise it if measurement says to.

### What this does not prove

- **Real model latency is slower and far more variable.** A p99 generation of
  20s holds thread-time for 8x as long as this test assumes, cutting throughput
  proportionally. This is the largest source of error here.
- **Real Atlas is slower than a 15 ms dict.** Every real round-trip is more
  thread-time on the same budget.
- **A dev laptop is not a t3.micro.** CPU is a real factor: at 99 req/s the
  server encodes ~10,000 SSE JSON payloads per second, all of it contending for
  the GIL. Re-measure on the target instance before trusting these numbers.
- **Cost and provider rate limits bind before compute does.** At 83 req/s and
  ~$0.01/query that is ~$3,000/hour. The binding constraint on this system is
  the budget, not the server.

### Is the async conversion still worth doing?

On this evidence, it is no longer required to hit the target, which changes the
tradeoff. Threads cost ~105 KB each and scale linearly to at least 320; the
async rewrite would touch the streaming path, the provider interface, and the
retrieval helpers, and would introduce cancellation semantics — a client
disconnect becoming task cancellation at an `await` — that are easy to get
subtly wrong.

Given the "maintainable by a team of junior developers" requirement, the
recommendation is to take the configuration change now and treat async as a
later step, justified by measurement rather than by principle. Async remains the
better answer if per-request thread-time grows a lot (slower models, slower
database) or if memory becomes the constraint.

## Reproducing

```bash
# Worst case — every request generates
./.venv/bin/uvicorn scripts.loadtest.server:app --port 8001 --log-level warning &
./.venv/bin/python scripts/loadtest/run.py --concurrency 10 20 40 80 160

# Best case — every request refuses
SIMILARITY_THRESHOLD=1.0 ./.venv/bin/uvicorn scripts.loadtest.server:app --port 8001 &
./.venv/bin/python scripts/loadtest/run.py --concurrency 10 40 80 160

# Vary the thread pool
LOADTEST_THREAD_TOKENS=320 ./.venv/bin/uvicorn scripts.loadtest.server:app --port 8001 &
./.venv/bin/python scripts/loadtest/run.py --concurrency 320
```
