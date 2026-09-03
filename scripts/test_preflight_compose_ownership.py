#!/usr/bin/env python3
"""Prove preflight ownership against real Compose network labels.

Creates production-named networks with ``docker compose`` (no fabricated
``working_dir`` labels) and a differently named ``-p`` project from the same
compose file. Diagnostic containers, networks, and volumes are always removed.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "scripts" / "preflight-docker-networks.sh"
COMPOSE_FILE = ROOT / "docker-compose.yml"
PID = os.getpid()
PROD_PROJECT = f"preflight-owned-{PID}"
ALT_PROJECT = f"preflight-alt-{PID}"
EDGE_SUBNET = os.environ.get("PREFLIGHT_VERIFY_EDGE_SUBNET", "10.249.80.0/24")
APP_SUBNET = os.environ.get("PREFLIGHT_VERIFY_APP_SUBNET", "10.249.81.0/24")
IP_HELPER = f"{PROD_PROJECT}-iproute"


def _docker() -> str:
    found = shutil.which("docker")
    if found:
        return found
    windows = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
    if windows.exists():
        return str(windows)
    raise RuntimeError("docker was not found on PATH and Docker Desktop's CLI was not found")


def _bash() -> str:
    candidates = [
        os.environ.get("BASH_PATH", ""),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        shutil.which("bash") or "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("bash is required to run preflight-docker-networks.sh")


def _msys_path(path: Path) -> str:
    resolved = path.resolve().as_posix()
    if len(resolved) >= 2 and resolved[1] == ":":
        return f"/{resolved[0].lower()}{resolved[2:]}"
    return resolved


DOCKER = _docker()
BASH = _bash()


def _child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = (base or os.environ).copy()
    docker_dir = str(Path(DOCKER).parent)
    parts = env.get("PATH", "").split(os.pathsep)
    if docker_dir not in parts:
        env["PATH"] = docker_dir + os.pathsep + env.get("PATH", "")
    return env


def _run(*args: str, check: bool = True, capture: bool = False, env=None):
    return subprocess.run(
        [DOCKER, *args],
        cwd=ROOT,
        check=check,
        text=True,
        capture_output=capture,
        env=_child_env(env),
    )


def _output(*args: str, env=None) -> str:
    return _run(*args, capture=True, env=env).stdout.strip()


def _compose(project: str, *args: str, check: bool = True, capture: bool = False, env=None):
    child = _child_env(env)
    child["COMPOSE_PROJECT_NAME"] = project
    child["EDGE_SUBNET"] = EDGE_SUBNET
    child["APP_SUBNET"] = APP_SUBNET
    command = ["compose", "-p", project, "-f", str(COMPOSE_FILE)]
    return _run(*command, *args, check=check, capture=capture, env=child)


def _default_compose_project() -> str:
    raw = _output("compose", "-f", str(COMPOSE_FILE), "config")
    for line in raw.splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    raise RuntimeError("docker compose config did not emit a top-level name")


def _network_ids(project: str) -> list[str]:
    raw = _output(
        "network",
        "ls",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--quiet",
    )
    return [line for line in raw.splitlines() if line]


def _inspect_labels(network_id: str) -> dict[str, str]:
    raw = _output("network", "inspect", "--format", "{{json .Labels}}", network_id)
    data = json.loads(raw or "{}")
    return {str(key): str(value) for key, value in data.items()}


def _inspect_subnet(network_id: str) -> str:
    return _output(
        "network",
        "inspect",
        "--format",
        "{{range .IPAM.Config}}{{.Subnet}}{{end}}",
        network_id,
    )


def _create_ip_shim(bin_dir: Path) -> None:
    """Give Git Bash an ``ip`` that reads the Docker VM/Linux host routes."""
    native = shutil.which("ip")
    script = bin_dir / "ip"
    if native:
        script.write_text(
            f'#!/usr/bin/env bash\nexec "{native}" "$@"\n',
            encoding="utf-8",
            newline="\n",
        )
    else:
        docker_msys = Path(DOCKER).as_posix()
        if len(docker_msys) >= 2 and docker_msys[1] == ":":
            docker_msys = f"/{docker_msys[0].lower()}{docker_msys[2:]}"
        script.write_text(
            (f'#!/usr/bin/env bash\nexec "{docker_msys}" exec "{IP_HELPER}" ip "$@"\n'),
            encoding="utf-8",
            newline="\n",
        )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _ensure_ip_helper() -> None:
    if shutil.which("ip"):
        return
    _run(
        "run",
        "--detach",
        "--network",
        "host",
        "--name",
        IP_HELPER,
        "--entrypoint",
        "sleep",
        "alpine:3.20",
        "300",
        capture=True,
    )
    install = _run(
        "exec",
        IP_HELPER,
        "sh",
        "-c",
        "apk add --no-cache iproute2 >/dev/null",
        check=False,
        capture=True,
    )
    if install.returncode != 0:
        raise RuntimeError(f"failed to install iproute2 in helper: {install.stderr}")


def _run_preflight(project: str, bin_dir: Path) -> subprocess.CompletedProcess[str]:
    env = _child_env()
    env["EDGE_SUBNET"] = EDGE_SUBNET
    env["APP_SUBNET"] = APP_SUBNET
    env["COMPOSE_PROJECT_NAME"] = project
    extra = str(bin_dir)
    env["PATH"] = extra + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        [BASH, _msys_path(PREFLIGHT)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _bring_up_networks(project: str) -> None:
    _compose(
        project,
        "up",
        "--no-start",
        "--no-deps",
        "--pull",
        "missing",
        "--no-build",
        "caddy",
    )


def _assert_owned_labels(project: str) -> None:
    ids = _network_ids(project)
    by_logical: dict[str, str] = {}
    print(f"Compose project {project} networks:")
    for network_id in ids:
        labels = _inspect_labels(network_id)
        logical = labels.get("com.docker.compose.network", "")
        subnet = _inspect_subnet(network_id)
        print(
            f"  id={network_id} logical={logical} subnet={subnet} "
            f"project={labels.get('com.docker.compose.project', '')} "
            f"version={labels.get('com.docker.compose.version', '')} "
            f"has_working_dir={'com.docker.compose.project.working_dir' in labels}"
        )
        if "com.docker.compose.project.working_dir" in labels:
            raise AssertionError("Compose network unexpectedly has working_dir label")
        if labels.get("com.docker.compose.project") != project:
            raise AssertionError(f"project label mismatch on {network_id}")
        if logical:
            by_logical[logical] = subnet
    if by_logical.get("edge") != EDGE_SUBNET or by_logical.get("app") != APP_SUBNET:
        raise AssertionError(
            f"expected edge/app networks with configured subnets, got {by_logical}"
        )


def _cleanup() -> None:
    for project in (PROD_PROJECT, ALT_PROJECT):
        _compose(project, "down", "--volumes", "--remove-orphans", check=False, capture=True)
        leftover = _network_ids(project)
        if leftover:
            _run("network", "rm", *leftover, check=False, capture=True)
    _run("rm", "--force", IP_HELPER, check=False, capture=True)


def main() -> int:
    default_name = _default_compose_project()
    print(f"Default docker compose down project name: {default_name}")
    print(f"Verification production project: {PROD_PROJECT}")
    print(f"Verification alternate -p project: {ALT_PROJECT}")
    print(
        "Ownership identity: com.docker.compose.project + com.docker.compose.network + exact subnet"
    )

    with tempfile.TemporaryDirectory(prefix="preflight-ip-") as tmp:
        bin_dir = Path(tmp)
        try:
            _ensure_ip_helper()
            _create_ip_shim(bin_dir)

            _bring_up_networks(PROD_PROJECT)
            _assert_owned_labels(PROD_PROJECT)
            owned = _run_preflight(PROD_PROJECT, bin_dir)
            print(owned.stdout)
            if owned.returncode != 0:
                print(owned.stderr, file=sys.stderr)
                raise AssertionError("preflight failed for legitimate repeat-deployment state")
            if "already owned by this Compose project" not in owned.stdout:
                raise AssertionError(
                    "preflight did not recognize Compose-created production networks"
                )

            _compose(PROD_PROJECT, "down", "--volumes", "--remove-orphans", capture=True)

            _bring_up_networks(ALT_PROJECT)
            _assert_owned_labels(ALT_PROJECT)
            collision = _run_preflight(PROD_PROJECT, bin_dir)
            print(collision.stdout)
            print(collision.stderr)
            if collision.returncode == 0:
                raise AssertionError("preflight succeeded for an alternate -p project collision")
            if "already owned by this Compose project" in collision.stdout:
                raise AssertionError("alternate -p project was incorrectly treated as owned")
            combined = collision.stdout + collision.stderr
            if "overlaps Docker network" not in combined and "overlaps host route" not in combined:
                raise AssertionError(
                    "alternate -p project did not fail as a host-route or Docker-network collision"
                )

            print(
                "PASS: Compose production labels were owned, "
                f"{ALT_PROJECT} was not owned, repeat-deploy preflight passed, "
                "and alternate-project preflight failed."
            )
            return 0
        finally:
            _cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
