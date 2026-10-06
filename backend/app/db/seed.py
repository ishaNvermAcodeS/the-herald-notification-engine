"""
Seed the Herald database with development workflows.

These workflow records correspond to the Phase 1 registered workflow IDs.
Run this once after `alembic upgrade head`.
"""
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.base import Base
from app.db.models import Workflow
from app.db.session import sync_engine, SyncSessionLocal


DEV_WORKFLOWS = [
    {
        "id": "campus-maintenance-alert",
        "name": "Campus Maintenance Alert",
        "description": "Alerts for campus infrastructure maintenance events.",
        "isCritical": False,
        "steps": [{"type": "EMAIL"}, {"type": "IN_APP"}],
    },
    {
        "id": "campus-bulletin",
        "name": "Campus Bulletin",
        "description": "General campus announcements with digest grouping.",
        "isCritical": False,
        "steps": [{"type": "DIGEST", "durationMs": 300000}, {"type": "EMAIL"}, {"type": "IN_APP"}],
    },
    {
        "id": "campus-emergency",
        "name": "Campus Emergency Alert",
        "description": "Critical emergency notifications that override subscriber mutes.",
        "isCritical": True,
        "steps": [{"type": "EMAIL"}, {"type": "IN_APP"}],
    },
]


def seed_workflows(session) -> int:
    """Insert dev workflows if they do not already exist. Returns number inserted."""
    inserted = 0
    for wf in DEV_WORKFLOWS:
        existing = session.get(Workflow, wf["id"])
        if existing is None:
            session.add(
                Workflow(
                    id=wf["id"],
                    name=wf["name"],
                    description=wf["description"],
                    is_critical=wf["isCritical"],
                    steps=wf["steps"],
                )
            )
            inserted += 1
    session.commit()
    return inserted


if __name__ == "__main__":
    print("Seeding development workflows…")
    with SyncSessionLocal() as session:
        count = seed_workflows(session)
    print(f"Done — {count} workflows inserted (existing workflows skipped).")
