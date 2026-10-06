from typing import Optional
from fastapi import APIRouter, Depends, Header, Response, status
from app.core.auth import verify_api_key
from app.schemas.event import (
    EventTriggerRequest,
    EventTriggerResponse,
    BulkEventTriggerRequest,
    BulkEventTriggerResponse,
)
from app.services.event_service import event_service

router = APIRouter(prefix="/events", tags=["Events"])


@router.post(
    "/trigger",
    response_model=EventTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger Single Event",
    description="Triggers an event workflow for one or more subscribers according to API.md Section 2.1.",
)
async def trigger_event(
    request: EventTriggerRequest,
    response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    _auth: str = Depends(verify_api_key),
) -> EventTriggerResponse:
    result, status_code = await event_service.ingest_event(
        request=request,
        idempotency_key=idempotency_key,
    )
    response.status_code = status_code
    return result


@router.post(
    "/trigger-bulk",
    response_model=BulkEventTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk Trigger Events",
    description="Ingests multiple event triggers in a single batch according to API.md Section 2.2.",
)
async def trigger_bulk_events(
    request: BulkEventTriggerRequest,
    _auth: str = Depends(verify_api_key),
) -> BulkEventTriggerResponse:
    return await event_service.ingest_bulk_events(request=request)
