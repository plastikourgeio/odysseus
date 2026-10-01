"""Owner-scoped Project Context control-plane routes."""

from fastapi import APIRouter, HTTPException, Request

from core.database import Session as DbSession, SessionLocal
from src.auth_helpers import effective_user
from src.project_context import (
    archive_project,
    attach_session,
    create_project,
    detach_session,
    get_project,
    get_session_project,
    list_projects,
)


router = APIRouter(tags=["projects"])


def _owner(request: Request) -> str:
    user = effective_user(request)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    return user


def _require_owned_session(session_id: str, owner: str) -> None:
    db = SessionLocal()
    try:
        row = (
            db.query(DbSession.id)
            .filter(
                DbSession.id == session_id,
                DbSession.owner == owner,
            )
            .first()
        )
    finally:
        db.close()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Session {session_id} not found",
        )


@router.get("/projects")
def api_list_projects(
    request: Request,
    include_archived: bool = False,
):
    owner = _owner(request)

    return {
        "projects": list_projects(
            owner,
            include_archived=include_archived,
        )
    }


@router.post("/projects", status_code=201)
async def api_create_project(request: Request):
    owner = _owner(request)

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Request body must be valid JSON",
        )

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=422,
            detail="Request body must be a JSON object",
        )

    title = str(payload.get("title") or "").strip()
    goal = str(payload.get("goal") or "").strip()
    state = payload.get("state")

    if not title:
        raise HTTPException(
            status_code=422,
            detail="title is required",
        )

    if state is not None and not isinstance(state, dict):
        raise HTTPException(
            status_code=422,
            detail="state must be an object",
        )

    try:
        project = create_project(
            owner=owner,
            title=title,
            goal=goal,
            state=state,
            status="active",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        )

    return {"project": project}


@router.get("/projects/{project_id}")
def api_get_project(
    project_id: str,
    request: Request,
):
    owner = _owner(request)
    project = get_project(project_id, owner)

    if project is None:
        raise HTTPException(
            status_code=404,
            detail="Project not found",
        )

    return {"project": project}


@router.post(
    "/projects/{project_id}/attach/{session_id}"
)
def api_attach_project(
    project_id: str,
    session_id: str,
    request: Request,
):
    owner = _owner(request)

    _require_owned_session(session_id, owner)

    project = get_project(project_id, owner)

    if project is None:
        raise HTTPException(
            status_code=404,
            detail="Project not found",
        )

    if project.get("status") == "archived":
        raise HTTPException(
            status_code=409,
            detail="Archived projects cannot be attached",
        )

    try:
        attach_session(
            project_id=project_id,
            session_id=session_id,
            owner=owner,
        )
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=404,
            detail="Project or session not found",
        )

    return {
        "session_id": session_id,
        "project": get_session_project(
            session_id=session_id,
            owner=owner,
        ),
    }


@router.get("/sessions/{session_id}/project")
def api_get_session_project(
    session_id: str,
    request: Request,
):
    owner = _owner(request)

    _require_owned_session(session_id, owner)

    return {
        "session_id": session_id,
        "project": get_session_project(
            session_id=session_id,
            owner=owner,
        ),
    }


@router.post("/sessions/{session_id}/detach-project")
def api_detach_project(
    session_id: str,
    request: Request,
):
    owner = _owner(request)

    _require_owned_session(session_id, owner)

    previous = get_session_project(
        session_id=session_id,
        owner=owner,
    )

    detach_session(
        session_id=session_id,
        owner=owner,
    )

    return {
        "session_id": session_id,
        "detached": previous is not None,
        "project_id": (
            previous.get("project_id")
            if previous
            else None
        ),
    }


@router.post("/projects/{project_id}/archive")
def api_archive_project(
    project_id: str,
    request: Request,
):
    owner = _owner(request)

    project = archive_project(
        project_id=project_id,
        owner=owner,
    )

    if project is None:
        raise HTTPException(
            status_code=404,
            detail="Project not found",
        )

    return {"project": project}
