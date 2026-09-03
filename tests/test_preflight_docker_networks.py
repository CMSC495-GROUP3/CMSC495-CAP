"""Regression tests for scripts/preflight-docker-networks.sh.

Exercises owned-network exclusion without talking to a real Docker daemon or
host routing table. PATH stubs for ``docker`` and ``ip`` drive each scenario
using MSYS-style paths so Git Bash process substitutions resolve them.

Ownership matches the labels Compose actually sets on networks
(``com.docker.compose.project`` and ``com.docker.compose.network``) plus an
exact subnet match. Directory labels and other ``-p`` projects from the same
checkout are not treated as production-owned.
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
PRODUCTION_PROJECT = "cmsc495-cap-team"


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
    project: str = PRODUCTION_PROJECT,
    script: Path | None = None,
    fail: str | None = None,
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
    payload: dict = {"project": project, "networks": networks, "routes": routes}
    if fail is not None:
        payload["fail"] = fail
    fixture_path.write_text(json.dumps(payload), encoding="utf-8")
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


def _assert_inventory_failure(
    result: subprocess.CompletedProcess[str],
    *,
    operation_snippet: str,
) -> None:
    """Required inventory failures must fail closed with a useful error."""
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "Proxy network preflight passed" not in combined
    assert f"ERROR: failed to {operation_snippet}" in result.stderr
    # Collision messages alone are not enough; inventory must be the failure.
    assert "already owned by this Compose project" not in result.stdout


def _compose_labels(project: str, logical: str) -> dict[str, str]:
    return {
        "com.docker.compose.project": project,
        "com.docker.compose.network": logical,
        "com.docker.compose.version": "2.29.2",
        "com.docker.compose.config-hash": "test-config-hash",
    }


def _network(
    *,
    short_id: str,
    logical: str,
    subnet: str,
    project: str = PRODUCTION_PROJECT,
) -> dict:
    return {
        "id": short_id,
        "full_id": f"{short_id}{'b' * 20}{'c' * 12}",
        "labels": _compose_labels(project, logical),
        "subnets": [subnet],
        "bridge": f"br-{short_id}",
    }


def _owned_edge_network() -> dict:
    return _network(short_id="aaaaaaaaaaaa", logical="edge", subnet=EDGE)


def _owned_app_network() -> dict:
    return _network(short_id="bbbbbbbbbbbb", logical="app", subnet=APP)


def _owned_routes() -> list[str]:
    return [
        f"{EDGE} dev br-aaaaaaaaaaaa proto kernel scope link src 10.250.0.1",
        f"{APP} dev br-bbbbbbbbbbbb proto kernel scope link src 10.251.0.1",
    ]


def test_first_deploy_no_owned_networks(tmp_path: Path) -> None:
    """Clean host: no owned networks and no colliding routes."""
    result = _run_preflight(tmp_path, networks=[], routes=["192.168.1.0/24 dev eth0"])
    assert result.returncode == 0, result.stderr
    assert "already owned" not in result.stdout
    assert "Proxy network preflight passed" in result.stdout


def test_repeat_deploy_owned_networks_only(tmp_path: Path) -> None:
    """Owned edge/app bridges are excluded; no unrelated collisions remain."""
    result = _run_preflight(
        tmp_path,
        networks=[_owned_edge_network(), _owned_app_network()],
        routes=_owned_routes(),
    )
    assert result.returncode == 0, result.stderr
    assert "already owned by this Compose project" in result.stdout
    assert "Proxy network preflight passed" in result.stdout


def test_same_checkout_different_compose_project_is_not_owned(tmp_path: Path) -> None:
    """A `-p` project from this checkout is still a collision for production."""
    alternate = [
        _network(
            short_id="aaaaaaaaaaaa",
            logical="edge",
            subnet=EDGE,
            project="proxy-chain-acceptance-999",
        ),
        _network(
            short_id="bbbbbbbbbbbb",
            logical="app",
            subnet=APP,
            project="proxy-chain-acceptance-999",
        ),
    ]
    result = _run_preflight(
        tmp_path,
        networks=alternate,
        routes=["192.168.1.0/24 dev eth0"],
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "already owned by this Compose project" not in result.stdout
    assert "overlaps Docker network" in result.stderr


def test_correct_project_wrong_logical_network_is_not_owned(tmp_path: Path) -> None:
    """Project match is not enough without the edge/app logical network label."""
    mislabeled = [
        _network(short_id="aaaaaaaaaaaa", logical="frontend", subnet=EDGE),
        _owned_app_network(),
    ]
    result = _run_preflight(
        tmp_path,
        networks=mislabeled,
        routes=["192.168.1.0/24 dev eth0"],
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "overlaps Docker network" in result.stderr


def test_repeat_deploy_unrelated_host_route_conflict(tmp_path: Path) -> None:
    """Owned network must not hide an unrelated overlapping host route."""
    result = _run_preflight(
        tmp_path,
        networks=[_owned_edge_network(), _owned_app_network()],
        routes=[
            *_owned_routes(),
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
        "labels": {
            "com.docker.compose.project": "other-app",
            "com.docker.compose.network": "edge",
            "com.docker.compose.version": "2.29.2",
            "com.docker.compose.config-hash": "foreign-hash",
        },
        "subnets": ["10.250.0.0/25"],
        "bridge": "br-ffffffffffff",
    }
    result = _run_preflight(
        tmp_path,
        networks=[_owned_edge_network(), _owned_app_network(), foreign],
        routes=_owned_routes(),
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
            PRODUCTION_COMPOSE_PROJECT="cmsc495-cap-team"
            project_owns_subnet() {
              local logical="$1" target="$2" network project network_label subnet
              while IFS= read -r network; do
                [[ -n "$network" ]] || continue
                project="$(
                  docker network inspect \\
                    --format '{{index .Labels "com.docker.compose.project"}}' \\
                    "$network" 2>/dev/null || true
                )"
                [[ "$project" == "$PRODUCTION_COMPOSE_PROJECT" ]] || continue
                network_label="$(
                  docker network inspect \\
                    --format '{{index .Labels "com.docker.compose.network"}}' \\
                    "$network" 2>/dev/null || true
                )"
                [[ "$network_label" == "$logical" ]] || continue
                while IFS= read -r subnet; do
                  [[ "$subnet" == "$target" ]] && return 0
                done < <(docker network inspect --format '{{range .IPAM.Config}}{{println .Subnet}}{{end}}' "$network")
              done < <(docker network ls --quiet)
              return 1
            }
            check_subnet() {
              local label="$1" target="$2"
              if project_owns_subnet "$label" "$target"; then
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

    routes = [*_owned_routes(), f"{EDGE} via 192.168.1.1 dev eth0"]
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


def test_inventory_failure_compose_config(tmp_path: Path) -> None:
    result = _run_preflight(tmp_path, networks=[], routes=[], fail="compose_config")
    _assert_inventory_failure(result, operation_snippet="run docker compose config")


def test_inventory_failure_ip_route(tmp_path: Path) -> None:
    result = _run_preflight(tmp_path, networks=[], routes=[], fail="ip_route")
    _assert_inventory_failure(result, operation_snippet="inventory host IPv4 routes")


def test_inventory_failure_network_ls(tmp_path: Path) -> None:
    result = _run_preflight(tmp_path, networks=[], routes=[], fail="network_ls")
    _assert_inventory_failure(result, operation_snippet="inventory Docker networks")


def test_inventory_failure_inspect_project_label(tmp_path: Path) -> None:
    result = _run_preflight(
        tmp_path,
        networks=[_owned_edge_network(), _owned_app_network()],
        routes=_owned_routes(),
        fail="inspect_project_label",
    )
    _assert_inventory_failure(
        result,
        operation_snippet="inspect Compose project label on Docker network",
    )


def test_inventory_failure_inspect_network_label(tmp_path: Path) -> None:
    result = _run_preflight(
        tmp_path,
        networks=[_owned_edge_network(), _owned_app_network()],
        routes=_owned_routes(),
        fail="inspect_network_label",
    )
    _assert_inventory_failure(
        result,
        operation_snippet="inspect Compose network label on Docker network",
    )


def test_inventory_failure_inspect_subnet(tmp_path: Path) -> None:
    result = _run_preflight(
        tmp_path,
        networks=[_owned_edge_network(), _owned_app_network()],
        routes=_owned_routes(),
        fail="inspect_subnet",
    )
    _assert_inventory_failure(result, operation_snippet="inspect Docker network subnet")


def test_inventory_failure_inspect_network_id(tmp_path: Path) -> None:
    result = _run_preflight(
        tmp_path,
        networks=[_owned_edge_network(), _owned_app_network()],
        routes=_owned_routes(),
        fail="inspect_network_id",
    )
    _assert_inventory_failure(result, operation_snippet="inspect Docker network ID")


def test_inventory_failure_inspect_bridge(tmp_path: Path) -> None:
    result = _run_preflight(
        tmp_path,
        networks=[_owned_edge_network(), _owned_app_network()],
        routes=_owned_routes(),
        fail="inspect_bridge",
    )
    _assert_inventory_failure(
        result,
        operation_snippet="inspect Docker network bridge option",
    )
