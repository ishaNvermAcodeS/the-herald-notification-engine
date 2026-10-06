from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
import uuid

from app.api.deps import get_session
from app.core.auth import verify_api_key
from app.db.models import Subscriber
from app.schemas.dashboard import PreferencesOut, PreferencesUpdate, SubscriberOut, SubscriberUpdate
from app.services.preferences import effective_channels, set_global_preferences

router = APIRouter(prefix="/subscribers", tags=["Subscribers"], dependencies=[Depends(verify_api_key)])


def _get_or_404(session: Session, subscriber_id: str) -> Subscriber:
    sub = session.execute(select(Subscriber).where(Subscriber.subscriber_id == subscriber_id)).scalar()
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Subscriber '{subscriber_id}' does not exist.")
    return sub


@router.get("", response_model=List[SubscriberOut], summary="List subscribers")
def list_subscribers(session: Session = Depends(get_session)):
    subs = session.execute(select(Subscriber).order_by(Subscriber.subscriber_id)).scalars().all()
    return [
        SubscriberOut(subscriberId=s.subscriber_id, email=s.email, name=s.name,
                      channels=effective_channels(session, s.subscriber_id))
        for s in subs
    ]


@router.put("/{subscriber_id}", response_model=SubscriberOut, summary="Create or update a subscriber")
def upsert_subscriber(subscriber_id: str, body: SubscriberUpdate, session: Session = Depends(get_session)):
    session.execute(
        pg_insert(Subscriber).values(id=str(uuid.uuid4()), subscriberId=subscriber_id)
        .on_conflict_do_nothing(index_elements=["subscriberId"])
    )
    sub = _get_or_404(session, subscriber_id)
    if "email" in body.model_fields_set:
        sub.email = body.email
    if "name" in body.model_fields_set:
        sub.name = body.name
    session.flush()
    return SubscriberOut(subscriberId=sub.subscriber_id, email=sub.email, name=sub.name,
                         channels=effective_channels(session, subscriber_id))


@router.get("/{subscriber_id}/preferences", response_model=PreferencesOut)
def get_preferences(subscriber_id: str, session: Session = Depends(get_session)):
    _get_or_404(session, subscriber_id)
    return PreferencesOut(subscriberId=subscriber_id, channels=effective_channels(session, subscriber_id))


@router.put("/{subscriber_id}/preferences", response_model=PreferencesOut)
def put_preferences(subscriber_id: str, body: PreferencesUpdate, session: Session = Depends(get_session)):
    _get_or_404(session, subscriber_id)
    channels = {k: v for k, v in body.model_dump().items() if v is not None}
    return PreferencesOut(subscriberId=subscriber_id, channels=set_global_preferences(session, subscriber_id, channels))
