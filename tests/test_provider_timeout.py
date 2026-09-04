"""Provider timeout configuration and login isolation from the chat pool."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import anyio.to_thread
import httpx
import openai
from conftest import TEST_PASSWORD

from policy_assistant.api.routes import auth as auth_routes
from policy_assistant.rag import llm
from policy_assistant.rag.config import (
    LOGIN_THREADPOOL_TOKENS,
    OPENAI_MAX_RETRIES,
    OPENAI_TIMEOUT_SECONDS,
)


def test_openai_client_uses_configured_timeout_and_retries(monkeypatch):
    """OpenAIProvider must pass env-backed timeout/retries into the SDK client.

    No network: the OpenAI constructor is replaced with a recorder.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")
    monkeypatch.setattr(llm, "OPENAI_TIMEOUT_SECONDS", 12.5)
    monkeypatch.setattr(llm, "OPENAI_MAX_RETRIES", 0)

    captured: dict = {}

    class _Recorder:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(openai, "OpenAI", _Recorder)

    provider = llm.OpenAIProvider()
    assert provider._client is not None
    assert captured["api_key"] == "sk-test-not-used"
    assert captured["max_retries"] == 0
    timeout = captured["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read == 12.5
    assert timeout.write == 12.5
    assert timeout.connect == 5.0


def test_openai_defaults_match_config():
    assert OPENAI_TIMEOUT_SECONDS == 30.0
    assert OPENAI_MAX_RETRIES == 1
    assert LOGIN_THREADPOOL_TOKENS == 10
    assert auth_routes._login_limiter.total_tokens == LOGIN_THREADPOOL_TOKENS


def test_login_stays_available_when_chat_threads_are_saturated(
    client, auth, retrieval, conversation
):
    """Two slow streams fill a 2-token chat pool; login must still return quickly.

    Mirrors the issue check: high stream delay with LOADTEST_THREAD_TOKENS=2.
    """
    provider = llm.get_provider()
    hold = threading.Event()
    started = threading.Barrier(3)

    def slow_stream(*_args, **_kwargs):
        started.wait(timeout=5)
        assert hold.wait(timeout=10), "login side never released the streams"
        yield " done"

    original_stream = provider.stream
    provider.stream = slow_stream

    def _resize_pool(tokens: int) -> None:
        anyio.to_thread.current_default_thread_limiter().total_tokens = tokens

    original_tokens = client.portal.call(
        lambda: anyio.to_thread.current_default_thread_limiter().total_tokens
    )
    client.portal.call(_resize_pool, 2)

    def run_stream() -> None:
        with client.stream(
            "POST",
            "/api/chat/stream",
            json={"question": "How much PTO?", "session_id": conversation},
            headers=auth,
            timeout=30.0,
        ) as response:
            for _ in response.iter_bytes():
                pass

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run_stream), pool.submit(run_stream)]
            started.wait(timeout=5)

            t0 = time.perf_counter()
            login = client.post("/api/auth/login", json={"password": TEST_PASSWORD})
            elapsed = time.perf_counter() - t0

            assert login.status_code == 200, login.text
            assert elapsed < 2.0, f"login blocked by saturated chat pool ({elapsed:.2f}s)"

            hold.set()
            for future in futures:
                future.result(timeout=15)
    finally:
        provider.stream = original_stream
        client.portal.call(_resize_pool, original_tokens)
