"""Read-only operational views for the admin dashboard."""
from typing import Dict, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.core.auth import verify_api_key
from app.db.models import DigestWindow, Job, Message, Notification
from app.services.digest_service import collect_events
from app.schemas.dashboard import AdminNotificationOut, DigestOut, JobOut, MessageOut

router = APIRouter(prefix="/admin", tags=["Admin"], dependencies=[Depends(verify_api_key)])


def _counts(session: Session, col) -> Dict[str, int]:
    return {k: v for k, v in session.execute(select(col, func.count()).group_by(col)).all()}


@router.get("/overview", summary="Pipeline metrics")
def overview(session: Session = Depends(get_session)):
    notif = _counts(session, Notification.status)
    jobs = _counts(session, Job.status)
    windows = _counts(session, DigestWindow.status)
    emails = dict(session.execute(
        select(Message.status, func.count()).where(Message.channel == "EMAIL").group_by(Message.status)).all())
    in_app = session.scalar(select(func.count()).select_from(Message).where(
        Message.channel == "IN_APP", Message.status == "SENT")) or 0
    events = session.scalar(select(func.count(func.distinct(
        func.regexp_replace(Notification.transaction_id, ":[^:]*$", ""))))) or 0
    retrying = session.scalar(select(func.count()).select_from(Job).where(
        Job.status == "DELAYED", Job.step_type != "DIGEST")) or 0
    return {
        "totalEvents": events,
        "totalNotifications": sum(notif.values()),
        "notificationsByStatus": notif,
        "delivered": {"email": emails.get("SENT", 0), "inApp": in_app},
        "failed": jobs.get("FAILED", 0),
        "pending": jobs.get("PENDING", 0) + jobs.get("RUNNING", 0),
        "retrying": retrying,
        "skipped": jobs.get("SKIPPED", 0),
        "digestCount": sum(windows.values()),
        "digestWindowsByStatus": windows,
    }


@router.get("/notifications", response_model=List[AdminNotificationOut], summary="Recent notifications with jobs")
def notifications(limit: int = Query(50, ge=1, le=200), session: Session = Depends(get_session)):
    rows = session.execute(select(Notification).order_by(Notification.created_at.desc()).limit(limit)).scalars().all()
    ids = [n.id for n in rows]
    jobs = session.execute(select(Job).where(Job.notification_id.in_(ids)).order_by(Job.created_at)).scalars().all()
    job_notif = {j.id: j.notification_id for j in jobs}
    msgs = session.execute(select(Message).where(Message.job_id.in_(list(job_notif)))).scalars().all()
    out = []
    for n in rows:
        out.append(AdminNotificationOut(
            id=n.id, transactionId=n.transaction_id, subscriberId=n.subscriber_id, workflowId=n.workflow_id,
            status=n.status, payload=n.payload, digestWindowId=n.digest_window_id, createdAt=n.created_at,
            jobs=[JobOut(id=j.id, stepType=j.step_type, status=j.status, skipReason=j.skip_reason,
                         attempts=j.attempts, maxAttempts=j.max_attempts, delayUntil=j.delay_until, error=j.error)
                  for j in jobs if j.notification_id == n.id],
            messages=[MessageOut(id=m.id, jobId=m.job_id, channel=m.channel, status=m.status,
                                 providerMessageId=m.provider_message_id, deliveredAt=m.delivered_at)
                      for m in msgs if job_notif.get(m.job_id) == n.id],
        ))
    return out


@router.get("/digests", response_model=List[DigestOut], summary="Digest windows with AI summaries")
def digests(limit: int = Query(30, ge=1, le=100), session: Session = Depends(get_session)):
    windows = session.execute(select(DigestWindow).order_by(DigestWindow.created_at.desc()).limit(limit)).scalars().all()
    masters = {j.id: j for j in session.execute(
        select(Job).where(Job.id.in_([w.master_job_id for w in windows]))).scalars()}
    out = []
    for w in windows:
        meta = (masters.get(w.master_job_id).digest_metadata if masters.get(w.master_job_id) else None) or {}
        out.append(DigestOut(
            id=w.id, subscriberId=w.subscriber_id, workflowId=w.workflow_id, status=w.status,
            eventCount=w.event_count, windowStart=w.created_at, windowEnd=w.window_ends_at,
            masterJobId=w.master_job_id, summary=meta.get("summary"),
            events=meta.get("events") or collect_events(session, w.id)))
    return out


@router.get("/jobs", response_model=List[JobOut], summary="Jobs, optionally filtered by status")
def jobs(status: str | None = None, limit: int = Query(100, ge=1, le=500), session: Session = Depends(get_session)):
    q = select(Job).order_by(Job.updated_at.desc()).limit(limit)
    if status:
        q = q.where(Job.status == status.upper())
    return [JobOut(id=j.id, stepType=j.step_type, status=j.status, skipReason=j.skip_reason, attempts=j.attempts,
                   maxAttempts=j.max_attempts, delayUntil=j.delay_until, error=j.error)
            for j in session.execute(q).scalars()]
