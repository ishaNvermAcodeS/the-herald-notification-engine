from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


class PreferencesUpdate(BaseModel):
    email: Optional[bool] = None
    in_app: Optional[bool] = None

    @model_validator(mode="after")
    def at_least_one(self):
        if self.email is None and self.in_app is None:
            raise ValueError("Provide at least one of 'email' or 'in_app'")
        return self


class PreferencesOut(BaseModel):
    subscriberId: str
    channels: Dict[str, bool]


class SubscriberUpdate(BaseModel):
    email: Optional[str] = Field(default=None, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: Optional[str] = Field(default=None, max_length=255)


class SubscriberOut(BaseModel):
    subscriberId: str
    email: Optional[str]
    name: Optional[str]
    channels: Dict[str, bool]


class InboxItem(BaseModel):
    id: str
    notificationId: str
    workflowId: str
    title: Optional[str]
    body: str
    read: bool
    seen: bool
    status: str
    createdAt: datetime
    aiSummary: Optional[Dict[str, Any]] = None


class InboxOut(BaseModel):
    subscriberId: str
    unreadCount: int
    items: List[InboxItem]


class JobOut(BaseModel):
    id: str
    stepType: str
    status: str
    skipReason: Optional[str]
    attempts: int
    maxAttempts: int
    delayUntil: Optional[datetime]
    error: Optional[str]


class MessageOut(BaseModel):
    id: str
    jobId: str
    channel: str
    status: str
    providerMessageId: Optional[str]
    deliveredAt: Optional[datetime]


class AdminNotificationOut(BaseModel):
    id: str
    transactionId: str
    subscriberId: str
    workflowId: str
    status: str
    payload: Dict[str, Any]
    digestWindowId: Optional[str]
    createdAt: datetime
    jobs: List[JobOut]
    messages: List[MessageOut]


class DigestOut(BaseModel):
    id: str
    subscriberId: str
    workflowId: str
    status: str
    eventCount: int
    windowStart: datetime
    windowEnd: datetime
    masterJobId: str
    summary: Optional[Dict[str, Any]] = None
    events: List[Dict[str, Any]] = []
