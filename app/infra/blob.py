"""
MinIO blob-storage adapter.

This module lives in ``app/infra/`` and therefore obeys two non-negotiable
rules from the project architecture:

  1. **No business logic.** This class is a pure wrapper around the
     official ``minio`` SDK. It does not decide *what* to upload or
     *why* — that lives in the service layer.
  2. **No SQLAlchemy.** Infra adapters never touch the database. They
     speak HTTP/S3 only.

The class is consumed by:

  * the sftp-ingest worker, which uploads raw scans
  * the inference worker, which writes annotated PNG overlays
  * (read paths only) the API, when generating presigned download URLs

Callers receive the object key as a return value and should persist that
key in their DB row; nothing here is authoritative — MinIO is the
source of truth for blob bytes, Postgres for blob *metadata*.
"""

from __future__ import annotations

import io
import logging
from typing import BinaryIO

from minio import Minio
from minio.error import S3Error
from urllib3.exceptions import MaxRetryError

from app.infra.exceptions import BlobUnavailableError


logger = logging.getLogger(__name__)


class MinioBlobClient:
    """Thin wrapper around :class:`minio.Minio`.

    Initialised once at process startup (one instance per container is
    fine — the underlying urllib3 pool is thread-safe).
    """

    # Default bucket. Centralised here so `startup()` and `upload_file()`
    # cannot disagree on which bucket they reference. The service layer
    # may pass a different bucket per call if a future use case requires
    # it, but the *default* must stay in lockstep.
    DEFAULT_BUCKET = "documents"

    def __init__(
        self,
        endpoint:   str,
        access_key: str,
        secret_key: str,
        *,
        secure: bool = False,
    ) -> None:
        """
        Parameters
        ----------
        endpoint
            Host:port of the MinIO server, *without* a scheme.
            In docker-compose this is typically ``"minio:9000"``.
        access_key
            S3 access key — locally the MinIO root user, in production
            a Vault-resolved service account.
        secret_key
            Matching secret. Never log this; never expose via ``__repr__``.
        secure
            ``True`` only when MinIO is fronted by TLS (prod). Local
            compose is plaintext so the default is ``False``.
        """
        # We deliberately store only the endpoint string for logging;
        # the credentials are kept inside the Minio client and never
        # surfaced again from this object.
        self._endpoint = endpoint
        self._client   = Minio(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def startup(self, bucket: str = DEFAULT_BUCKET) -> None:
        """Ensure ``bucket`` exists, creating it if not.

        Idempotent — safe to call on every container boot. We deliberately
        do *not* set a bucket policy or lifecycle here; that belongs in an
        IaC layer (or, for the local stack, in the MinIO console). This
        method has one job: guarantee that subsequent ``upload_file``
        calls won't fail with NoSuchBucket.

        Raises
        ------
        S3Error
            If the bucket-exists check itself fails (network, auth, etc.).
            We re-raise so the caller's startup probe fails loudly — silent
            "I'll create the bucket on first write" semantics have bitten
            us before in similar projects.
        """
        # ``bucket_exists`` is a HEAD request; cheap, but it does require
        # the credentials to be valid, so a 403 here is a useful signal
        # that the access keys are wrong before any worker code runs.
        if self._client.bucket_exists(bucket):
            logger.info("minio: bucket %r already present", bucket)
            return

        # ``make_bucket`` is best-effort idempotent at the server level,
        # but on a race two callers could both reach this branch. We
        # therefore swallow only the specific "already owned by you"
        # error code; anything else is a real failure.
        try:
            self._client.make_bucket(bucket)
            logger.info("minio: created bucket %r", bucket)
        except S3Error as exc:
            if exc.code == "BucketAlreadyOwnedByYou":
                logger.info(
                    "minio: bucket %r created concurrently by another worker",
                    bucket,
                )
                return
            raise

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------
    def upload_file(
        self,
        file_name: str,
        file_stream: bytes,
        *,
        bucket:       str = DEFAULT_BUCKET,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload ``file_stream`` to ``bucket`` under key ``file_name``.

        Returns
        -------
        str
            The fully-qualified S3 URI of the stored object,
            e.g. ``"s3://documents/batches/abc/scan.tif"``. Callers
            persist this in the ``prediction.overlay_path`` /
            ``batch.blob_path`` columns.

        Notes
        -----
        We accept raw ``bytes`` (rather than a file-like object) because
        the upstream caller — the sftp-ingest worker — already holds the
        full payload in memory after reading it off the SFTP socket
        (per Person C's SFTPClient contract). For very large blobs we
        would switch to a streaming put_object with a known length; the
        RVL-CDIP TIFFs cap out well below memory pressure (≪10 MB each).
        """
        # Wrap the bytes in a BytesIO so we can pass a *length* to
        # ``put_object``. MinIO refuses streams with length=-1 unless
        # ``part_size`` is set, and we'd rather know-and-state the size
        # than configure multipart for files this small.
        buffer: BinaryIO = io.BytesIO(file_stream)
        length            = len(file_stream)

        # Two distinct failure modes, two distinct policies
        # (CLAUDE.md §10.4):
        #   • S3Error (server-side per-request, e.g. 403/404/NoSuchBucket)
        #     propagates unchanged — the caller introspects ``.code``
        #     and decides; we do NOT want to flatten "the bucket is
        #     missing" into "MinIO is down".
        #   • MaxRetryError (urllib3 connection-pool exhaustion ⇒ MinIO
        #     itself is unreachable) → BlobUnavailableError, so the
        #     service layer can distinguish "transient infra blip,
        #     retry" from "permanent error, surface".
        # Retry *policy* still lives above us (RQ retry config / service
        # layer) — see CLAUDE.md §10.5.
        try:
            self._client.put_object(
                bucket_name=bucket,
                object_name=file_name,
                data=buffer,
                length=length,
                content_type=content_type,
            )
        except MaxRetryError as exc:
            logger.exception(
                "minio: connection failed uploading %r to bucket %r",
                file_name, bucket,
            )
            raise BlobUnavailableError(
                f"MinIO upload to s3://{bucket}/{file_name} failed: {exc}"
            ) from exc

        # Returning an `s3://` URI (rather than a presigned HTTP URL)
        # keeps this method side-effect-free w.r.t. expiry windows.
        # Whoever needs a downloadable link should call a separate
        # `presigned_url` method (not in scope for this PR).
        uri = f"s3://{bucket}/{file_name}"
        logger.info(
            "minio: uploaded %d bytes to %s (content_type=%s)",
            length, uri, content_type,
        )
        return uri

    # ------------------------------------------------------------------
    # Debug
    # ------------------------------------------------------------------
    def __repr__(self) -> str:  # pragma: no cover — trivial
        # Intentionally elides credentials. Anyone who needs them should
        # be reading Vault, not this object.
        return f"MinioBlobClient(endpoint={self._endpoint!r})"
