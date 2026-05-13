"""Typed exceptions for app/infra/ adapters."""

from __future__ import annotations


class InfraError(Exception):
    """Base for failures raised out of app/infra/ adapters."""


class BlobUnavailableError(InfraError):
    """MinIO is unreachable. Distinct from minio.error.S3Error
    (per-request server errors like 403/NoSuchBucket), which
    propagates unchanged so callers can inspect ``.code``."""


class QueueUnavailableError(InfraError):
    """Redis queue backend is unreachable; the job did not enqueue."""


class CacheUnavailableError(InfraError):
    """Cache Redis is unreachable at startup. Raise to refuse-to-start."""


class SFTPConnectError(InfraError):
    """SFTP session could not be established. Inspect ``__cause__`` to
    distinguish auth (permanent) from network (retryable)."""
