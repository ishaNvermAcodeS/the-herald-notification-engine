"""Subscriber channel preferences (Phase 5)."""
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import Preference

CHANNELS = ("email", "in_app")
STEP_TO_CHANNEL = {"EMAIL": "email", "IN_APP": "in_app"}


def effective_channels(session: Session, subscriber_id: str, workflow_id: Optional[str] = None) -> Dict[str, bool]:
    """
    Resolve channel toggles. Workflow-specific values override global ones;
    anything unset defaults to enabled.
    """
    rows = session.execute(
        select(Preference).where(Preference.subscriber_id == subscriber_id)
    ).scalars().all()
    result = {c: True for c in CHANNELS}
    for pref in (p for p in rows if p.workflow_id is None):
        result.update({k: bool(v) for k, v in pref.channels.items() if k in CHANNELS})
    if workflow_id:
        for pref in (p for p in rows if p.workflow_id == workflow_id):
            result.update({k: bool(v) for k, v in pref.channels.items() if k in CHANNELS})
    return result


def is_channel_enabled(session: Session, subscriber_id: str, workflow_id: str, step_type: str) -> bool:
    return effective_channels(session, subscriber_id, workflow_id)[STEP_TO_CHANNEL[step_type]]


def set_global_preferences(session: Session, subscriber_id: str, channels: Dict[str, bool]) -> Dict[str, bool]:
    """Upsert the global (workflowId NULL) preference, merging with existing toggles."""
    existing = session.execute(
        select(Preference).where(Preference.subscriber_id == subscriber_id, Preference.workflow_id.is_(None))
    ).scalar()
    if existing:
        existing.channels = {**existing.channels, **channels}
    else:
        session.add(Preference(subscriber_id=subscriber_id, workflow_id=None, channels=dict(channels)))
    session.flush()
    return effective_channels(session, subscriber_id)
