"""X-Forwarded-For trust: Uvicorn behaviour and proxy config invariants.

Production path (see docker-compose.yml):

* Caddy is the public edge. With no trusted_proxies, it replaces any
  client-supplied X-Forwarded-* with the connecting client's address.
* Nginx trusts X-Forwarded-For only from Docker's default address pools
  (set_real_ip_from 172.16.0.0/12 and 192.168.0.0/16), resolves $remote_addr
  to that client, then *replaces* X-Forwarded-For with $remote_addr toward
  Uvicorn.
* Uvicorn trusts X-Forwarded-* only from the same Docker address pools
  (FORWARDED_ALLOW_IPS=172.16.0.0/12,192.168.0.0/16).

Behavioural proof of the full live chain is scripts/test_proxy_chain.py.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx
import pytest
import yaml
from conftest import TEST_PASSWORD
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from policy_assistant.api import main
from policy_assistant.api.limiter import limiter

# Must match docker-compose.yml FORWARDED_ALLOW_IPS and nginx.conf.
DOCKER_POOL_CIDR = "172.16.0.0/12,192.168.0.0/16"
NGINX_PEER = ("172.18.0.10", 50000)
UNTRUSTED_PEER = ("203.0.113.9", 44321)
REPO_ROOT = Path(__file__).resolve().parents[1]


def _uvicorn_client(*, peer: tuple[str, int], trusted_hosts: str = DOCKER_POOL_CIDR) -> TestClient:
    """TestClient as if uvicorn wrapped the app with the given trust list."""
    return TestClient(
        ProxyHeadersMiddleware(main.app, trusted_hosts=trusted_hosts),
        client=peer,
    )


def _failed_login(client: TestClient, *, forwarded_for: str | None = None) -> httpx.Response:
    headers = {"X-Forwarded-For": forwarded_for} if forwarded_for is not None else None
    return client.post("/api/auth/login", json={"password": "wrong"}, headers=headers)


def test_untrusted_source_cannot_make_uvicorn_accept_forwarded_identity(caplog):
    """A peer outside the Docker address pool cannot forge request.client via XFF."""
    client = _uvicorn_client(peer=UNTRUSTED_PEER, trusted_hosts=DOCKER_POOL_CIDR)
    limiter.enabled = True
    limiter.reset()
    with caplog.at_level(logging.WARNING, logger="policy_assistant.api.routes.auth"):
        response = _failed_login(client, forwarded_for="1.2.3.4")
    assert response.status_code == 401
    assert "Failed login attempt from 203.0.113.9" in caplog.text
    assert "1.2.3.4" not in caplog.text


@pytest.mark.parametrize(
    "peer",
    [NGINX_PEER, ("192.168.16.4", 50000)],
)
def test_trusted_nginx_replace_header_is_honoured(peer, caplog):
    """After Nginx replace, Uvicorn on both Docker pools uses the resolved client."""
    client = _uvicorn_client(peer=peer)
    limiter.enabled = True
    limiter.reset()
    with caplog.at_level(logging.WARNING, logger="policy_assistant.api.routes.auth"):
        response = _failed_login(client, forwarded_for="203.0.113.50")
    assert response.status_code == 401
    assert "Failed login attempt from 203.0.113.50" in caplog.text


def test_direct_local_uvicorn_trust_ignores_non_loopback_forgery(caplog):
    """Bare local uvicorn defaults to trusting only 127.0.0.1."""
    client = _uvicorn_client(peer=UNTRUSTED_PEER, trusted_hosts="127.0.0.1")
    limiter.enabled = True
    limiter.reset()
    with caplog.at_level(logging.WARNING, logger="policy_assistant.api.routes.auth"):
        response = _failed_login(client, forwarded_for="1.2.3.4")
    assert response.status_code == 401
    assert "Failed login attempt from 203.0.113.9" in caplog.text
    assert "1.2.3.4" not in caplog.text


def test_login_still_works_without_forwarded_headers():
    """Direct clients (no XFF) remain usable under the Compose trust list."""
    client = _uvicorn_client(peer=UNTRUSTED_PEER)
    limiter.enabled = True
    limiter.reset()
    response = client.post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_normal_proxied_login_still_works():
    """Happy-path login through a trusted Nginx peer still issues a token."""
    client = _uvicorn_client(peer=NGINX_PEER)
    limiter.enabled = True
    limiter.reset()
    response = client.post(
        "/api/auth/login",
        json={"password": TEST_PASSWORD},
        headers={"X-Forwarded-For": "203.0.113.9"},
    )
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_legacy_append_style_xff_documents_why_nginx_must_replace(caplog):
    """If Nginx appended, a forged leftmost hop would win under uvicorn rules."""
    client = _uvicorn_client(peer=NGINX_PEER)
    limiter.enabled = True
    limiter.reset()
    with caplog.at_level(logging.WARNING, logger="policy_assistant.api.routes.auth"):
        response = _failed_login(client, forwarded_for=f"1.2.3.4, {NGINX_PEER[0]}")
    assert response.status_code == 401
    assert "Failed login attempt from 1.2.3.4" in caplog.text


def test_nginx_uses_real_ip_then_replaces_xff_with_remote_addr():
    conf = (REPO_ROOT / "web" / "nginx.conf").read_text(encoding="utf-8")
    assert "set_real_ip_from 172.16.0.0/12;" in conf
    assert "set_real_ip_from 192.168.0.0/16;" in conf
    assert "real_ip_header X-Forwarded-For;" in conf
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in conf
    assert not re.search(
        r"proxy_set_header\s+X-Forwarded-For\s+\$proxy_add_x_forwarded_for",
        conf,
    )


SECURITY_HEADER_LINES = (
    'add_header X-Frame-Options "SAMEORIGIN" always;',
    'add_header X-Content-Type-Options "nosniff" always;',
    'add_header X-XSS-Protection "1; mode=block" always;',
    'add_header Referrer-Policy "strict-origin-when-cross-origin" always;',
)


def _nginx_location_block(conf: str, location: str) -> str:
    """Return the body of `location <location> { ... }` from nginx.conf."""
    pattern = rf"location\s+{re.escape(location)}\s*\{{"
    match = re.search(pattern, conf)
    assert match is not None, f"missing location {location}"
    start = match.end()
    depth = 1
    index = start
    while index < len(conf) and depth:
        if conf[index] == "{":
            depth += 1
        elif conf[index] == "}":
            depth -= 1
        index += 1
    assert depth == 0, f"unbalanced braces for location {location}"
    return conf[start : index - 1]


def test_nginx_cache_control_and_asset_404_keep_security_headers():
    """index.html is never cached; hashed assets are immutable; missing assets 404."""
    conf = (REPO_ROOT / "web" / "nginx.conf").read_text(encoding="utf-8")

    # Server-level security headers remain for locations that do not redeclare
    # add_header (SPA fallback and /api/).
    for line in SECURITY_HEADER_LINES:
        assert line in conf

    index_block = _nginx_location_block(conf, "= /index.html")
    assert 'add_header Cache-Control "no-cache" always;' in index_block
    for line in SECURITY_HEADER_LINES:
        assert line in index_block

    assets_block = _nginx_location_block(conf, "/assets/")
    assert 'add_header Cache-Control "public, max-age=31536000, immutable" always;' in assets_block
    assert "try_files $uri =404;" in assets_block
    for line in SECURITY_HEADER_LINES:
        assert line in assets_block

    spa_block = _nginx_location_block(conf, "/")
    assert "try_files $uri $uri/ /index.html;" in spa_block
    assert "gzip on;" in conf


def test_caddyfile_does_not_trust_upstream_proxies():
    caddy = (REPO_ROOT / "Caddyfile").read_text(encoding="utf-8")
    assert "reverse_proxy web:80" in caddy
    for line in caddy.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped:
            assert "trusted_proxies" not in stripped


def test_dockerfile_does_not_trust_every_forwarded_ip():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "--forwarded-allow-ips=*" not in dockerfile
    assert "--proxy-headers" in dockerfile


def test_compose_trusts_docker_pool_and_keeps_api_off_edge():
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert services["api"]["environment"]["FORWARDED_ALLOW_IPS"] == DOCKER_POOL_CIDR
    assert set(compose["networks"]) == {"edge", "app"}
    assert services["caddy"]["networks"] == ["edge"]
    assert services["web"]["networks"] == ["edge", "app"]
    assert services["api"]["networks"] == ["app"]
    assert "ports" in services["caddy"]
    assert "ports" not in services["web"]
    assert "ports" not in services["api"]


@pytest.fixture(autouse=True)
def _restore_limiter():
    """These tests enable the limiter; leave it off for the rest of the suite."""
    yield
    limiter.enabled = False
