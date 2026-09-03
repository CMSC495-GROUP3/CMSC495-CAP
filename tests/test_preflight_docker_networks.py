"""Regression tests for scripts/preflight-docker-networks.sh.

Exercises owned-network exclusion without talking to a real Docker daemon or
host routing table. PATH stubs for ``docker`` and ``ip`` drive each scenario
using MSYS-style paths so Git Bash process substitutions resolve them.

The HIGH finding this covers: when the Compose project already owned the
target subnet, the preflight used to return early and skip host-route and
Docker-network collision checks. ``deploy.sh`` then runs ``docker compose
down``, removing that owned network before recreate — so unchecked collisions
could still break the deploy.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = REPO_ROOT / "scripts" / "preflight-docker-networks.sh"
STUB_DIR = Path(__file__).resolve().parent / "preflight_stubs"
EDGE = "10.250.0.0/24"
APP = "10.251.0.0/24"


def _bash() -> str:
    """Prefer Git Bash on Windows so OneDrive paths resolve as native paths."""
    candidates = [
        os.environ.get("BASH_PATH", ""),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        shutil.which("bash") or "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    pytest.skip("bash is required to run preflight network tests")


def _python() -> str:
    venv = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv.is_file():
        return str(venv)
    venv_unix = REPO_ROOT / ".venv" / "bin" / "python"
    if venv_unix.is_file():
        return str(venv_unix)
    found = shutil.which("python") or shutil.which("python3")
    if found:
        return found
    pytest.skip("python is required to run preflight network stubs")


def _msys_path(path: Path) -> str:
    """Convert a Windows path to the /c/... form Git Bash expects on PATH."""
    resolved = path.resolve().as_posix()
    if len(resolved) >= 2 and resolved[1] == ":":
        return f"/{resolved[0].lower()}{resolved[2:]}"
    return resolved


def _pwd_p(bash: str, cwd: Path) -> str:
    result = subprocess.run(
        [bash, "-c", "pwd -P"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _write_executable(path: Path, content: str) -> None:
    _write_text(path, content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _install_path_stubs(bin_dir: Path, fixture_path: Path, python: str) -> None:
    """Install docker/ip executables that Git Bash can resolve via PATH."""
    python_msys = _msys_path(Path(python))
    docker_stub = _msys_path(STUB_DIR / "docker_stub.py")
    ip_stub = _msys_path(STUB_DIR / "ip_stub.py")
    fixture = _msys_path(fixture_path)
    _write_executable(
        bin_dir / "docker",
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            exec "{python_msys}" "{docker_stub}" "{fixture}" "$@"
            """
        ),
    )
    _write_executable(
        bin_dir / "ip",
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            if [[ "${{1:-}}" == "-4" && "${{2:-}}" == "route" && "${{3:-}}" == "show" ]]; then
              exec "{python_msys}" "{ip_stub}" "{fixture}"
            fi
            echo "unsupported ip invocation: $*" >&2
            exit 1
            """
        ),
    )


def _run_preflight(
    tmp_path: Path,
    *,
    networks: list[dict],
    routes: list[str],
    edge: str = EDGE,
    app: str = APP,
    script: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    bash = _bash()
    python = _python()
    root = tmp_path
    root.mkdir(parents=True, exist_ok=True)
    work = root / "checkout"
    work.mkdir(parents=True, exist_ok=True)
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    fixture_path = root / "fixture.json"
    target = script or PREFLIGHT

    owner = _pwd_p(bash, work)
    normalized = []
    for network in networks:
        item = dict(network)
        if item.get("working_dir") == "$OWNER":
            item["working_dir"] = owner
        normalized.append(item)
    fixture_path.write_text(
        json.dumps({"networks": normalized, "routes": routes}),
        encoding="utf-8",
    )
    _install_path_stubs(bin_dir, fixture_path, python)

    env = os.environ.copy()
    env["EDGE_SUBNET"] = edge
    env["APP_SUBNET"] = app
    # MSYS paths only: keep real docker off PATH and make stubs resolvable.
    env["PATH"] = f"{_msys_path(bin_dir)}:/usr/bin:/bin"
    return subprocess.run(
        [bash, _msys_path(target)],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _owned_edge_network() -> dict:
    return {
        "id": "aaaaaaaaaaaa",
        "full_id": "aaaaaaaaaaaabbbbbbbbbbbbcccccccccccccccc",
        "working_dir": "$OWNER",
        "subnets": [EDGE],
        "bridge": "br-aaaaaaaaaaaa",
    }


def _owned_app_network() -> dict:
    return {
        "id": "bbbbbbbbbbbb",
        "full_id": "bbbbbbbbbbbbccccccccccccdddddddddddddddd",
        "working_dir": "$OWNER",
        "subnets": [APP],
        "bridge": "br-bbbbbbbbbbbb",
    }


def test_first_deploy_no_owned_networks(tmp_path: Path) -> None:
    """Clean host: no owned networks and no colliding routes."""
    result = _run_preflight(tmp_path, networks=[], routes=["192.168.1.0/24 dev eth0"])
    assert result.returncode == 0, result.stderr
    assert "Proxy network preflight passed" in result.stdout


def test_repeat_deploy_owned_networks_only(tmp_path: Path) -> None:
    """Owned edge/app bridges are excluded; no unrelated collisions remain."""
    result = _run_preflight(
        tmp_path,
        networks=[_owned_edge_network(), _owned_app_network()],
        routes=[
            f"{EDGE} dev br-aaaaaaaaaaaa proto kernel scope link src 10.250.0.1",
            f"{APP} dev br-bbbbbbbbbbbb proto kernel scope link src 10.251.0.1",
        ],
    )
    assert result.returncode == 0, result.stderr
    assert "already owned by this Compose project" in result.stdout
    assert "Proxy network preflight passed" in result.stdout


def test_repeat_deploy_unrelated_host_route_conflict(tmp_path: Path) -> None:
    """Owned network must not hide an unrelated overlapping host route."""
    result = _run_preflight(
        tmp_path,
        networks=[_owned_edge_network(), _owned_app_network()],
        routes=[
            f"{EDGE} dev br-aaaaaaaaaaaa proto kernel scope link src 10.250.0.1",
            f"{APP} dev br-bbbbbbbbbbbb proto kernel scope link src 10.251.0.1",
            # Same CIDR on a different device — must still fail.
            f"{EDGE} via 192.168.1.1 dev eth0",
        ],
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "overlaps host route" in result.stderr


def test_repeat_deploy_unrelated_docker_network_conflict(tmp_path: Path) -> None:
    """Owned network must not hide an unrelated overlapping Docker network."""
    foreign = {
        "id": "ffffffffffff",
        "full_id": "ffffffffffffffffffffffffffffffffffffffff",
        "working_dir": "/other/project",
        "subnets": ["10.250.0.0/25"],
        "bridge": "br-ffffffffffff",
    }
    result = _run_preflight(
        tmp_path,
        networks=[_owned_edge_network(), _owned_app_network(), foreign],
        routes=[
            f"{EDGE} dev br-aaaaaaaaaaaa proto kernel scope link src 10.250.0.1",
            f"{APP} dev br-bbbbbbbbbbbb proto kernel scope link src 10.251.0.1",
        ],
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "overlaps Docker network" in result.stderr


def test_edge_app_subnet_overlap(tmp_path: Path) -> None:
    result = _run_preflight(
        tmp_path,
        networks=[],
        routes=[],
        edge="10.250.0.0/24",
        app="10.250.0.0/25",
    )
    assert result.returncode != 0
    assert "EDGE_SUBNET" in result.stderr and "overlaps APP_SUBNET" in result.stderr


def test_malformed_cidr_fails(tmp_path: Path) -> None:
    result = _run_preflight(
        tmp_path,
        networks=[],
        routes=[],
        edge="10.250.0.0/99",
        app=APP,
    )
    assert result.returncode != 0
    assert "invalid IPv4 CIDR" in result.stderr


def test_legacy_early_return_would_miss_host_route_conflict(tmp_path: Path) -> None:
    """Demonstrate the previous skip bug against the same fixture as the fix.

    Old logic: if the project owned the subnet, return success immediately.
    That incorrectly accepts an unrelated overlapping host route.
    """
    legacy = tmp_path / "legacy-preflight.sh"
    _write_text(
        legacy,
        textwrap.dedent(
            """\
            #!/bin/bash
            set -euo pipefail
            project_owns_subnet() {
              local target="$1" network owner subnet
              while IFS= read -r network; do
                [[ -n "$network" ]] || continue
                owner="$(
                  docker network inspect \\
                    --format '{{index .Labels "com.docker.compose.project.working_dir"}}' \\
                    "$network" 2>/dev/null || true
                )"
                [[ "$owner" == "$(pwd -P)" ]] || continue
                while IFS= read -r subnet; do
                  [[ "$subnet" == "$target" ]] && return 0
                done < <(docker network inspect --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}' "$network")
              done < <(docker network ls --quiet)
              return 1
            }
            check_subnet() {
              local label="$1" target="$2"
              if project_owns_subnet "$target"; then
                printf 'Subnet %s is already owned by this Compose project.\\n' "$target"
                return
              fi
              echo "would check collisions for $label $target"
              return 1
            }
            EDGE_SUBNET="${EDGE_SUBNET:-10.250.0.0/24}"
            APP_SUBNET="${APP_SUBNET:-10.251.0.0/24}"
            check_subnet edge "$EDGE_SUBNET"
            check_subnet app "$APP_SUBNET"
            printf 'Proxy network preflight passed: edge=%s app=%s\\n' "$EDGE_SUBNET" "$APP_SUBNET"
            """
        ),
    )

    routes = [
        f"{EDGE} dev br-aaaaaaaaaaaa proto kernel scope link src 10.250.0.1",
        f"{APP} dev br-bbbbbbbbbbbb proto kernel scope link src 10.251.0.1",
        f"{EDGE} via 192.168.1.1 dev eth0",
    ]
    networks = [_owned_edge_network(), _owned_app_network()]

    legacy_result = _run_preflight(
        tmp_path / "legacy_run",
        networks=networks,
        routes=routes,
        script=legacy,
    )
    fixed_result = _run_preflight(
        tmp_path / "fixed_run",
        networks=networks,
        routes=routes,
    )

    assert legacy_result.returncode == 0, legacy_result.stderr
    assert "Proxy network preflight passed" in legacy_result.stdout
    assert fixed_result.returncode != 0, fixed_result.stdout + fixed_result.stderr
    assert "overlaps host route" in fixed_result.stderr
