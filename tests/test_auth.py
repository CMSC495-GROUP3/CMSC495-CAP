"""Shared-password login and bearer enforcement."""

from conftest import TEST_PASSWORD


def test_login_issues_a_usable_token(client):
    response = client.post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert (
        client.get("/api/conversations", headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )


def test_wrong_password_is_rejected(client):
    assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401


def test_unconfigured_password_is_a_server_error(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD_HASH", "")
    assert client.post("/api/auth/login", json={"password": TEST_PASSWORD}).status_code == 500


def test_malformed_password_hash_is_a_server_error(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD_HASH", "not-a-bcrypt-hash")
    assert client.post("/api/auth/login", json={"password": TEST_PASSWORD}).status_code == 500


def test_protected_routes_reject_missing_and_bad_tokens(client):
    assert client.get("/api/conversations").status_code in (401, 403)
    assert (
        client.get("/api/conversations", headers={"Authorization": "Bearer junk"}).status_code
        == 401
    )


def test_health_and_config_are_public(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert "similarity_threshold" in client.get("/api/config").json()
