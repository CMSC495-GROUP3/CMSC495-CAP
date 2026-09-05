"""Narrow checks for the synthetic load-test stub.

Production rate limits stay on; only scripts/loadtest/server.py opts out so the
documented concurrency ladder is not capped by CHAT_RATE_LIMIT on 127.0.0.1.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_loadtest_stub_disables_limiter():
    """Fresh process: importing the stub must leave the limiter disabled."""
    code = (
        "from scripts.loadtest.server import limiter; "
        "assert limiter.enabled is False, limiter.enabled"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
