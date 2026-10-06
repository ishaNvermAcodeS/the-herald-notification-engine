"""
Recipient resolution seam for Phase 4.

The workflow executor depends only on the ``RecipientResolver`` protocol.
The fan-out developer's branch can swap in a richer implementation (groups,
topics, bulk subscriber lookup) without touching the executor.

``DefaultRecipientResolver`` is the minimal implementation: it normalises the
event's ``to`` field into an ordered, de-duplicated list of subscriber IDs and
makes sure a ``Subscriber`` row exists for each (required by the
``notifications.subscriberId`` foreign key).
"""
import uuid
from typing import List, Protocol, Sequence, Union

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import Subscriber


class RecipientResolver(Protocol):
    def resolve(self, session: Session, to: Union[str, Sequence[str]]) -> List[str]:
        """Return the distinct subscriberIds that should receive the event."""
        ...


class DefaultRecipientResolver:
    def resolve(self, session: Session, to: Union[str, Sequence[str]]) -> List[str]:
        raw = [to] if isinstance(to, str) else list(to)
        subscriber_ids = list(dict.fromkeys(s.strip() for s in raw if s and s.strip()))
        if not subscriber_ids:
            return []

        # Insert-if-missing is race-safe across concurrent workers.
        session.execute(
            pg_insert(Subscriber)
            .values([{"id": str(uuid.uuid4()), "subscriberId": sid} for sid in subscriber_ids])
            .on_conflict_do_nothing(index_elements=["subscriberId"])
        )
        existing = session.execute(
            select(Subscriber.subscriber_id).where(Subscriber.subscriber_id.in_(subscriber_ids))
        ).scalars()
        found = set(existing)
        return [sid for sid in subscriber_ids if sid in found]

