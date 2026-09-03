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

import pytest
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


def _failed_login(client: TestClient, *, forwarded_for: str | None = None) -> object:
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


def test_trusted_nginx_replace_header_is_honoured(caplog):
    """After Nginx replace, Uvicorn on the Docker pool uses the resolved client."""
    client = _uvicorn_client(peer=NGINX_PEER)
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
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'FORWARDED_ALLOW_IPS: "172.16.0.0/12,192.168.0.0/16"' in compose
    assert "EDGE_SUBNET" not in compose
    assert "APP_SUBNET" not in compose
    assert "ipam:" not in compose
    assert re.search(r"(?m)^  edge:\s*$", compose)
    assert re.search(r"(?m)^  app:\s*$", compose)
    assert re.search(r"(?ms)^  caddy:.*?networks:\n      - edge\n", compose)
    assert re.search(r"(?ms)^  web:.*?networks:\n      - edge\n      - app\n", compose)
    api_networks = re.search(
        r"(?m)^  api:(?:\n(?!  [a-z]).*)*\n    networks:\n((?:      - [^\n]+\n)+)",
        compose,
    )
    assert api_networks is not None
    assert api_networks.group(1).strip() == "- app"
    web_block = re.search(r"(?ms)^  web:(.*?)(?=^  api:)", compose)
    api_block = re.search(r"(?ms)^  api:(.*?)(?=^volumes:)", compose)
    assert web_block is not None and "ports:" not in web_block.group(1)
    assert api_block is not None and "ports:" not in api_block.group(1)
    assert re.search(r"(?ms)^  caddy:.*?ports:\n", compose)


@pytest.fixture(autouse=True)
def _restore_limiter():
    """These tests enable the limiter; leave it off for the rest of the suite."""
    yield
    limiter.enabled = False
