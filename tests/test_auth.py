"""Shared-password login and bearer enforcement."""

import json
import logging

import bcrypt
import pytest
from conftest import TEST_PASSWORD

from policy_assistant.api.routes import auth as auth_routes


def _cost4_hash() -> str:
    return bcrypt.hashpw(TEST_PASSWORD.encode(), bcrypt.gensalt(4)).decode()


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


SECOND_PASSWORD = "professor-review-password"


def _second_hash() -> str:
    return bcrypt.hashpw(SECOND_PASSWORD.encode(), bcrypt.gensalt(4)).decode()


def test_second_password_logs_in_alongside_the_first(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD_HASH_2", _second_hash())
    for password in (TEST_PASSWORD, SECOND_PASSWORD):
        response = client.post("/api/auth/login", json={"password": password})
        assert response.status_code == 200, password
        token = response.json()["access_token"]
        assert (
            client.get(
                "/api/conversations", headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 200
        )
    assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401


def test_second_password_is_rejected_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD_HASH_2", raising=False)
    assert client.post("/api/auth/login", json={"password": SECOND_PASSWORD}).status_code == 401


def test_empty_second_hash_means_one_password(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD_HASH_2", "")
    assert client.post("/api/auth/login", json={"password": TEST_PASSWORD}).status_code == 200
    assert auth_routes.configured_password_hashes() == [
        ("APP_PASSWORD_HASH", auth_routes.os.environ["APP_PASSWORD_HASH"])
    ]


def test_second_hash_alone_does_not_replace_the_first(client, monkeypatch, caplog):
    monkeypatch.setenv("APP_PASSWORD_HASH", "")
    monkeypatch.setenv("APP_PASSWORD_HASH_2", _second_hash())
    with caplog.at_level(logging.ERROR, logger="policy_assistant.api.routes.auth"):
        response = client.post("/api/auth/login", json={"password": SECOND_PASSWORD})
    assert response.status_code == 500
    assert "APP_PASSWORD_HASH is not configured" in caplog.text


def test_malformed_second_hash_is_a_server_error(client, monkeypatch, caplog):
    # A broken second hash must not quietly fall back to the first password.
    monkeypatch.setenv("APP_PASSWORD_HASH_2", "not-a-bcrypt-hash")
    with caplog.at_level(logging.ERROR, logger="policy_assistant.api.routes.auth"):
        response = client.post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert response.status_code == 500
    assert "APP_PASSWORD_HASH_2 is not a valid bcrypt hash" in caplog.text


def test_configured_password_hashes_keeps_declaration_order(monkeypatch):
    first, second = _cost4_hash(), _second_hash()
    monkeypatch.setenv("APP_PASSWORD_HASH", first)
    monkeypatch.setenv("APP_PASSWORD_HASH_2", second)
    assert auth_routes.configured_password_hashes() == [
        ("APP_PASSWORD_HASH", first),
        ("APP_PASSWORD_HASH_2", second),
    ]


def test_unconfigured_password_is_a_server_error(client, monkeypatch, caplog):
    monkeypatch.setenv("APP_PASSWORD_HASH", "")
    with caplog.at_level(logging.ERROR, logger="policy_assistant.api.routes.auth"):
        response = client.post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert response.status_code == 500
    assert "APP_PASSWORD_HASH is not configured" in caplog.text


def test_malformed_password_hash_is_a_server_error(client, monkeypatch, caplog):
    monkeypatch.setenv("APP_PASSWORD_HASH", "not-a-bcrypt-hash")
    with caplog.at_level(logging.ERROR, logger="policy_assistant.api.routes.auth"):
        response = client.post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert response.status_code == 500
    assert "APP_PASSWORD_HASH is not a valid bcrypt hash" in caplog.text


@pytest.mark.parametrize("damage", ["\n", " "], ids=["trailing-newline", "trailing-space"])
def test_hash_with_trailing_whitespace_does_not_silently_lock_out(
    client, monkeypatch, caplog, damage
):
    # checkpw returns False for these, which would read as a wrong password.
    monkeypatch.setenv("APP_PASSWORD_HASH", _cost4_hash() + damage)
    with caplog.at_level(logging.ERROR, logger="policy_assistant.api.routes.auth"):
        response = client.post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert response.status_code == 500
    assert "not a valid bcrypt hash" in caplog.text


def test_truncated_hash_is_a_server_error_not_a_lockout(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD_HASH", _cost4_hash()[:-1])
    assert client.post("/api/auth/login", json={"password": TEST_PASSWORD}).status_code == 500


def test_lone_surrogate_password_is_rejected_not_a_server_error(client, caplog):
    # json.loads accepts a lone surrogate; str.encode refuses it with a
    # UnicodeEncodeError, which subclasses ValueError.
    body = json.dumps({"password": "\ud800abc"}).encode("utf-8", "surrogatepass")
    with caplog.at_level(logging.ERROR, logger="policy_assistant.api.routes.auth"):
        response = client.post(
            "/api/auth/login", content=body, headers={"content-type": "application/json"}
        )
    assert response.status_code in (401, 422)
    assert "APP_PASSWORD_HASH" not in caplog.text


def test_overlong_password_is_rejected_not_a_server_error(client, monkeypatch):
    """bcrypt 5 raises past 72 bytes where 4 truncated. Pin the 401 on either."""
    real = bcrypt.checkpw

    def strict(password: bytes, hashed: bytes) -> bool:
        if len(password) > auth_routes.BCRYPT_MAX_PASSWORD_BYTES:
            raise ValueError("password cannot be longer than 72 bytes")
        return real(password, hashed)

    monkeypatch.setattr(auth_routes.bcrypt, "checkpw", strict)
    assert client.post("/api/auth/login", json={"password": "a" * 100}).status_code == 401


def test_overlong_correct_password_still_logs_in(client, monkeypatch):
    """Truncation, not rejection: a deployment with a long password keeps working."""
    long_password = "p" * 80
    monkeypatch.setenv(
        "APP_PASSWORD_HASH",
        bcrypt.hashpw(long_password.encode()[:72], bcrypt.gensalt(4)).decode(),
    )
    assert client.post("/api/auth/login", json={"password": long_password}).status_code == 200


@pytest.mark.parametrize("prefix", ["$2a$", "$2b$", "$2y$"])
def test_login_accepts_legacy_bcrypt_prefixes(client, monkeypatch, prefix):
    monkeypatch.setenv("APP_PASSWORD_HASH", prefix + _cost4_hash()[4:])
    assert client.post("/api/auth/login", json={"password": TEST_PASSWORD}).status_code == 200


@pytest.mark.parametrize(
    "value",
    ["", "ci-placeholder", "not-a-bcrypt-hash", "$2b$99$" + "a" * 53, "$2b$04$" + "a" * 52],
)
def test_is_bcrypt_hash_rejects_bad_shapes(value):
    assert not auth_routes.is_bcrypt_hash(value)


def test_protected_routes_reject_missing_and_bad_tokens(client):
    assert client.get("/api/conversations").status_code in (401, 403)
    assert (
        client.get("/api/conversations", headers={"Authorization": "Bearer junk"}).status_code
        == 401
    )


def test_health_and_config_are_public(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert "similarity_threshold" in client.get("/api/config").json()
