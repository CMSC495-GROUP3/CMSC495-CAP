"""Conversation and project CRUD."""

from policy_assistant.api.db import conversations_col


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
