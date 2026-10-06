"""
Workflow repository — Phase 2 database-backed workflow lookup.

Replaces the Phase 1 REGISTERED_WORKFLOWS config list
so workflow existence is validated against PostgreSQL.
"""
from typing import Optional
from sqlalchemy import select
from sqlalchemy.orm import Session as SyncSession

from app.db.models import Workflow


def get_workflow_sync(session: SyncSession, workflow_id: str) -> Optional[Workflow]:
    """Return a Workflow by ID using a sync session, or None if not found."""
    return session.get(Workflow, workflow_id)


def workflow_exists_sync(session: SyncSession, workflow_id: str) -> bool:
    """Return True if a workflow with the given ID exists."""
    result = session.execute(
        select(Workflow.id).where(Workflow.id == workflow_id).limit(1)
    )
    return result.scalar() is not None
