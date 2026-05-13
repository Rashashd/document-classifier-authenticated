"""RQ adapter — the only module that imports rq.

Payload contract for ``enqueue_job``::

    {"func": "app.workers.inference.run", "kwargs": {...}}

``func`` is the dotted import path resolved by the worker process.
``kwargs`` is optional and forwarded verbatim to that callable.
"""

from __future__ import annotations

import structlog
from typing import Any

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from rq import Queue

from app.infra.exceptions import QueueUnavailableError


logger = structlog.get_logger(__name__)


class RQClient:
    """RQ wrapper with per-name Queue memoisation.

    The underlying ``redis.Redis`` connection pool is thread-safe but
    NOT fork-safe — child processes must build their own client.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url:  str            = redis_url
        self._connection                 = Redis.from_url(redis_url)
        self._queues:    dict[str, Queue] = {}

    def _get_queue(self, queue_name: str) -> Queue:
        queue = self._queues.get(queue_name)
        if queue is None:
            queue = Queue(name=queue_name, connection=self._connection)
            self._queues[queue_name] = queue
        return queue

    def enqueue_job(self, queue_name: str, payload: dict[str, Any]) -> str:
        """Enqueue and return the job id.

        Raises
        ------
        ValueError
            ``payload`` is missing the mandatory ``func`` key.
        QueueUnavailableError
            Redis is unreachable; the job did not enqueue.
        """
        func_path: str | None = payload.get("func")
        if not func_path:
            raise ValueError(
                "RQClient.enqueue_job: payload missing required 'func' key; "
                "got keys: " + ", ".join(payload.keys())
            )

        kwargs: dict[str, Any] = payload.get("kwargs", {}) or {}
        queue = self._get_queue(queue_name)

        try:
            rq_job = queue.enqueue(func_path, **kwargs)
            # RQ 2.x: ``Job.id`` (str property), not the removed ``get_id()``.
            job_id = rq_job.id
        except RedisConnectionError as exc:
            logger.exception(
                "rq: redis connection failed on queue=%r func=%r",
                queue_name, func_path,
            )
            raise QueueUnavailableError(
                f"could not enqueue {func_path!r} on {queue_name!r}: {exc}"
            ) from exc

        logger.info(
            "rq: enqueued job %s on queue=%r func=%r",
            job_id, queue_name, func_path,
        )
        return job_id

    def __repr__(self) -> str:  # pragma: no cover
        # Strip any embedded credential before logging.
        safe = self._redis_url.split("@")[-1] if "@" in self._redis_url else self._redis_url
        return f"RQClient(redis={safe!r}, queues={list(self._queues)!r})"
