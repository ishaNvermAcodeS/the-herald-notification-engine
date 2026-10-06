"""In-app notification feed (persistent, PostgreSQL-backed)."""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.core.auth import verify_api_key
from app.db.models import Job, Message, Notification
from app.schemas.dashboard import InboxItem, InboxOut

router = APIRouter(prefix="/notifications", tags=["Notifications"], dependencies=[Depends(verify_api_key)])


def _inbox(session: Session, subscriber_id: str, unread_only: bool, limit: int) -> InboxOut:
    base = (
        select(Message, Notification)
        .join(Job, Message.job_id == Job.id)
        .join(Notification, Job.notification_id == Notification.id)
        .where(Message.subscriber_id == subscriber_id, Message.channel == "IN_APP", Message.status == "SENT")
    )
    q = base.where(Message.read.is_(False)) if unread_only else base
    rows = session.execute(q.order_by(Message.created_at.desc()).limit(limit)).all()
    unread = session.scalar(
        select(func.count()).select_from(Message).where(
            Message.subscriber_id == subscriber_id, Message.channel == "IN_APP",
            Message.status == "SENT", Message.read.is_(False))
    )
    items = [
        InboxItem(id=m.id, notificationId=n.id, workflowId=n.workflow_id, title=m.subject, body=m.content,
                  read=m.read, seen=m.seen, status=n.status, createdAt=m.created_at, aiSummary=m.ai_summary)
        for m, n in rows
    ]
    return InboxOut(subscriberId=subscriber_id, unreadCount=unread or 0, items=items)


@router.get("", response_model=InboxOut, summary="All in-app notifications for a subscriber")
def list_notifications(subscriberId: str = Query(..., min_length=1), limit: int = Query(50, ge=1, le=200),
                       session: Session = Depends(get_session)):
    return _inbox(session, subscriberId, False, limit)


@router.get("/unread", response_model=InboxOut, summary="Unread in-app notifications")
def list_unread(subscriberId: str = Query(..., min_length=1), limit: int = Query(50, ge=1, le=200),
                session: Session = Depends(get_session)):
    return _inbox(session, subscriberId, True, limit)


@router.post("/{message_id}/read", status_code=status.HTTP_200_OK, summary="Mark a notification read")
def mark_read(message_id: str, subscriberId: str = Query(..., min_length=1),
              session: Session = Depends(get_session)):
    updated = session.execute(
        update(Message)
        .where(Message.id == message_id, Message.subscriber_id == subscriberId, Message.channel == "IN_APP")
        .values(read=True, seen=True)
        .returning(Message.id)
    ).scalar()
    if not updated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found for this subscriber.")
    return {"id": message_id, "read": True}
