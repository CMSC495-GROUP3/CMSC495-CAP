"""Run the synthetic auto_deploy shell checks under pytest."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "test_auto_deploy.sh"


def test_auto_deploy_retries_from_deployed_ref() -> None:
    """Failed builds must retry from refs/deployed/main, not HEAD."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required to run scripts/test_auto_deploy.sh")
    subprocess.run([bash, str(SCRIPT)], check=True, cwd=ROOT)
