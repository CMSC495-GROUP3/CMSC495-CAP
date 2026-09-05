"""Narrow checks for the synthetic load-test stub.

Production rate limits stay on; only scripts/loadtest/server.py opts out so the
documented concurrency ladder is not capped by CHAT_RATE_LIMIT on 127.0.0.1.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _import_stub(**environment: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(environment)
    return subprocess.run(
        [sys.executable, "-c", "import scripts.loadtest.server"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_loadtest_stub_disables_limiter():
    """Fresh process: importing the stub must leave the limiter disabled."""
    code = (
        "from scripts.loadtest.server import limiter; "
        "assert limiter.enabled is False, limiter.enabled"
    )
    env = os.environ.copy()
    env.update(APP_ENV="development", LLM_PROVIDER="fake")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_loadtest_stub_refuses_production_environment():
    result = _import_stub(APP_ENV="production", LLM_PROVIDER="fake")
    assert result.returncode != 0
    assert "cannot run with APP_ENV=production" in result.stderr


def test_loadtest_stub_refuses_real_provider():
    result = _import_stub(APP_ENV="development", LLM_PROVIDER="openai")
    assert result.returncode != 0
    assert "requires LLM_PROVIDER=fake" in result.stderr
