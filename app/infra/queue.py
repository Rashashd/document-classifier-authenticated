"""
Redis Queue (RQ) adapter.

This is the single point in the codebase that imports the ``rq`` package.
Everywhere else — services, workers, sftp-ingest — talks to RQ through
this thin wrapper. That keeps two architectural promises:

  1. **Infra is replaceable.** If we ever swap RQ for Celery / Arq /
     Dramatiq, we change this file only.
  2. **No business logic.** Enqueueing is a transport concern; *what*
     gets enqueued (the :class:`app.domain.jobs.InferenceJob` payload)
     lives in the domain layer.

Payload contract
----------------
Callers pass a dict that is treated as a thin envelope::

    {
        "func":   "app.workers.inference.run",   # dotted import path
        "kwargs": { ... }                        # forwarded to the func
    }

The ``func`` key is mandatory — the adapter is deliberately ignorant of
which functions exist; resolution happens inside the RQ worker process
via standard Python import.  ``kwargs`` is optional (defaults to ``{}``).

This shape composes cleanly with
:meth:`app.domain.jobs.InferenceJob.to_rq_kwargs`, which already returns
``{"kwargs": {"payload": <json>}}`` — the caller just adds ``"func"``.
"""

from __future__ import annotations

import logging
from typing import Any

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from rq import Queue

from app.infra.exceptions import QueueUnavailableError


logger = logging.getLogger(__name__)


class RQClient:
    """Wraps a single Redis connection and an on-demand ``Queue`` cache.

    A new ``rq.Queue`` instance is cheap, but we still cache them per
    queue name so we don't re-create the keyspace probes on every
    enqueue. The cache is process-local — sharing across processes is
    neither needed nor safe (Redis connections aren't fork-safe).
    """

    def __init__(self, redis_url: str) -> None:
        """
        Parameters
        ----------
        redis_url
            A strict Redis connection URL — for example
            ``"redis://redis:6379/0"`` inside compose, or
            ``"redis://localhost:6379/0"`` against a local Redis.

            We use logical DB 0 for the queue and reserve DB 1 for the
            fastapi-cache2 cache; the caller decides which DB by
            choosing the URL suffix. That keeps cache flushes from
            wiping in-flight jobs.
        """
        # We construct the Redis client via the URL parser so callers
        # can encode auth, TLS, db, and timeouts in a single string.
        # ``from_url`` returns a connection pool under the hood — safe
        # to reuse across threads, *not* across forks (workers must
        # build their own).
        self._redis_url: str = redis_url
        self._connection     = Redis.from_url(redis_url)

        # Lazy cache of queue_name -> rq.Queue. We avoid declaring all
        # queues up-front because the set is open: new workers can
        # register new queue names without redeploying the adapter.
        self._queues: dict[str, Queue] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _get_queue(self, queue_name: str) -> Queue:
        """Return (and memoise) an ``rq.Queue`` for ``queue_name``."""
        queue = self._queues.get(queue_name)
        if queue is None:
            # ``Queue`` does not connect on construction; it only does so
            # on the first command. So this is cheap.
            queue = Queue(name=queue_name, connection=self._connection)
            self._queues[queue_name] = queue
        return queue

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def enqueue_job(self, queue_name: str, payload: dict[str, Any]) -> str:
        """Enqueue a job onto ``queue_name``.

        Parameters
        ----------
        queue_name
            Logical queue name, e.g. ``"inference"``. Workers consume
            queues by name; mismatched names mean silently-dropped jobs,
            so the source of truth for queue names is
            ``app/core/settings.py`` (Person A) — callers should import
            from there, not pass string literals around.
        payload
            Envelope dict, see module docstring. Must contain a ``func``
            key with a dotted import path. ``kwargs`` is optional.

        Returns
        -------
        str
            The RQ-generated job ID (a UUID-ish string). Persist it on
            the batch/prediction row if you need to look the job up
            later (e.g. for status polling or cancellation).

        Raises
        ------
        ValueError
            If ``payload`` is missing the mandatory ``func`` key. We
            raise *before* hitting Redis so callers get a clean
            programming-error signal rather than a delayed worker-side
            ImportError.
        QueueUnavailableError
            If the underlying Redis connection fails. The service layer
            decides whether to retry or surface to the user
            (CLAUDE.md §10.5).
        """
        # Pull the function path out of the envelope. We use ``.get``
        # rather than ``payload["func"]`` so we can emit a more useful
        # error message than ``KeyError: 'func'``.
        func_path: str | None = payload.get("func")
        if not func_path:
            raise ValueError(
                "RQClient.enqueue_job: payload is missing required "
                "'func' key (a dotted import path to the worker "
                "callable). Got keys: " + ", ".join(payload.keys())
            )

        # ``kwargs`` is the only piece the worker actually needs at
        # call time. Defaulting to an empty dict keeps simple zero-arg
        # jobs ergonomic for callers.
        kwargs: dict[str, Any] = payload.get("kwargs", {}) or {}

        queue   = self._get_queue(queue_name)
        # RQ accepts a dotted string for ``f`` and will import-and-call
        # it inside the worker process. That's exactly the loose
        # coupling we want at the infra layer.
        try:
            rq_job = queue.enqueue(func_path, **kwargs)
            # ``Job.id`` is the canonical accessor in RQ 2.x; the
            # 1.x ``get_id()`` method was removed. Caught by the
            # integration test suite (see reports/infra-adapters.md).
            job_id = rq_job.id
        except RedisConnectionError as exc:
            # logger.exception() preserves the traceback in the log;
            # the typed error preserves it in the exception chain for
            # programmatic callers (CLAUDE.md §10.2, §10.3, §10.4).
            logger.exception(
                "rq: redis connection failed while enqueueing on "
                "queue=%r func=%r",
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

    # ------------------------------------------------------------------
    # Debug
    # ------------------------------------------------------------------
    def __repr__(self) -> str:  # pragma: no cover — trivial
        # The URL may contain a password; for debug logs we strip it.
        # Anyone debugging connectivity is looking for the host:port
        # not the secret.
        safe = self._redis_url.split("@")[-1] if "@" in self._redis_url else self._redis_url
        return f"RQClient(redis={safe!r}, queues={list(self._queues)!r})"
