"""Conversation and project CRUD."""

import threading

import pytest

from policy_assistant.api.db import conversations_col, projects_col
from policy_assistant.api.routes import conversations as conversations_routes
from policy_assistant.api.routes import projects as projects_routes


def test_conversation_lifecycle(client, auth):
    created = client.post("/api/conversations", json={"title": "PTO"}, headers=auth).json()
    sid = created["session_id"]
    assert created["messages"] == [] and created["project_id"] is None

    listed = client.get("/api/conversations", headers=auth).json()
    assert [c["session_id"] for c in listed] == [sid]
    assert "messages" not in listed[0]

    assert client.get(f"/api/conversations/{sid}", headers=auth).json()["title"] == "PTO"
    assert client.get("/api/conversations/missing", headers=auth).status_code == 404

    assert client.patch(
        f"/api/conversations/{sid}", json={"title": "Renamed"}, headers=auth
    ).json() == {"ok": True}
    assert client.get(f"/api/conversations/{sid}", headers=auth).json()["title"] == "Renamed"
    assert (
        client.patch("/api/conversations/missing", json={"title": "x"}, headers=auth).status_code
        == 404
    )

    deleted = client.delete(f"/api/conversations/{sid}", headers=auth)
    assert deleted.json() == {"ok": True}
    deleted_again = client.delete(f"/api/conversations/{sid}", headers=auth)
    assert deleted_again.status_code == 404


def test_projects_group_conversations_and_release_them_on_delete(client, auth):
    project = client.post("/api/projects", json={"name": "Onboarding"}, headers=auth).json()
    pid = project["project_id"]
    assert "_id" not in project
    assert [p["project_id"] for p in client.get("/api/projects", headers=auth).json()] == [pid]

    sid = client.post(
        "/api/conversations", json={"title": "c", "project_id": pid}, headers=auth
    ).json()["session_id"]
    assert client.get(f"/api/conversations/{sid}", headers=auth).json()["project_id"] == pid

    # Explicit null unassigns; omitting the field leaves it alone.
    client.patch(f"/api/conversations/{sid}", json={"title": "still mine"}, headers=auth)
    assert client.get(f"/api/conversations/{sid}", headers=auth).json()["project_id"] == pid
    client.patch(f"/api/conversations/{sid}", json={"project_id": None}, headers=auth)
    assert client.get(f"/api/conversations/{sid}", headers=auth).json()["project_id"] is None

    client.patch(f"/api/conversations/{sid}", json={"project_id": pid}, headers=auth)
    deleted = client.delete(f"/api/projects/{pid}", headers=auth)
    assert deleted.json() == {"ok": True}
    assert client.get(f"/api/conversations/{sid}", headers=auth).json()["project_id"] is None
    deleted_again = client.delete(f"/api/projects/{pid}", headers=auth)
    assert deleted_again.status_code == 404


def test_unknown_project_assignments_are_rejected_without_mutation(client, auth):
    create = client.post(
        "/api/conversations",
        json={"title": "orphan", "project_id": "missing"},
        headers=auth,
    )
    assert create.status_code == 404
    assert create.json() == {"detail": "Project not found."}
    assert client.get("/api/conversations", headers=auth).json() == []

    project = client.post("/api/projects", json={"name": "Real"}, headers=auth).json()
    conversation = client.post(
        "/api/conversations",
        json={"title": "original", "project_id": project["project_id"]},
        headers=auth,
    ).json()

    update = client.patch(
        f"/api/conversations/{conversation['session_id']}",
        json={"title": "changed", "project_id": "missing"},
        headers=auth,
    )
    assert update.status_code == 404
    assert update.json() == {"detail": "Project not found."}

    unchanged = client.get(f"/api/conversations/{conversation['session_id']}", headers=auth).json()
    assert unchanged["title"] == "original"
    assert unchanged["project_id"] == project["project_id"]


def test_deleting_unknown_project_does_not_unassign_orphaned_conversation(client, auth):
    conversations_col.insert_one(
        {
            "session_id": "legacy-orphan",
            "title": "Legacy orphan",
            "project_id": "missing",
            "messages": [],
        }
    )

    response = client.delete("/api/projects/missing", headers=auth)
    assert response.status_code == 404
    assert response.json() == {"detail": "Project not found."}
    assert (
        client.get("/api/conversations/legacy-orphan", headers=auth).json()["project_id"]
        == "missing"
    )


def test_delete_project_keeps_project_when_unassign_raises(client, auth, monkeypatch):
    """If update_many fails, the project must still be present (no delete yet)."""
    project = client.post("/api/projects", json={"name": "Fragile"}, headers=auth).json()
    pid = project["project_id"]
    sid = client.post(
        "/api/conversations", json={"title": "c", "project_id": pid}, headers=auth
    ).json()["session_id"]

    def boom(*_args, **_kwargs):
        raise RuntimeError("injected update_many failure")

    monkeypatch.setattr(projects_routes.conversations_col, "update_many", boom)

    with pytest.raises(RuntimeError, match="injected update_many failure"):
        client.delete(f"/api/projects/{pid}", headers=auth)

    assert projects_col.find_one({"project_id": pid}) is not None
    assert client.get(f"/api/conversations/{sid}", headers=auth).json()["project_id"] == pid
    assert any(p["project_id"] == pid for p in client.get("/api/projects", headers=auth).json())


def test_conversation_titles_and_project_names_are_normalized_and_bounded(client, auth):
    blank = client.post("/api/conversations", json={"title": "   "}, headers=auth)
    assert blank.status_code == 422

    oversized = client.post(
        "/api/conversations",
        json={"title": "x" * 201},
        headers=auth,
    )
    assert oversized.status_code == 422

    created = client.post(
        "/api/conversations",
        json={"title": "  leave   question  "},
        headers=auth,
    )
    assert created.status_code == 200
    assert created.json()["title"] == "leave question"

    defaulted = client.post("/api/conversations", json={}, headers=auth)
    assert defaulted.status_code == 200
    assert defaulted.json()["title"] == "New conversation"

    sid = created.json()["session_id"]
    patch_blank = client.patch(
        f"/api/conversations/{sid}",
        json={"title": "\t"},
        headers=auth,
    )
    assert patch_blank.status_code == 422

    patch_ok = client.patch(
        f"/api/conversations/{sid}",
        json={"title": "  follow-up  "},
        headers=auth,
    )
    assert patch_ok.status_code == 200
    assert client.get(f"/api/conversations/{sid}", headers=auth).json()["title"] == "follow-up"

    blank_project = client.post("/api/projects", json={"name": "  "}, headers=auth)
    assert blank_project.status_code == 422

    long_project = client.post("/api/projects", json={"name": "p" * 101}, headers=auth)
    assert long_project.status_code == 422

    project = client.post(
        "/api/projects",
        json={"name": "  Q3   Planning "},
        headers=auth,
    )
    assert project.status_code == 200
    assert project.json()["name"] == "Q3 Planning"


def test_assignment_can_interleave_with_project_delete(client, auth, monkeypatch):
    """Reproduce validate+create TOCTOU: insert can land after the project is deleted.

    `_require_project` and `insert_one` are separate steps with no shared Mongo
    session/transaction (fakemongo and `rag/mongo.py` expose none). Freezing the
    create between those steps lets a concurrent delete finish first, producing
    an assignment to a missing project_id. This documents the pilot limitation;
    it does not claim atomic referential integrity.
    """
    project = client.post("/api/projects", json={"name": "Race"}, headers=auth).json()
    pid = project["project_id"]
    gate = threading.Event()
    release = threading.Event()
    original_insert = conversations_routes.conversations_col.insert_one

    def insert_after_delete(doc):
        gate.set()
        assert release.wait(timeout=5), "timed out waiting for concurrent delete"
        return original_insert(doc)

    monkeypatch.setattr(conversations_routes.conversations_col, "insert_one", insert_after_delete)

    results: list = []

    def create_conversation():
        response = client.post(
            "/api/conversations",
            json={"title": "late assign", "project_id": pid},
            headers=auth,
        )
        results.append(response)

    creator = threading.Thread(target=create_conversation)
    creator.start()
    assert gate.wait(timeout=5), "create never reached insert gate"

    deleted = client.delete(f"/api/projects/{pid}", headers=auth)
    assert deleted.status_code == 200
    assert projects_col.find_one({"project_id": pid}) is None

    release.set()
    creator.join(timeout=5)
    assert not creator.is_alive()

    created = results[0]
    assert created.status_code == 200, created.text
    assert created.json()["project_id"] == pid
    assert (
        client.get(f"/api/conversations/{created.json()['session_id']}", headers=auth).json()[
            "project_id"
        ]
        == pid
    )
