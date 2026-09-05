#!/usr/bin/env python3
"""Exercise client-IP handling through the real Caddy, Nginx, and Uvicorn containers."""

from __future__ import annotations

import os
import re
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
SECURITY_HEADERS = {
    "X-Frame-Options": "SAMEORIGIN",
    "X-Content-Type-Options": "nosniff",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}
ASSET_SRC_RE = re.compile(r"""(?:src|href)=["'](/assets/[^"']+)["']""", re.IGNORECASE)


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


def _parse_headers(raw: str) -> dict[str, str]:
    """Parse curl -i / -D style headers into a case-insensitive name→value map."""
    headers: dict[str, str] = {}
    for line in raw.splitlines():
        if not line or line.startswith("HTTP/"):
            continue
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    return headers


def _curl_response(
    container: str,
    path: str,
    *,
    method: str = "GET",
) -> tuple[int, dict[str, str], str]:
    """Return status, response headers, and body for a path via Caddy."""
    header_file = "/tmp/proxy-chain-headers"
    body_file = "/tmp/proxy-chain-body"
    status = _output(
        "exec",
        container,
        "curl",
        "--silent",
        "--request",
        method,
        "--header",
        "Host: localhost",
        "--dump-header",
        header_file,
        "--output",
        body_file,
        "--write-out",
        "%{http_code}",
        f"http://caddy{path}",
    )
    if not status.isdigit():
        raise RuntimeError(f"curl returned a non-status response for {path}: {status!r}")
    header_text = _output("exec", container, "cat", header_file)
    body = _output("exec", container, "cat", body_file)
    return int(status), _parse_headers(header_text), body


def _assert_security_headers(headers: dict[str, str], *, path: str) -> None:
    for name, expected in SECURITY_HEADERS.items():
        actual = headers.get(name.lower())
        if actual != expected:
            raise AssertionError(
                f"{path} expected {name}: {expected!r}, got {actual!r} in {headers}"
            )


def _assert_cache_headers(container: str) -> None:
    """Prove index.html is never cached, hashed assets are immutable, missing assets 404."""
    status, headers, index_body = _curl_response(container, "/")
    if status != 200:
        raise AssertionError(f"/ returned {status}, expected 200")
    _assert_security_headers(headers, path="/")
    cache_control = headers.get("cache-control", "")
    if cache_control != "no-cache":
        raise AssertionError(f"/ Cache-Control was {cache_control!r}, expected 'no-cache'")

    match = ASSET_SRC_RE.search(index_body)
    if match is None:
        raise AssertionError(f"/ HTML did not reference an /assets/ URL: {index_body[:200]!r}")
    asset_path = match.group(1)

    asset_status, asset_headers, _ = _curl_response(container, asset_path)
    if asset_status != 200:
        raise AssertionError(f"{asset_path} returned {asset_status}, expected 200")
    _assert_security_headers(asset_headers, path=asset_path)
    asset_cache = asset_headers.get("cache-control", "")
    if "immutable" not in asset_cache or "max-age=31536000" not in asset_cache:
        raise AssertionError(
            f"{asset_path} Cache-Control was {asset_cache!r}, "
            "expected public max-age=31536000 immutable"
        )

    missing = "/assets/missing-does-not-exist-issue91.js"
    missing_status, missing_headers, missing_body = _curl_response(container, missing)
    if missing_status != 404:
        raise AssertionError(f"{missing} returned {missing_status}, expected 404")
    # Nginx's default 404 page is HTML; what matters is we did not SPA-fallback
    # to index.html (which would be a 200 with the Vite shell).
    if missing_body.strip() == index_body.strip():
        raise AssertionError(f"{missing} returned the SPA index.html body instead of 404")
    if 'id="root"' in missing_body:
        raise AssertionError(f"{missing} body looked like the SPA shell, not a 404")
    # Error responses still carry the location's add_header ... always lines.
    _assert_security_headers(missing_headers, path=missing)


def main() -> int:
    env = os.environ.copy()
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

        _assert_cache_headers(probes[0])

        print(
            "PASS: real proxy chain ignored forged XFF, enforced 10x401 then 429, "
            f"kept {client_b_ip} independent, logged {client_a_ip}/{client_b_ip}, "
            "and served Cache-Control no-cache on / with immutable hashed assets "
            "(missing /assets/ returns 404, not SPA HTML)."
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
