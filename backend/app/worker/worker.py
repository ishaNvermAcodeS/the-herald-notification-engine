import asyncio
import json
import logging
import signal
from typing import Any, Dict, List, Optional, Union

import redis.asyncio as aioredis
from app.core.config import settings
from app.services.workflow_executor import (
    InvalidWorkflowError,
    WorkflowExecutor,
    WorkflowNotFoundError,
)
from app.services.job_runner import JobRunner

logger = logging.getLogger("herald.worker")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


class MalformedJobError(Exception):
    """Raised when a queue job fails structural or type validation."""
    pass


class HeraldWorker:
    """
    Phase 3 background worker process.
    Consumes event jobs from Redis queue ('herald:queue:workflow-jobs'),
    safely validates payloads, logs successful consumption, handles malformed
    messages without crashing, and acknowledges/removes consumed jobs.
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        queue_name: str = "workflow-jobs",
        dead_letter_queue: Optional[str] = None,
        executor: Optional[WorkflowExecutor] = None,
        runner: Optional[JobRunner] = None,
    ):
        # Phase 5-9: when a runner is supplied, created jobs are executed
        # (digest, preferences, delivery, retry) and due delayed work is polled.
        self.runner = runner
        # Phase 4: when an executor is supplied, each consumed job is run through
        # workflow execution. Without one the worker keeps its Phase 3 behavior.
        self.executor = executor
        self.redis_url = redis_url or settings.REDIS_URL
        self.queue_name = queue_name
        self.queue_key = f"herald:queue:{queue_name}"
        self.dead_letter_key = (
            f"herald:queue:{dead_letter_queue}"
            if dead_letter_queue
            else f"herald:queue:{queue_name}:dead-letter"
        )
        self._redis: Optional[aioredis.Redis] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self.consumed_count = 0
        self.malformed_count = 0
        self.executed_count = 0
        self.failed_execution_count = 0

    async def get_client(self) -> aioredis.Redis:
        current_loop = asyncio.get_running_loop()
        if self._redis is None or self._loop != current_loop:
            self._redis = aioredis.from_url(
                self.redis_url,
                decode_responses=True,
            )
            self._loop = current_loop
        return self._redis

    def parse_and_validate(self, raw_message: str) -> Dict[str, Any]:
        """
        Safely deserializes and validates an incoming queue message.
        Supports both wrapped envelopes ({"id": ..., "data": {...}})
        and raw event dictionaries.
        Raises MalformedJobError on validation failure.
        """
        try:
            parsed = json.loads(raw_message)
        except (json.JSONDecodeError, TypeError) as err:
            raise MalformedJobError(f"Invalid JSON payload: {err}")

        if not isinstance(parsed, dict):
            raise MalformedJobError(f"Job payload must be a JSON object, got {type(parsed).__name__}")

        # Check for envelope
        if "data" in parsed and isinstance(parsed["data"], dict):
            job_data = parsed["data"]
            envelope_id = parsed.get("id")
        else:
            job_data = parsed
            envelope_id = None

        # Validate required fields according to Phase 1/3 event schemas
        # Required: transactionId, workflowId, to, payload
        transaction_id = job_data.get("transactionId")
        if not transaction_id or not isinstance(transaction_id, str):
            raise MalformedJobError("Missing or invalid 'transactionId' (must be non-empty string)")

        workflow_id = job_data.get("workflowId")
        if not workflow_id or not isinstance(workflow_id, str):
            raise MalformedJobError("Missing or invalid 'workflowId' (must be non-empty string)")

        to_field = job_data.get("to")
        if to_field is None:
            raise MalformedJobError("Missing 'to' field")
        if not (isinstance(to_field, str) or (isinstance(to_field, list) and len(to_field) > 0)):
            raise MalformedJobError("'to' must be a non-empty string or non-empty list of subscriber IDs")

        payload = job_data.get("payload")
        if payload is None or not isinstance(payload, dict):
            raise MalformedJobError("Missing or invalid 'payload' (must be a JSON object)")

        job_id = envelope_id or transaction_id
        return {
            "id": job_id,
            "transactionId": transaction_id,
            "workflowId": workflow_id,
            "to": to_field,
            "payload": payload,
            "raw": job_data,
        }

    async def process_one(self, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        """
        Pulls and processes a single job from the Redis queue.
        Returns the validated job dictionary if successful,
        or None if timeout reached or job was malformed.
        Never raises exceptions on malformed jobs.
        """
        client = await self.get_client()
        try:
            result = await client.blpop(self.queue_key, timeout=timeout)
        except Exception as err:
            logger.error(f"Redis BLPOP error on {self.queue_key}: {err}")
            await asyncio.sleep(0.5)
            return None

        if result is None:
            return None

        _, raw_item = result

        try:
            job = self.parse_and_validate(raw_item)
            self.consumed_count += 1
            logger.info(
                f"Successfully consumed event job [id={job['id']}, "
                f"workflow={job['workflowId']}, tx={job['transactionId']}]"
            )
            if self.executor is not None and not await self._execute(client, job, raw_item):
                return None
            return job
        except MalformedJobError as err:
            self.malformed_count += 1
            logger.warning(
                f"Malformed message encountered on {self.queue_key}: {err}. "
                f"Moving to dead-letter queue ({self.dead_letter_key})."
            )
            try:
                await client.rpush(self.dead_letter_key, raw_item)
            except Exception as dl_err:
                logger.error(f"Failed to push to dead-letter queue: {dl_err}")
            return None
        except Exception as err:
            self.malformed_count += 1
            logger.error(f"Unexpected error processing message: {err}", exc_info=True)
            try:
                await client.rpush(self.dead_letter_key, raw_item)
            except Exception:
                pass
            return None

    async def _execute(self, client: aioredis.Redis, job: Dict[str, Any], raw_item: str) -> bool:
        """
        Runs workflow execution for a validated job. Unknown/invalid workflows
        (and unexpected failures) are routed to the dead-letter queue, matching
        the Phase 3 convention. Retry semantics are a later phase.
        """
        try:
            result = await asyncio.to_thread(self.executor.execute, job)
            self.executed_count += 1
            if self.runner is not None:
                for notification_id in result.notification_ids:
                    await asyncio.to_thread(self.runner.run_notification, notification_id)
            return True
        except (WorkflowNotFoundError, InvalidWorkflowError) as err:
            logger.warning(
                f"Workflow execution rejected [id={job['id']}, workflow={job['workflowId']}]: {err}. "
                f"Moving to dead-letter queue ({self.dead_letter_key})."
            )
        except Exception as err:
            logger.error(
                f"Workflow execution failed [id={job['id']}, workflow={job['workflowId']}]: {err}",
                exc_info=True,
            )
        self.failed_execution_count += 1
        try:
            await client.rpush(self.dead_letter_key, raw_item)
        except Exception as dl_err:
            logger.error(f"Failed to push to dead-letter queue: {dl_err}")
        return False

    async def start(self) -> None:
        """Starts the worker consumption loop."""
        self._running = True
        client = await self.get_client()
        logger.info(
            f"Herald Worker started [queue={self.queue_key}, "
            f"dead_letter={self.dead_letter_key}, redis={self.redis_url}]"
        )

        while self._running:
            try:
                await self.process_one(timeout=1.0)
                if self.runner is not None:
                    await asyncio.to_thread(self.runner.process_due)
            except asyncio.CancelledError:
                break
            except Exception as err:
                logger.error(f"Worker loop error: {err}", exc_info=True)
                await asyncio.sleep(0.5)

        logger.info(
            f"Herald Worker stopped. Total consumed: {self.consumed_count}, "
            f"malformed: {self.malformed_count}"
        )

    async def stop(self) -> None:
        """Signals the worker loop to stop and closes Redis connection."""
        self._running = False
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None


def setup_signal_handlers(worker: HeraldWorker, loop: asyncio.AbstractEventLoop):
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(worker.stop()))
        except NotImplementedError:
            # Fallback for systems where add_signal_handler is not implemented
            pass


async def run_worker():
    worker = HeraldWorker(executor=WorkflowExecutor(), runner=JobRunner())
    loop = asyncio.get_running_loop()
    setup_signal_handlers(worker, loop)
    await worker.start()


if __name__ == "__main__":
    asyncio.run(run_worker())
