"""Load-test client — opens N concurrent SSE streams and reports latency.

    ./.venv/bin/python scripts/loadtest/run.py --concurrency 20 40 80 160

Each virtual user posts one question to /api/chat/stream and reads the response
to completion, recording:

    TTFB    time to the first token — what the user perceives as responsiveness
    total   time until the `done` event

The number to watch is TTFB as concurrency rises. If requests are queueing for a
worker rather than being served, TTFB climbs while the per-stream generation
time stays flat — that gap is the queue, and it is the signal this test exists
to find.

Start the stubbed server first (see scripts/loadtest/server.py).
"""

import argparse
import asyncio
import json
import statistics
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

import httpx

QUESTIONS = [
    "How many PTO days do I get in my first year?",
    "How long do I have to enroll in benefits?",
    "What is the 401(k) company match?",
    "What is the meal limit when travelling?",
    "How much parental leave do I get?",
]


def fetch_token(stream_url: str, password: str) -> str:
    """Log in once so the token carries whatever claims require_auth expects.

    One bcrypt check before the run is fine; keeping it out of the timed loop
    is what matters.
    """
    parts = urlsplit(stream_url)
    login_url = urlunsplit((parts.scheme, parts.netloc, "/api/auth/login", "", ""))
    response = httpx.post(login_url, json={"password": password}, timeout=30.0)
    response.raise_for_status()
    return response.json()["access_token"]


class Result:
    __slots__ = ("cached", "chunks", "error", "total", "ttfb")

    def __init__(self, ttfb=None, total=None, error=None, chunks=0, cached=False):
        self.ttfb, self.total, self.error = ttfb, total, error
        self.chunks, self.cached = chunks, cached


async def one_stream(
    client: httpx.AsyncClient, url: str, token: str, question: str, session_id: str
) -> Result:
    started = time.perf_counter()
    ttfb = None
    chunks = 0
    try:
        async with client.stream(
            "POST",
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"question": question, "session_id": session_id},
            timeout=httpx.Timeout(180.0),
        ) as response:
            if response.status_code != 200:
                return Result(error=f"HTTP {response.status_code}")
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = json.loads(line[6:])
                if "chunk" in payload:
                    chunks += 1
                    if ttfb is None:
                        ttfb = time.perf_counter() - started
                elif "error" in payload:
                    return Result(error=payload["error"])
                elif payload.get("done"):
                    return Result(
                        ttfb=ttfb,
                        total=time.perf_counter() - started,
                        chunks=chunks,
                        cached=bool(payload.get("cached")),
                    )
    except Exception as exc:
        return Result(error=f"{type(exc).__name__}: {exc}")
    # Stream ended without a `done` event.
    return Result(
        ttfb=ttfb,
        total=time.perf_counter() - started,
        chunks=chunks,
        error=None if chunks else "no data received",
    )


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(round(p / 100 * (len(ordered) - 1)), len(ordered) - 1)
    return ordered[index]


async def run_level(url: str, token: str, concurrency: int, run_id: str) -> dict:
    limits = httpx.Limits(
        max_connections=concurrency + 10, max_keepalive_connections=concurrency + 10
    )
    async with httpx.AsyncClient(limits=limits) as client:
        wall_start = time.perf_counter()
        results = await asyncio.gather(
            *[
                # Session ids must be unique per run. Reusing them means the second
                # run's conversations already have history, which correctly
                # disqualifies them from the first-turn answer cache — and would
                # silently measure the uncached path while looking like a cache test.
                one_stream(
                    client,
                    url,
                    token,
                    QUESTIONS[i % len(QUESTIONS)],
                    f"load-{run_id}-{concurrency}-{i}",
                )
                for i in range(concurrency)
            ]
        )
        wall = time.perf_counter() - wall_start

    ok = [r for r in results if r.error is None]
    ttfbs = [r.ttfb for r in ok if r.ttfb is not None]
    totals = [r.total for r in ok if r.total is not None]
    errors: dict[str, int] = {}
    for r in results:
        if r.error:
            errors[r.error] = errors.get(r.error, 0) + 1

    return {
        "concurrency": concurrency,
        "completed": len(ok),
        "failed": len(results) - len(ok),
        "errors": errors,
        "wall": wall,
        "throughput": len(ok) / wall if wall else 0,
        "cache_hits": sum(1 for r in ok if r.cached),
        "ttfb_p50": pct(ttfbs, 50),
        "ttfb_p95": pct(ttfbs, 95),
        "ttfb_max": max(ttfbs, default=float("nan")),
        "total_p50": pct(totals, 50),
        "total_p95": pct(totals, 95),
        "gen_p50": statistics.median([t - f for t, f in zip(totals, ttfbs, strict=False)])
        if ttfbs and totals
        else float("nan"),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8001/api/chat/stream")
    parser.add_argument(
        "--password",
        default="loadtest",
        help="Shared password the stub/server accepts (default matches server.py).",
    )
    parser.add_argument("--concurrency", type=int, nargs="+", default=[10, 20, 40, 80, 160])
    parser.add_argument("--label", default="")
    parser.add_argument(
        "--run-id",
        default="",
        help="Reuse a previous run's id to hit an already-warm answer cache.",
    )
    args = parser.parse_args()

    token = fetch_token(args.url, args.password)
    run_id = args.run_id or uuid.uuid4().hex[:8]

    print(f"\n  {args.label or 'load test'}  ->  {args.url}   (run id {run_id})\n")
    header = f"  {'conc':>5} {'ok':>5} {'fail':>5} {'cached':>7} {'TTFB p50':>10} {'TTFB p95':>10} {'gen p50':>9} {'req/s':>7}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    rows = []
    for level in args.concurrency:
        row = await run_level(args.url, token, level, run_id)
        rows.append(row)
        print(
            f"  {row['concurrency']:>5} {row['completed']:>5} {row['failed']:>5} "
            f"{row['cache_hits']:>7} "
            f"{row['ttfb_p50']:>9.2f}s {row['ttfb_p95']:>9.2f}s "
            f"{row['gen_p50']:>8.2f}s {row['throughput']:>7.1f}"
        )
        if row["errors"]:
            for message, count in row["errors"].items():
                print(f"          {count} x {message}")
        # Let the server settle between levels so one run does not skew the next.
        await asyncio.sleep(2)

    baseline = rows[0]["ttfb_p50"]
    print(f"\n  TTFB p50 growth relative to concurrency={rows[0]['concurrency']}:")
    for row in rows:
        ratio = row["ttfb_p50"] / baseline if baseline else float("nan")
        print(f"    {row['concurrency']:>5} -> {ratio:>5.1f}x")
    print("\n  Flat TTFB means requests are being served concurrently.")
    print("  TTFB growing in step with concurrency means they are queueing.\n")


if __name__ == "__main__":
    asyncio.run(main())
