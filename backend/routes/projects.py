"""Project CRUD endpoints."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import projects_col, conversations_col
from routes.deps import require_auth

router = APIRouter()


class CreateProjectRequest(BaseModel):
    name: str


@router.get("/projects", dependencies=[Depends(require_auth)])
def list_projects():
    docs = projects_col.find({}, {"_id": 0}).sort("created_at", 1)
    return list(docs)


@router.post("/projects", dependencies=[Depends(require_auth)])
def create_project(body: CreateProjectRequest):
    doc = {
        "project_id": str(uuid.uuid4()),
        "name": body.name,
        "created_at": datetime.now(timezone.utc),
    }
    projects_col.insert_one(doc)
    doc.pop("_id", None)
    return doc


@router.delete("/projects/{project_id}", dependencies=[Depends(require_auth)])
def delete_project(project_id: str):
    # Unassign all conversations in this project before deleting it
    conversations_col.update_many(
        {"project_id": project_id},
        {"$set": {"project_id": None}},
    )
    result = projects_col.delete_one({"project_id": project_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Project not found.")
    return {"ok": True}
