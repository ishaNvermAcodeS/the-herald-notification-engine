"""
SQLAlchemy ORM models for The Herald.

Entities strictly derived from DATA_MODEL.md:
  - Subscriber
  - Preference
  - Workflow
  - DigestWindow
  - Notification
  - Job
  - Message
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ── Helpers ────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# ── Subscriber ────────────────────────────────────────────────────────────────
class Subscriber(Base):
    """
    DATA_MODEL.md §3.1
    Represents a campus notification recipient (student / faculty / staff).
    """
    __tablename__ = "subscribers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subscriber_id: Mapped[str] = mapped_column(
        "subscriberId", String(255), unique=True, nullable=False, index=True
    )
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        "updatedAt", DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    # Relationships
    preferences: Mapped[list["Preference"]] = relationship(
        "Preference", back_populates="subscriber", cascade="all, delete-orphan"
    )
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification", back_populates="subscriber", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Subscriber subscriberId={self.subscriber_id!r}>"


# ── Preference ────────────────────────────────────────────────────────────────
class Preference(Base):
    """
    DATA_MODEL.md §3.2
    Per-subscriber channel enablement settings.
    Directly drives Killer Test 2 (muted email → in-app only).
    """
    __tablename__ = "preferences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subscriber_id: Mapped[str] = mapped_column(
        "subscriberId",
        String(255),
        ForeignKey("subscribers.subscriberId", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workflow_id: Mapped[str | None] = mapped_column(
        "workflowId",
        String(255),
        ForeignKey("workflows.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # channels: {"email": bool, "in_app": bool}
    channels: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        "updatedAt", DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    # Unique: one global pref per subscriber (workflowId NULL) OR one per workflow
    __table_args__ = (
        UniqueConstraint(
            "subscriberId", "workflowId", name="uq_preference_subscriber_workflow"
        ),
        Index("ix_preferences_subscriber_workflow", "subscriberId", "workflowId"),
    )

    # Relationships
    subscriber: Mapped["Subscriber"] = relationship("Subscriber", back_populates="preferences")
    workflow: Mapped["Workflow | None"] = relationship("Workflow", back_populates="preferences")

    def __repr__(self) -> str:
        return f"<Preference subscriberId={self.subscriber_id!r} workflowId={self.workflow_id!r}>"


# ── Workflow ──────────────────────────────────────────────────────────────────
class Workflow(Base):
    """
    DATA_MODEL.md §3.3
    Blueprint defining notification orchestration steps and metadata.
    """
    __tablename__ = "workflows"

    # PK is the human-readable workflow slug (e.g. "campus-maintenance-alert")
    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_critical: Mapped[bool] = mapped_column(
        "isCritical", Boolean, nullable=False, default=False
    )
    # steps: [{"type": "DIGEST", "durationMs": 300000}, {"type": "EMAIL"}, ...]
    steps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True), nullable=False, default=_now
    )

    # Relationships
    preferences: Mapped[list["Preference"]] = relationship(
        "Preference", back_populates="workflow"
    )
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification", back_populates="workflow"
    )

    def __repr__(self) -> str:
        return f"<Workflow id={self.id!r} name={self.name!r}>"


# ── DigestWindow ──────────────────────────────────────────────────────────────
class DigestWindow(Base):
    """
    DATA_MODEL.md §3.4
    Manages active atomic digest grouping state per subscriber and workflow.
    Supports Killer Test 1 and the Atomic Digest Fix (Phase 7).
    The partial unique index on (subscriberId, workflowId, digestKey) WHERE status='OPEN'
    enforces strictly one open window per subscriber+workflow combination.
    """
    __tablename__ = "digest_windows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subscriber_id: Mapped[str] = mapped_column(
        "subscriberId", String(255), nullable=False, index=True
    )
    workflow_id: Mapped[str] = mapped_column(
        "workflowId", String(255), nullable=False, index=True
    )
    digest_key: Mapped[str | None] = mapped_column("digestKey", String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("OPEN", "EXPIRED", "DISPATCHED", name="digestwindow_status"),
        nullable=False,
        default="OPEN",
    )
    master_job_id: Mapped[str] = mapped_column(
        "masterJobId", String(36), nullable=False
    )
    window_ends_at: Mapped[datetime] = mapped_column(
        "windowEndsAt", DateTime(timezone=True), nullable=False, index=True
    )
    event_count: Mapped[int] = mapped_column(
        "eventCount", Integer, nullable=False, default=1
    )
    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        # Partial unique index: only one OPEN window per (subscriber, workflow, digestKey)
        # This is the cornerstone of the Phase 7 Atomic Digest Fix
        Index(
            "ix_digest_window_open_unique",
            "subscriberId",
            "workflowId",
            "digestKey",
            unique=True,
            postgresql_where="status = 'OPEN'",
            postgresql_nulls_not_distinct=True,
        ),
    )

    # Relationships
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification", back_populates="digest_window"
    )

    def __repr__(self) -> str:
        return (
            f"<DigestWindow id={self.id!r} status={self.status!r} "
            f"subscriberId={self.subscriber_id!r}>"
        )


# ── Notification ──────────────────────────────────────────────────────────────
class Notification(Base):
    """
    DATA_MODEL.md §3.5
    Records each distinct incoming event trigger instance.
    """
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    transaction_id: Mapped[str] = mapped_column(
        "transactionId", String(128), unique=True, nullable=False, index=True
    )
    subscriber_id: Mapped[str] = mapped_column(
        "subscriberId",
        String(255),
        ForeignKey("subscribers.subscriberId", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workflow_id: Mapped[str] = mapped_column(
        "workflowId",
        String(255),
        ForeignKey("workflows.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(
            "PENDING", "PROCESSING", "MERGED", "COMPLETED", "FAILED",
            name="notification_status",
        ),
        nullable=False,
        default="PENDING",
    )
    digest_window_id: Mapped[str | None] = mapped_column(
        "digestWindowId",
        String(36),
        ForeignKey("digest_windows.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True), nullable=False, default=_now
    )

    # Relationships
    subscriber: Mapped["Subscriber"] = relationship(
        "Subscriber", back_populates="notifications"
    )
    workflow: Mapped["Workflow"] = relationship(
        "Workflow", back_populates="notifications"
    )
    digest_window: Mapped["DigestWindow | None"] = relationship(
        "DigestWindow", back_populates="notifications"
    )
    jobs: Mapped[list["Job"]] = relationship(
        "Job", back_populates="notification", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Notification id={self.id!r} transactionId={self.transaction_id!r}>"


# ── Job ───────────────────────────────────────────────────────────────────────
class Job(Base):
    """
    DATA_MODEL.md §3.6
    Atomic execution unit for each workflow step.
    """
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    notification_id: Mapped[str] = mapped_column(
        "notificationId",
        String(36),
        ForeignKey("notifications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_job_id: Mapped[str | None] = mapped_column(
        "parentJobId",
        String(36),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    subscriber_id: Mapped[str] = mapped_column(
        "subscriberId", String(255), nullable=False, index=True
    )
    step_type: Mapped[str] = mapped_column(
        "stepType",
        Enum("DIGEST", "EMAIL", "IN_APP", name="job_step_type"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Enum(
            "PENDING", "DELAYED", "RUNNING", "COMPLETED", "FAILED", "SKIPPED", "MERGED",
            name="job_status",
        ),
        nullable=False,
        default="PENDING",
        index=True,
    )
    skip_reason: Mapped[str | None] = mapped_column(
        "skipReason",
        Enum("SUBSCRIBER_PREFERENCE", "CONDITIONS_UNMET", name="job_skip_reason"),
        nullable=True,
    )
    attempts: Mapped[int] = mapped_column("attempts", Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column("maxAttempts", Integer, nullable=False, default=3)
    delay_until: Mapped[datetime | None] = mapped_column(
        "delayUntil", DateTime(timezone=True), nullable=True, index=True
    )
    error: Mapped[str | None] = mapped_column("error", Text, nullable=True)
    # Stores aggregated events array + AI summary when digest completes
    digest_metadata: Mapped[dict | None] = mapped_column("digestMetadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        "updatedAt", DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    # Relationships
    notification: Mapped["Notification"] = relationship(
        "Notification", back_populates="jobs"
    )
    parent_job: Mapped["Job | None"] = relationship(
        "Job", remote_side="Job.id", foreign_keys=[parent_job_id]
    )
    message: Mapped["Message | None"] = relationship(
        "Message", back_populates="job", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Job id={self.id!r} stepType={self.step_type!r} status={self.status!r}>"


# ── Message ───────────────────────────────────────────────────────────────────
class Message(Base):
    """
    DATA_MODEL.md §3.7
    Represents the actual communication artifact delivered to a channel.
    Directly drives Killer Test 3 (retry without duplicates) and in-app feeds.
    """
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(
        "jobId",
        String(36),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subscriber_id: Mapped[str] = mapped_column(
        "subscriberId", String(255), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(
        Enum("EMAIL", "IN_APP", name="message_channel"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Enum("PENDING", "SENT", "FAILED", name="message_status"),
        nullable=False,
        default="PENDING",
        index=True,
    )
    subject: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Structured AI briefing for digested notifications (Phase 9 differentiator)
    ai_summary: Mapped[dict | None] = mapped_column("aiSummary", JSONB, nullable=True)
    # Deterministic dedup key: hash(subscriberId, workflowId, stepType, transactionId)
    deduplication_key: Mapped[str] = mapped_column(
        "deduplicationKey", String(512), unique=True, nullable=False
    )
    provider_message_id: Mapped[str | None] = mapped_column(
        "providerMessageId", String(512), nullable=True
    )
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    seen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        "createdAt", DateTime(timezone=True), nullable=False, default=_now, index=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        "deliveredAt", DateTime(timezone=True), nullable=True
    )

    # Relationships
    job: Mapped["Job"] = relationship("Job", back_populates="message")

    def __repr__(self) -> str:
        return f"<Message id={self.id!r} channel={self.channel!r} status={self.status!r}>"
