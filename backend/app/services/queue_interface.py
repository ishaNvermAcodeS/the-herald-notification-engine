from abc import ABC, abstractmethod
import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional
import uuid

import redis.asyncio as aioredis
from app.core.config import settings

logger = logging.getLogger(__name__)


class BaseQueueProducer(ABC):
    """
    Abstract interface for queue producers.
    Allows Phase 1 in-memory stubbing and seamless Phase 3 swap to Redis/BullMQ.
    """

    @abstractmethod
    async def enqueue(self, queue_name: str, job_data: Dict[str, Any]) -> str:
        """Enqueue a single event job for background orchestration."""
        pass

    @abstractmethod
    async def enqueue_batch(self, queue_name: str, jobs_data: List[Dict[str, Any]]) -> List[str]:
        """Enqueue a batch of event jobs."""
        pass


class InMemoryQueueProducer(BaseQueueProducer):
    """
    Phase 1 isolated in-memory queue stub.
    Holds enqueued jobs in an in-memory buffer without faking background execution.
    """

    def __init__(self):
        self._enqueued_jobs: List[Dict[str, Any]] = []

    async def enqueue(self, queue_name: str, job_data: Dict[str, Any]) -> str:
        job_id = job_data.get("transactionId", f"job-{len(self._enqueued_jobs) + 1}")
        self._enqueued_jobs.append({"queue": queue_name, "id": job_id, "data": job_data})
        return job_id

    async def enqueue_batch(self, queue_name: str, jobs_data: List[Dict[str, Any]]) -> List[str]:
        job_ids = []
        for job_data in jobs_data:
            job_id = await self.enqueue(queue_name, job_data)
            job_ids.append(job_id)
        return job_ids

    def get_enqueued_jobs(self) -> List[Dict[str, Any]]:
        return list(self._enqueued_jobs)

    def clear(self):
        self._enqueued_jobs.clear()


class RedisQueueProducer(BaseQueueProducer):
    """
    Phase 3 Redis-backed queue producer.
    Enqueues jobs to Redis lists using FIFO semantics (RPUSH/BLPOP).
    """

    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url or settings.REDIS_URL
        self._redis: Optional[aioredis.Redis] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def get_client(self) -> aioredis.Redis:
        current_loop = asyncio.get_running_loop()
        if self._redis is None or self._loop != current_loop:
            self._redis = aioredis.from_url(
                self.redis_url,
                decode_responses=True,
            )
            self._loop = current_loop
        return self._redis

    def queue_key(self, queue_name: str) -> str:
        return f"herald:queue:{queue_name}"

    async def enqueue(self, queue_name: str, job_data: Dict[str, Any]) -> str:
        client = await self.get_client()
        job_id = job_data.get("transactionId") or f"job-{uuid.uuid4().hex[:16]}"
        envelope = {
            "id": job_id,
            "queue": queue_name,
            "data": job_data,
            "enqueuedAt": time.time(),
        }
        await client.rpush(self.queue_key(queue_name), json.dumps(envelope))
        return job_id

    async def enqueue_batch(self, queue_name: str, jobs_data: List[Dict[str, Any]]) -> List[str]:
        if not jobs_data:
            return []
        client = await self.get_client()
        job_ids = []
        serialized = []
        for job_data in jobs_data:
            job_id = job_data.get("transactionId") or f"job-{uuid.uuid4().hex[:16]}"
            job_ids.append(job_id)
            envelope = {
                "id": job_id,
                "queue": queue_name,
                "data": job_data,
                "enqueuedAt": time.time(),
            }
            serialized.append(json.dumps(envelope))
        await client.rpush(self.queue_key(queue_name), *serialized)
        return job_ids

    async def count(self, queue_name: str) -> int:
        client = await self.get_client()
        return await client.llen(self.queue_key(queue_name))

    async def clear(self, queue_name: str) -> None:
        client = await self.get_client()
        await client.delete(self.queue_key(queue_name))

    async def peek(self, queue_name: str, start: int = 0, stop: int = -1) -> List[Dict[str, Any]]:
        client = await self.get_client()
        items = await client.lrange(self.queue_key(queue_name), start, stop)
        result = []
        for item in items:
            try:
                result.append(json.loads(item))
            except Exception:
                result.append({"raw": item})
        return result

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
