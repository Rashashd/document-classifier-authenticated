"""
Typed exceptions for the ``app/infra/`` layer.

Per CLAUDE.md §10.4, infra adapters translate external-SDK exceptions
(``S3Error``, ``paramiko.SSHException``, ``redis.exceptions.ConnectionError``,
``urllib3.exceptions.MaxRetryError``, ``OSError``, …) into a small,
stable vocabulary that higher layers can pattern-match on.

Why one shared module
---------------------
* Callers in ``app/services/`` import from one place — they never need
  to know which SDK any adapter happens to use.
* Service-layer retry / fail-over policy can ``except QueueUnavailableError``
  without dragging ``redis-py`` into the service layer's import graph.
* Swapping an adapter's underlying SDK (e.g. RQ → Arq) becomes a
  one-file change: the exception types stay constant.

Keep each subclass empty unless you actually need extra structured
attributes — the *type* is the contract, not the message.
"""

from __future__ import annotations


class InfraError(Exception):
    """Base for every error raised out of ``app/infra/`` adapters.

    Callers can ``except InfraError`` to handle "any external system
    misbehaved" uniformly, while still being able to pattern-match on
    the specific subclasses below for finer-grained policy.
    """


class BlobUnavailableError(InfraError):
    """MinIO / S3 backend is unreachable (network / connection level).

    Distinct from ``minio.error.S3Error``, which represents a per-request
    server-side error (e.g. 403, 404, NoSuchBucket). S3Error propagates
    unchanged from the adapter so callers can introspect ``.code``;
    BlobUnavailableError signals "the storage tier itself is down".
    """


class QueueUnavailableError(InfraError):
    """RQ / Redis queue backend is unreachable.

    Raised by :class:`app.infra.queue.RQClient` when the underlying
    Redis connection fails during enqueue. The job ID does not exist.
    The caller decides whether to retry or surface to the user.
    """


class CacheUnavailableError(InfraError):
    """fastapi-cache2 Redis backend is unreachable.

    Raised by :func:`app.infra.cache.init_redis_cache` when the startup
    ping fails. The API's startup event should propagate this so the
    container refuses to boot (CLAUDE.md §10.6).
    """


class SFTPConnectError(InfraError):
    """SFTP session could not be established.

    Covers both network-level failures (DNS, connection refused) and
    SSH-level failures (auth refused, host-key mismatch). To distinguish
    them, inspect ``exc.__cause__``:

        try:
            client.connect()
        except SFTPConnectError as exc:
            if isinstance(exc.__cause__, paramiko.AuthenticationException):
                ...  # permanent — never retry
            ...      # otherwise retryable
    """
