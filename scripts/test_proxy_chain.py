#!/usr/bin/env python3
"""Exercise client-IP handling through the real Caddy, Nginx, and Uvicorn containers."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILES = (ROOT / "docker-compose.yml", ROOT / "docker-compose.acceptance.yml")
API_IMAGE = "policy-assistant-api:acceptance"
WEB_IMAGE = "policy-assistant-web:acceptance"
CURL_IMAGE = "curlimages/curl:8.12.1"
FORGED_PREFIX = "198.18.0."


def _docker() -> str:
    found = shutil.which("docker")
    if found:
        return found
    windows = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
    if windows.exists():
        return str(windows)
    raise RuntimeError("docker was not found on PATH and Docker Desktop's CLI was not found")


DOCKER = _docker()
PROJECT = f"proxy-chain-acceptance-{os.getpid()}"


def _run(*args: str, check: bool = True, capture: bool = False, env=None):
    child_env = (env or os.environ).copy()
    docker_dir = str(Path(DOCKER).parent)
    if docker_dir not in child_env.get("PATH", "").split(os.pathsep):
        child_env["PATH"] = docker_dir + os.pathsep + child_env.get("PATH", "")
    return subprocess.run(
        [DOCKER, *args],
        cwd=ROOT,
        check=check,
        text=True,
        capture_output=capture,
        env=child_env,
    )


def _compose(*args: str, check: bool = True, capture: bool = False, env=None):
    command = ["compose", "-p", PROJECT]
    for path in COMPOSE_FILES:
        command.extend(("-f", str(path)))
    return _run(*command, *args, check=check, capture=capture, env=env)


def _output(*args: str) -> str:
    return _run(*args, capture=True).stdout.strip()


def _compose_output(*args: str, env=None) -> str:
    return _compose(*args, capture=True, env=env).stdout.strip()


def _bcrypt_hash() -> str:
    code = "import bcrypt; print(bcrypt.hashpw(b'acceptance', bcrypt.gensalt(4)).decode())"
    return _output("run", "--rm", "--entrypoint", "python", API_IMAGE, "-c", code)


def _curl_status(container: str, *, forged_xff: str | None = None) -> int:
    args = [
        "exec",
        container,
        "curl",
        "--silent",
        "--output",
        "/dev/null",
        "--write-out",
        "%{http_code}",
        "--header",
        "Host: localhost",
    ]
    if forged_xff:
        args.extend(("--header", f"X-Forwarded-For: {forged_xff}"))
    args.extend(
        (
            "--header",
            "Content-Type: application/json",
            "--data",
            '{"password":"wrong"}',
            "http://caddy/api/auth/login",
        )
    )
    result = _output(*args)
    if not result.isdigit():
        raise RuntimeError(f"curl returned a non-status response: {result!r}")
    return int(result)


def _wait_for_caddy(container: str) -> None:
    for _ in range(60):
        result = _run(
            "exec",
            container,
            "curl",
            "--fail",
            "--silent",
            "--output",
            "/dev/null",
            "--header",
            "Host: localhost",
            "http://caddy/api/health",
            check=False,
            capture=True,
        )
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError("the acceptance stack did not become healthy within 60 seconds")


def _network_id() -> str:
    result = _output(
        "network",
        "ls",
        "--filter",
        f"label=com.docker.compose.project={PROJECT}",
        "--filter",
        "label=com.docker.compose.network=edge",
        "--quiet",
    )
    ids = result.splitlines()
    if len(ids) != 1:
        raise RuntimeError(f"expected one acceptance edge network, found {len(ids)}")
    return ids[0]


def _start_probe(name: str, network: str) -> None:
    _run(
        "run",
        "--detach",
        "--rm",
        "--network",
        network,
        "--name",
        name,
        "--entrypoint",
        "sleep",
        CURL_IMAGE,
        "300",
        capture=True,
    )


def _container_ip(name: str) -> str:
    return _output(
        "inspect",
        "--format",
        "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
        name,
    )


def main() -> int:
    env = os.environ.copy()
    # Use ranges separate from the deployment defaults so this isolated project
    # can run while a developer's normal stack is already up.
    env["EDGE_SUBNET"] = env.get("ACCEPTANCE_EDGE_SUBNET", "10.252.0.0/24")
    env["APP_SUBNET"] = env.get("ACCEPTANCE_APP_SUBNET", "10.253.0.0/24")
    env["SITE_ADDRESS"] = "http://localhost"
    env["ACCEPTANCE_PASSWORD_HASH"] = "build-placeholder"

    probes = [f"{PROJECT}-client-a", f"{PROJECT}-client-b"]
    stack_started = False
    try:
        # Always ask Compose to build so a local cached tag can never make this
        # acceptance test exercise stale source or proxy configuration.
        _compose("build", "api", "web", env=env)

        env["ACCEPTANCE_PASSWORD_HASH"] = _bcrypt_hash()
        _compose("up", "--detach", "--no-build", "--wait", "--wait-timeout", "120", env=env)
        stack_started = True

        network = _network_id()
        for probe in probes:
            _start_probe(probe, network)
        _wait_for_caddy(probes[0])

        client_a_ip = _container_ip(probes[0])
        client_b_ip = _container_ip(probes[1])
        forged = [f"{FORGED_PREFIX}{index}" for index in range(1, 12)]

        statuses = [_curl_status(probes[0], forged_xff=value) for value in forged]
        if statuses != [401] * 10 + [429]:
            raise AssertionError(f"client A statuses were {statuses}, expected 10x401 then 429")

        client_b_status = _curl_status(probes[1], forged_xff="198.18.1.1")
        if client_b_status != 401:
            raise AssertionError(f"client B returned {client_b_status}, expected independent 401")

        logs = _compose_output("logs", "--no-color", "api", env=env)
        for expected in (client_a_ip, client_b_ip):
            if expected not in logs:
                raise AssertionError(f"API logs did not contain resolved client IP {expected}")
        for rejected in (*forged, "198.18.1.1"):
            if rejected in logs:
                raise AssertionError(f"API logs contained forged client IP {rejected}")

        print(
            "PASS: real proxy chain ignored forged XFF, enforced 10x401 then 429, "
            f"kept {client_b_ip} independent, and logged {client_a_ip}/{client_b_ip}."
        )
        return 0
    except Exception:
        if stack_started:
            print(_compose_output("ps", env=env), file=sys.stderr)
            print(_compose_output("logs", "--no-color", env=env), file=sys.stderr)
        raise
    finally:
        for probe in probes:
            _run("rm", "--force", probe, check=False, capture=True)
        _compose("down", "--volumes", "--remove-orphans", check=False, capture=True, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
