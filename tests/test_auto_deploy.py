"""Run the synthetic auto_deploy shell checks under pytest."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "test_auto_deploy.sh"


def test_auto_deploy_retries_from_deployed_ref() -> None:
    """Failed builds must retry from refs/deployed/main, not HEAD."""
    bash = shutil.which("bash")
    if sys.platform == "win32":
        git_bash = Path("C:/Program Files/Git/bin/bash.exe")
        bash = str(git_bash) if git_bash.is_file() else None
    if bash is None:
        pytest.skip("Git Bash is required on Windows; bash is required elsewhere")
    script = SCRIPT.as_posix() if sys.platform == "win32" else str(SCRIPT)
    subprocess.run([bash, script], check=True, cwd=ROOT)
