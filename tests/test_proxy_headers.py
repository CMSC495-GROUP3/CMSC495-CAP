"""X-Forwarded-For trust chain: external client → Caddy → Nginx → Uvicorn.

Production path (see docker-compose.yml):

* Caddy is the public edge. With no trusted_proxies, it replaces any
  client-supplied X-Forwarded-* with the connecting client's address.
* Nginx trusts X-Forwarded-For only from the edge network (set_real_ip_from),
  resolves $remote_addr to that client, then *replaces* X-Forwarded-For with
  $remote_addr toward Uvicorn.
* Uvicorn trusts X-Forwarded-* only from the app network (FORWARDED_ALLOW_IPS).

These tests model that full chain. Config-string checks are supplements only.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from pathlib import Path

import pytest
from conftest import TEST_PASSWORD
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from policy_assistant.api import main
from policy_assistant.api.limiter import limiter

# Must match docker-compose.yml x-edge-subnet / x-app-subnet and nginx.conf.
EDGE_CIDR = "10.250.0.0/24"
APP_CIDR = "10.251.0.0/24"
CADDY_PEER = ("10.250.0.2", 443)
NGINX_PEER = ("10.251.0.10", 50000)
REPO_ROOT = Path(__file__).resolve().parents[1]


def _caddy_sanitize(*, client_ip: str, forged_xff: str | None) -> str:
    """Model Caddy at the edge: ignore client XFF; emit the connecting address.

    Matches Caddy reverse_proxy with no trusted_proxies (see Caddyfile).
    """
    del forged_xff  # Explicitly discarded at the edge.
    return client_ip


def _nginx_resolve_and_replace(*, peer_ip: str, xff_from_caddy: str) -> str | None:
    """Model Nginx real_ip + replace toward Uvicorn.

    set_real_ip_from EDGE_CIDR; real_ip_header X-Forwarded-For;
    proxy_set_header X-Forwarded-For $remote_addr;
    """
    peer = _ip(peer_ip)
    if peer in _network(EDGE_CIDR):
        # real_ip_recursive off: take the (only) address Caddy placed in XFF.
        return xff_from_caddy.split(",")[0].strip()
    # Untrusted peer: $remote_addr stays the TCP peer; do not honour XFF.
    return peer_ip


def _ip(value: str):
    return ipaddress.ip_address(value)


def _network(cidr: str):
    return ipaddress.ip_network(cidr)


def _uvicorn_client(*, peer: tuple[str, int], trusted_hosts: str = APP_CIDR) -> TestClient:
    """TestClient as if uvicorn wrapped the app with the given trust list."""
    return TestClient(
        ProxyHeadersMiddleware(main.app, trusted_hosts=trusted_hosts),
        client=peer,
    )


def _chain_login(
    *,
    external_client: str,
    forged_xff: str | None = None,
    password: str = "wrong",
):
    """Drive one login through the modelled Caddy → Nginx → Uvicorn chain."""
    caddy_xff = _caddy_sanitize(client_ip=external_client, forged_xff=forged_xff)
    nginx_xff = _nginx_resolve_and_replace(peer_ip=CADDY_PEER[0], xff_from_caddy=caddy_xff)
    assert nginx_xff is not None
    client = _uvicorn_client(peer=NGINX_PEER)
    return client.post(
        "/api/auth/login",
        json={"password": password},
        headers={"X-Forwarded-For": nginx_xff},
    )


def _failed_login(client: TestClient, *, forwarded_for: str | None = None) -> object:
    headers = {"X-Forwarded-For": forwarded_for} if forwarded_for is not None else None
    return client.post("/api/auth/login", json={"password": "wrong"}, headers=headers)


# ── Behavioural chain proofs ──────────────────────────────────────────────────


def test_forged_external_xff_is_ignored_in_failed_login_log(caplog):
    """Client-supplied XFF never becomes request.client after the full chain."""
    limiter.enabled = True
    limiter.reset()
    with caplog.at_level(logging.WARNING, logger="policy_assistant.api.routes.auth"):
        response = _chain_login(external_client="203.0.113.9", forged_xff="1.2.3.4")
    assert response.status_code == 401
    assert "Failed login attempt from 203.0.113.9" in caplog.text
    assert "1.2.3.4" not in caplog.text
    assert CADDY_PEER[0] not in caplog.text
    assert NGINX_PEER[0] not in caplog.text


def test_two_external_clients_retain_separate_rate_limit_identities():
    """Two real clients do not share Caddy's address as one limiter key."""
    limiter.enabled = True
    limiter.reset()
    client_a = "203.0.113.10"
    client_b = "203.0.113.20"
    for _ in range(10):
        assert _chain_login(external_client=client_a).status_code == 401
    assert _chain_login(external_client=client_a).status_code == 429
    assert _chain_login(external_client=client_b).status_code == 401


def test_eleven_rotating_forged_headers_from_one_client_still_429():
    """Rotating forged XFF at the public edge cannot bypass the 10/minute limit."""
    limiter.enabled = True
    limiter.reset()
    statuses = [
        _chain_login(external_client="203.0.113.9", forged_xff=f"1.2.3.{i}").status_code
        for i in range(11)
    ]
    assert statuses[:10] == [401] * 10
    assert statuses[10] == 429


def test_exhausted_client_does_not_rate_limit_unrelated_client():
    """One client's 429 must not lock out a different external address."""
    limiter.enabled = True
    limiter.reset()
    exhausted = "198.51.100.1"
    other = "198.51.100.2"
    for _ in range(10):
        assert _chain_login(external_client=exhausted).status_code == 401
    assert _chain_login(external_client=exhausted).status_code == 429
    assert _chain_login(external_client=other).status_code == 401


def test_failed_login_log_uses_resolved_external_address(caplog):
    """Log line is the external client, never forged / Caddy / Nginx addresses."""
    limiter.enabled = True
    limiter.reset()
    with caplog.at_level(logging.WARNING, logger="policy_assistant.api.routes.auth"):
        assert (
            _chain_login(
                external_client="203.0.113.50",
                forged_xff="8.8.8.8, 1.2.3.4",
            ).status_code
            == 401
        )
    assert "Failed login attempt from 203.0.113.50" in caplog.text
    for bad in ("8.8.8.8", "1.2.3.4", CADDY_PEER[0], NGINX_PEER[0]):
        assert bad not in caplog.text


def test_untrusted_source_cannot_make_uvicorn_accept_forwarded_identity(caplog):
    """A peer outside the app CIDR cannot forge request.client via XFF."""
    client = _uvicorn_client(peer=("203.0.113.9", 44321), trusted_hosts=APP_CIDR)
    limiter.enabled = True
    limiter.reset()
    with caplog.at_level(logging.WARNING, logger="policy_assistant.api.routes.auth"):
        response = _failed_login(client, forwarded_for="1.2.3.4")
    assert response.status_code == 401
    assert "Failed login attempt from 203.0.113.9" in caplog.text
    assert "1.2.3.4" not in caplog.text


def test_caddy_peer_on_edge_cannot_bypass_uvicorn_app_trust(caplog):
    """Even Caddy's address is untrusted by Uvicorn; only the app CIDR is."""
    client = _uvicorn_client(peer=CADDY_PEER, trusted_hosts=APP_CIDR)
    limiter.enabled = True
    limiter.reset()
    with caplog.at_level(logging.WARNING, logger="policy_assistant.api.routes.auth"):
        response = _failed_login(client, forwarded_for="203.0.113.77")
    assert response.status_code == 401
    assert f"Failed login attempt from {CADDY_PEER[0]}" in caplog.text
    assert "203.0.113.77" not in caplog.text


def test_normal_proxied_login_still_works():
    """Happy-path login through the modelled chain still issues a token."""
    limiter.enabled = True
    limiter.reset()
    response = _chain_login(external_client="203.0.113.9", password=TEST_PASSWORD)
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_nginx_ignores_xff_from_non_edge_peer():
    """real_ip must not rewrite $remote_addr for peers outside the edge CIDR."""
    assert (
        _nginx_resolve_and_replace(peer_ip="203.0.113.9", xff_from_caddy="1.2.3.4") == "203.0.113.9"
    )


def test_direct_local_uvicorn_trust_ignores_non_loopback_forgery(caplog):
    """Bare local uvicorn defaults to trusting only 127.0.0.1."""
    client = _uvicorn_client(peer=("203.0.113.9", 44321), trusted_hosts="127.0.0.1")
    limiter.enabled = True
    limiter.reset()
    with caplog.at_level(logging.WARNING, logger="policy_assistant.api.routes.auth"):
        response = _failed_login(client, forwarded_for="1.2.3.4")
    assert response.status_code == 401
    assert "Failed login attempt from 203.0.113.9" in caplog.text
    assert "1.2.3.4" not in caplog.text


def test_login_still_works_without_forwarded_headers():
    """Direct clients (no XFF) remain usable under the Compose app trust list."""
    client = _uvicorn_client(peer=("203.0.113.9", 44321))
    limiter.enabled = True
    limiter.reset()
    response = client.post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_trusted_nginx_replace_header_is_honoured(caplog):
    """After Nginx replace, Uvicorn on the app network uses the resolved client."""
    client = _uvicorn_client(peer=NGINX_PEER)
    limiter.enabled = True
    limiter.reset()
    with caplog.at_level(logging.WARNING, logger="policy_assistant.api.routes.auth"):
        response = _failed_login(client, forwarded_for="203.0.113.50")
    assert response.status_code == 401
    assert "Failed login attempt from 203.0.113.50" in caplog.text


def test_legacy_append_style_xff_documents_why_nginx_must_replace(caplog):
    """If Nginx appended, a forged leftmost hop would win under uvicorn rules."""
    client = _uvicorn_client(peer=NGINX_PEER)
    limiter.enabled = True
    limiter.reset()
    with caplog.at_level(logging.WARNING, logger="policy_assistant.api.routes.auth"):
        response = _failed_login(client, forwarded_for=f"1.2.3.4, {NGINX_PEER[0]}")
    assert response.status_code == 401
    assert "Failed login attempt from 1.2.3.4" in caplog.text


# ── Config supplements (not the only proof) ───────────────────────────────────


def test_nginx_uses_real_ip_then_replaces_xff_with_remote_addr():
    conf = (REPO_ROOT / "web" / "nginx.conf").read_text(encoding="utf-8")
    assert "set_real_ip_from ${EDGE_SUBNET};" in conf
    assert "real_ip_header X-Forwarded-For;" in conf
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in conf
    assert not re.search(
        r"proxy_set_header\s+X-Forwarded-For\s+\$proxy_add_x_forwarded_for",
        conf,
    )


def test_caddyfile_does_not_trust_upstream_proxies():
    caddy = (REPO_ROOT / "Caddyfile").read_text(encoding="utf-8")
    assert "reverse_proxy web:80" in caddy
    # Directive must not appear outside comments (edge must not trust peers).
    for line in caddy.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped:
            assert "trusted_proxies" not in stripped


def test_dockerfile_does_not_trust_every_forwarded_ip():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "--forwarded-allow-ips=*" not in dockerfile
    assert "--proxy-headers" in dockerfile


def test_compose_splits_edge_and_app_trust_cidrs():
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert f'x-edge-subnet: &edge-subnet "${{EDGE_SUBNET:-{EDGE_CIDR}}}"' in compose
    assert f'x-app-subnet: &app-subnet "${{APP_SUBNET:-{APP_CIDR}}}"' in compose
    assert "EDGE_SUBNET: *edge-subnet" in compose
    assert 'NGINX_ENVSUBST_FILTER: "^EDGE_SUBNET$"' in compose
    assert "FORWARDED_ALLOW_IPS: *app-subnet" in compose
    assert re.search(r"subnet:\s*\*edge-subnet", compose)
    assert re.search(r"subnet:\s*\*app-subnet", compose)
    # API must not sit on the edge network (would widen uvicorn's trust surface).
    api_block = compose.split("api:", 1)[1].split("volumes:", 1)[0]
    assert "- app" in api_block
    assert "- edge" not in api_block


@pytest.fixture(autouse=True)
def _restore_limiter():
    """These tests enable the limiter; leave it off for the rest of the suite."""
    yield
    limiter.enabled = False
