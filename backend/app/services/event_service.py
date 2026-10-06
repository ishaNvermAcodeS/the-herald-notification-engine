import time
import uuid
from typing import Dict, List, Optional, Tuple
from fastapi import HTTPException, status
from app.core.config import settings
from app.schemas.event import (
    EventTriggerRequest,
    EventTriggerResponse,
    BulkEventTriggerRequest,
    BulkEventTriggerResponse,
)
from app.services.queue_interface import (
    BaseQueueProducer,
    InMemoryQueueProducer,
    RedisQueueProducer,
)


class EventIngestionService:
    """
    Handles event ingestion, schema/workflow validation, transaction identity,
    idempotency checking, and background queue dispatching.
    """

    def __init__(self, queue_producer: Optional[BaseQueueProducer] = None):
        self.queue_producer = queue_producer or RedisQueueProducer()
        # In-memory idempotency cache for Phase 1 (to be backed by Redis in Phase 3)
        # Structure: key -> {"status": str, "timestamp": float, "workflowId": str, "txId": str}
        self._idempotency_cache: Dict[str, Dict] = {}

    def _validate_workflow(self, workflow_id: str) -> None:
        if workflow_id in settings.REGISTERED_WORKFLOWS:
            return
        try:
            from app.db.session import SyncSessionLocal
            from app.db.repositories.workflow_repo import workflow_exists_sync
            with SyncSessionLocal() as session:
                if workflow_exists_sync(session, workflow_id):
                    return
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow '{workflow_id}' does not exist.",
        )

    async def ingest_event(
        self,
        request: EventTriggerRequest,
        idempotency_key: Optional[str] = None,
    ) -> Tuple[EventTriggerResponse, int]:
        """
        Ingests a single event trigger according to API.md Section 2.1.
        Returns a tuple of (EventTriggerResponse, HTTP_STATUS_CODE).
        """
        self._validate_workflow(request.workflowId)

        # Resolve transaction ID
        transaction_id = request.transactionId or f"tx-{uuid.uuid4().hex[:16]}"
        idempotency_lookup_key = idempotency_key or request.transactionId

        # Check idempotency contract
        if idempotency_lookup_key and idempotency_lookup_key in self._idempotency_cache:
            record = self._idempotency_cache[idempotency_lookup_key]
            current_status = record.get("status", "processed")

            if current_status == "in-progress":
                return (
                    EventTriggerResponse(
                        acknowledged=True,
                        status="in-progress",
                        transactionId=record.get("txId", transaction_id),
                        workflowId=record.get("workflowId", request.workflowId),
                    ),
                    status.HTTP_202_ACCEPTED,
                )
            else:
                # Previous trigger completed / processed
                return (
                    EventTriggerResponse(
                        acknowledged=True,
                        status="duplicate_ignored",
                        transactionId=record.get("txId", transaction_id),
                        workflowId=record.get("workflowId", request.workflowId),
                    ),
                    status.HTTP_200_OK,
                )

        # Record initial in-progress state for idempotency key
        if idempotency_lookup_key:
            self._idempotency_cache[idempotency_lookup_key] = {
                "status": "in-progress",
                "timestamp": time.time(),
                "workflowId": request.workflowId,
                "txId": transaction_id,
            }

        # Hand off to queue producer for asynchronous orchestration
        job_payload = {
            "transactionId": transaction_id,
            "workflowId": request.workflowId,
            "to": request.to,
            "payload": request.payload,
            "timestamp": time.time(),
        }
        await self.queue_producer.enqueue("workflow-jobs", job_payload)

        # Mark processed in idempotency cache
        if idempotency_lookup_key:
            self._idempotency_cache[idempotency_lookup_key]["status"] = "processed"

        return (
            EventTriggerResponse(
                acknowledged=True,
                status="processed",
                transactionId=transaction_id,
                workflowId=request.workflowId,
            ),
            status.HTTP_202_ACCEPTED,
        )

    async def ingest_bulk_events(
        self,
        request: BulkEventTriggerRequest,
    ) -> BulkEventTriggerResponse:
        """
        Ingests a batch of event triggers according to API.md Section 2.2.
        """
        # Validate all workflows before dispatching
        for event in request.events:
            self._validate_workflow(event.workflowId)

        transaction_ids: List[str] = []
        jobs_to_enqueue: List[Dict] = []

        for event in request.events:
            tx_id = event.transactionId or f"tx-{uuid.uuid4().hex[:16]}"
            transaction_ids.append(tx_id)
            jobs_to_enqueue.append(
                {
                    "transactionId": tx_id,
                    "workflowId": event.workflowId,
                    "to": event.to,
                    "payload": event.payload,
                    "timestamp": time.time(),
                }
            )

        await self.queue_producer.enqueue_batch("workflow-jobs", jobs_to_enqueue)

        return BulkEventTriggerResponse(
            acknowledged=True,
            count=len(transaction_ids),
            transactionIds=transaction_ids,
        )


# Global service singleton instance
event_service = EventIngestionService()
