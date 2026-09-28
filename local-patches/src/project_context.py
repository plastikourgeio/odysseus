"""Owner-scoped persistent STEM project context for Odysseus."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from core.database import (
    ProjectContext,
    Session as DbSession,
    SessionLocal,
)


STATE_KEYS = (
    "constraints",
    "decisions",
    "components",
    "failed_attempts",
    "open_questions",
    "next_steps",
)


def default_project_state() -> Dict[str, list]:
    return {
        key: []
        for key in STATE_KEYS
    }


def normalize_project_state(value: Any) -> Dict[str, list]:
    result = default_project_state()

    if not isinstance(value, dict):
        return result

    for key in STATE_KEYS:
        items = value.get(key)

        if isinstance(items, list):
            result[key] = items

    return result


def _serialize(project: ProjectContext) -> dict:
    return {
        "project_id": project.id,
        "owner": project.owner,
        "title": project.title,
        "goal": project.goal or "",
        "status": project.status or "active",
        "state": normalize_project_state(
            project.state_json
        ),
        "created_at": (
            project.created_at.isoformat()
            if getattr(project, "created_at", None)
            else None
        ),
        "updated_at": (
            project.updated_at.isoformat()
            if getattr(project, "updated_at", None)
            else None
        ),
    }


def create_project(
    owner: str,
    title: str,
    goal: str = "",
    state: Optional[dict] = None,
    project_id: Optional[str] = None,
    status: str = "active",
) -> dict:
    owner = str(owner or "").strip()
    title = str(title or "").strip()

    if not owner:
        raise ValueError("owner is required")

    if not title:
        raise ValueError("title is required")

    db = SessionLocal()

    try:
        project = ProjectContext(
            id=project_id or str(uuid.uuid4()),
            owner=owner,
            title=title,
            goal=str(goal or "").strip(),
            status=str(status or "active").strip() or "active",
            state_json=normalize_project_state(state),
        )

        db.add(project)
        db.commit()
        db.refresh(project)

        return _serialize(project)

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def get_project(
    project_id: str,
    owner: str,
) -> Optional[dict]:
    project_id = str(project_id or "").strip()
    owner = str(owner or "").strip()

    if not project_id or not owner:
        return None

    db = SessionLocal()

    try:
        project = (
            db.query(ProjectContext)
            .filter(
                ProjectContext.id == project_id,
                ProjectContext.owner == owner,
            )
            .first()
        )

        if project is None:
            return None

        return _serialize(project)

    finally:
        db.close()


def get_session_project(
    session_id: str,
    owner: str,
) -> Optional[dict]:
    session_id = str(session_id or "").strip()
    owner = str(owner or "").strip()

    if not session_id or not owner:
        return None

    db = SessionLocal()

    try:
        session = (
            db.query(DbSession)
            .filter(
                DbSession.id == session_id,
                DbSession.owner == owner,
            )
            .first()
        )

        if (
            session is None
            or not session.project_id
        ):
            return None

        project = (
            db.query(ProjectContext)
            .filter(
                ProjectContext.id == session.project_id,
                ProjectContext.owner == owner,
            )
            .first()
        )

        if project is None:
            return None

        return _serialize(project)

    finally:
        db.close()


def _sync_runtime_project_id(
    session_id: str,
    project_id: Optional[str],
) -> None:
    try:
        from core.models import (
            get_session_manager_instance,
        )

        manager = get_session_manager_instance()

        if manager is None:
            return

        session = manager.sessions.get(session_id)

        if session is not None:
            session.project_id = project_id

    except Exception:
        # DB remains authoritative.
        pass


def attach_session(
    project_id: str,
    session_id: str,
    owner: str,
) -> dict:
    project_id = str(project_id or "").strip()
    session_id = str(session_id or "").strip()
    owner = str(owner or "").strip()

    if not project_id:
        raise ValueError("project_id is required")

    if not session_id:
        raise ValueError("session_id is required")

    if not owner:
        raise ValueError("owner is required")

    db = SessionLocal()

    try:
        project = (
            db.query(ProjectContext)
            .filter(
                ProjectContext.id == project_id,
                ProjectContext.owner == owner,
            )
            .first()
        )

        if project is None:
            raise ValueError(
                "Project not found for this owner"
            )

        session = (
            db.query(DbSession)
            .filter(
                DbSession.id == session_id,
                DbSession.owner == owner,
            )
            .first()
        )

        if session is None:
            raise ValueError(
                "Session not found for this owner"
            )

        session.project_id = project.id

        db.commit()

        _sync_runtime_project_id(
            session.id,
            project.id,
        )

        return _serialize(project)

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def detach_session(
    session_id: str,
    owner: str,
) -> bool:
    session_id = str(session_id or "").strip()
    owner = str(owner or "").strip()

    if not session_id or not owner:
        return False

    db = SessionLocal()

    try:
        session = (
            db.query(DbSession)
            .filter(
                DbSession.id == session_id,
                DbSession.owner == owner,
            )
            .first()
        )

        if session is None:
            return False

        session.project_id = None

        db.commit()

        _sync_runtime_project_id(
            session.id,
            None,
        )

        return True

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def render_project_context(
    project_id: str,
    owner: str,
) -> str:
    project = get_project(
        project_id,
        owner,
    )

    if project is None:
        return ""

    state = project["state"]

    lines = [
        "[PROJECT CONTEXT]",
        f"Project: {project['title']}",
        f"Status: {project['status']}",
        f"Goal: {project['goal'] or '(not specified)'}",
    ]

    labels = (
        ("constraints", "Constraints"),
        ("decisions", "Decisions"),
        ("components", "Components"),
        ("failed_attempts", "Failed attempts"),
        ("open_questions", "Open questions"),
        ("next_steps", "Next steps"),
    )

    for key, label in labels:
        values = state.get(key) or []

        if not values:
            continue

        lines.append("")
        lines.append(f"{label}:")

        for item in values:
            lines.append(f"- {item}")

    return "\n".join(lines)



# ODYSSEUS PROJECT CONTEXT CONTROL PLANE v0.3

def list_projects(owner: str, *, include_archived: bool = False):
    """Return owner-scoped projects ordered by most recently updated."""
    owner = str(owner or "").strip()
    if not owner:
        raise ValueError("owner is required")

    db = SessionLocal()
    try:
        query = db.query(ProjectContext).filter(
            ProjectContext.owner == owner
        )

        if not include_archived:
            query = query.filter(
                ProjectContext.status != "archived"
            )

        rows = query.order_by(
            ProjectContext.updated_at.desc(),
            ProjectContext.created_at.desc(),
        ).all()

        return [_serialize(row) for row in rows]
    finally:
        db.close()


def archive_project(project_id: str, owner: str):
    """Archive an owner-scoped project without deleting its session links."""
    project_id = str(project_id or "").strip()
    owner = str(owner or "").strip()

    if not project_id:
        raise ValueError("project_id is required")
    if not owner:
        raise ValueError("owner is required")

    db = SessionLocal()
    try:
        row = (
            db.query(ProjectContext)
            .filter(
                ProjectContext.id == project_id,
                ProjectContext.owner == owner,
            )
            .first()
        )

        if row is None:
            return None

        row.status = "archived"
        db.commit()
        db.refresh(row)

        return _serialize(row)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
