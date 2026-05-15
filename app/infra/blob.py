"""MinIO blob-storage adapter. Pure SDK wrapper, no business logic."""

from __future__ import annotations

import io
import structlog
from typing import BinaryIO

from minio import Minio
from minio.error import S3Error
from urllib3.exceptions import MaxRetryError

from app.infra.exceptions import BlobUnavailableError


logger = structlog.get_logger(__name__)


class MinioBlobClient:
    """S3-compatible blob client. One instance per process."""

    DEFAULT_BUCKET = "documents"

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        *,
        secure: bool = False,
    ) -> None:
        self._endpoint = endpoint
        self._client = Minio(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )

    def startup(self, bucket: str = DEFAULT_BUCKET) -> None:
        """Ensure ``bucket`` exists. Idempotent."""
        if self._client.bucket_exists(bucket):
            return
        try:
            self._client.make_bucket(bucket)
        except S3Error as exc:
            if exc.code == "BucketAlreadyOwnedByYou":
                return
            raise

    def upload_file(
        self,
        file_name: str,
        file_stream: bytes,
        *,
        bucket: str = DEFAULT_BUCKET,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload bytes and return the ``s3://`` URI.

        S3Error (per-request server errors) propagates unchanged.
        MaxRetryError (MinIO unreachable) → BlobUnavailableError.
        """
        buffer: BinaryIO = io.BytesIO(file_stream)
        length = len(file_stream)

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
                "minio: connection failed uploading %r to %r", file_name, bucket
            )
            raise BlobUnavailableError(
                f"MinIO upload to s3://{bucket}/{file_name} failed: {exc}"
            ) from exc

        return f"s3://{bucket}/{file_name}"

    def download_file(self, file_name: str, *, bucket: str = DEFAULT_BUCKET) -> bytes:
        """Read an object's full bytes. Symmetric with ``upload_file``.

        S3Error propagates (per-request errors like NoSuchKey).
        MaxRetryError (MinIO unreachable) → BlobUnavailableError.
        """
        try:
            response = self._client.get_object(bucket, file_name)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except MaxRetryError as exc:
            logger.exception(
                "minio: connection failed downloading %r from %r", file_name, bucket
            )
            raise BlobUnavailableError(
                f"MinIO download from s3://{bucket}/{file_name} failed: {exc}"
            ) from exc

    def __repr__(self) -> str:  # pragma: no cover
        return f"MinioBlobClient(endpoint={self._endpoint!r})"
