"""SFTP ingestion worker.

Polls the SFTP upload directory every ``POLL_INTERVAL_SECONDS``, triages
each file (empty / oversized / wrong extension / wrong magic bytes /
valid TIFF), uploads valid TIFFs to MinIO, persists a PENDING batch row
in Postgres, and enqueues a classification job onto Redis.

Run with ``python -m app.workers.sftp_ingest``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import os.path
import posixpath
import sys
import time
import uuid

import paramiko
import structlog
from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from app.infra.blob import MinioBlobClient
from app.infra.exceptions import (
    BlobUnavailableError,
    QueueUnavailableError,
    SFTPConnectError,
)
from app.infra.queue import RQClient
from app.infra.sftp import SFTPClient
from app.infra.vault import VaultClient
from app.services.batch_service import BatchService


# -- configuration -----------------------------------------------------------

POLL_INTERVAL_SECONDS: int = 5
MAX_FILE_SIZE_BYTES: int = 50 * 1024 * 1024
REMOTE_UPLOAD_DIR: str = "/upload"
REMOTE_QUARANTINE_DIR: str = "/quarantine"
TIFF_SUFFIXES: tuple[str, ...] = (".tiff", ".tif")
TIFF_MAGIC_BYTES: tuple[bytes, ...] = (b"II*\x00", b"MM\x00*")
QUEUE_NAME: str = "classification_queue"
INFERENCE_FUNC_PATH: str = "app.workers.inference.run"


# -- logging -----------------------------------------------------------------


def configure_logging() -> None:
    """Route both structlog and stdlib logs through a JSON renderer.

    Bound context (``bind_contextvars``) carries ``request_id`` /
    ``filename`` across every log line emitted during a file's
    processing, including stdlib logs from the infra adapters.
    """
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processor=structlog.processors.JSONRenderer(),
    )

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


log = structlog.get_logger("sftp_ingest")


# -- secrets ----------------------------------------------------------------


def fetch_vault_secrets() -> tuple[dict, dict]:
    """Read SFTP and MinIO credential dicts from Vault. Exits the process on failure.

    The worker is a standalone Python process — it does NOT share the
    API's FastAPI lifespan, so it owns its own Vault bootstrap. On any
    failure (missing token, network, missing key) we log a critical
    event and exit so the orchestrator can surface the boot failure
    rather than a degraded worker silently dropping files.
    """
    addr = os.environ.get("VAULT_ADDR", "http://vault:8200")
    token = os.environ.get("VAULT_TOKEN")
    if not token:
        log.critical("vault.boot.missing_token")
        sys.exit(1)

    sftp_path = os.environ.get("VAULT_SFTP_PATH", "sftp")
    minio_path = os.environ.get("VAULT_MINIO_PATH", "minio")

    try:
        vault = VaultClient(addr=addr, token=token)
        sftp_creds = vault.get_secret(sftp_path)
        minio_creds = vault.get_secret(minio_path)
    except Exception as exc:
        log.critical(
            "vault.boot.fetch_failed",
            addr=addr,
            sftp_path=sftp_path,
            minio_path=minio_path,
            error=str(exc),
        )
        sys.exit(1)

    return sftp_creds, minio_creds


# -- factories ---------------------------------------------------------------


def build_sftp(creds: dict) -> SFTPClient:
    return SFTPClient(
        host=os.environ.get("SFTP_HOST", "sftp"),
        port=int(os.environ.get("SFTP_PORT", "22")),
        username=creds["username"],
        password=creds["password"],
    )


def build_blob(creds: dict) -> MinioBlobClient:
    return MinioBlobClient(
        endpoint=os.environ.get("MINIO_ENDPOINT", "minio:9000"),
        access_key=creds["access_key"],
        secret_key=creds["secret_key"],
        secure=False,
    )


def build_queue() -> RQClient:
    return RQClient(redis_url=os.environ.get("REDIS_URL", "redis://redis:6379/0"))


# -- triage primitives -------------------------------------------------------


def _is_tiff_extension(name: str) -> bool:
    return name.lower().endswith(TIFF_SUFFIXES)


def _is_tiff_magic(head: bytes) -> bool:
    return any(head.startswith(m) for m in TIFF_MAGIC_BYTES)


# -- database access ---------------------------------------------------------


async def _create_pending_batch(engine: AsyncEngine, sftp_path: str) -> uuid.UUID:
    """Open one transactional session, insert a PENDING batch, return its id.

    Scanner-originated batches have no JWT subject; ``owner_id`` is None.
    ``expire_on_commit=False`` keeps the returned ``id`` readable after
    commit without triggering a lazy SELECT outside the greenlet.
    """
    async with AsyncSession(engine, expire_on_commit=False) as session:
        return await BatchService(session).create_pending_batch(
            sftp_path=sftp_path,
            owner_id=None,
        )


# -- per-file processing -----------------------------------------------------


def process_one(
    raw_name: str,
    sftp: SFTPClient,
    blob: MinioBlobClient,
    queue: RQClient,
    engine: AsyncEngine,
) -> None:
    """Triage one entry off the SFTP listing. Idempotent on safe filename."""
    # Defeat path traversal — only operate on the basename inside /upload.
    safe_name = os.path.basename(raw_name)
    if safe_name != raw_name:
        log.warning("ingest.path_traversal_attempt", raw=raw_name, safe=safe_name)
        if not safe_name:
            return

    upload_path = posixpath.join(REMOTE_UPLOAD_DIR, safe_name)

    try:
        size = sftp.size_of(upload_path)
    except FileNotFoundError:
        log.warning("ingest.file_vanished")
        return

    # Triage 1 — empty.
    if size == 0:
        sftp.delete_file(upload_path)
        log.info("ingest.empty_file_deleted", size=0)
        return

    # Triage 2 — oversized or wrong extension.
    if size > MAX_FILE_SIZE_BYTES or not _is_tiff_extension(safe_name):
        reason = "oversized" if size > MAX_FILE_SIZE_BYTES else "wrong_extension"
        sftp.delete_file(upload_path)
        log.warning("ingest.rejected", size=size, reason=reason)
        return

    # Triage 3 — magic-byte check. Anything claiming .tiff but lacking
    # the magic is treated as a security event, not a mistake.
    head = sftp.read_partial(upload_path, 4)
    if not _is_tiff_magic(head):
        quarantine_path = posixpath.join(REMOTE_QUARANTINE_DIR, safe_name)
        sftp.move_file(upload_path, quarantine_path)
        log.critical(
            "ingest.security.malicious_payload_quarantined",
            magic_hex=head.hex(),
            quarantine_path=quarantine_path,
        )
        return

    # Happy path.
    data = sftp.read_file(upload_path)
    minio_uri = blob.upload_file(safe_name, data, content_type="image/tiff")

    # Persist PENDING batch row. process_one is sync; the DB layer is
    # async; one asyncio.run per file is cheap at this poll rate.
    batch_uuid = asyncio.run(_create_pending_batch(engine, sftp_path=upload_path))
    batch_id = str(batch_uuid)
    log.info("ingest.batch_persisted", batch_id=batch_id, minio_uri=minio_uri)

    ticket: dict[str, str] = {
        "batch_id": batch_id,
        "minio_file_path": minio_uri,
    }
    job_id = queue.enqueue_job(
        queue_name=QUEUE_NAME,
        payload={
            "func": INFERENCE_FUNC_PATH,
            "kwargs": {"payload": json.dumps(ticket)},
        },
    )

    sftp.delete_file(upload_path)
    log.info(
        "ingest.success",
        batch_id=batch_id,
        minio_uri=minio_uri,
        job_id=job_id,
        bytes=size,
    )


# -- main loop ---------------------------------------------------------------


def _reconnect(sftp: SFTPClient) -> None:
    try:
        sftp.close()
    except Exception:  # noqa: BLE001
        pass
    sftp.connect()


def main() -> None:
    configure_logging()
    log.info(
        "ingest.boot",
        poll_interval=POLL_INTERVAL_SECONDS,
        upload_dir=REMOTE_UPLOAD_DIR,
        quarantine_dir=REMOTE_QUARANTINE_DIR,
        max_size_bytes=MAX_FILE_SIZE_BYTES,
    )

    sftp_creds, minio_creds = fetch_vault_secrets()
    sftp = build_sftp(sftp_creds)
    blob = build_blob(minio_creds)
    queue = build_queue()
    # NullPool: the worker dispatches each file's DB write via
    # asyncio.run, which spins a fresh event loop; asyncpg connections
    # are not safe to reuse across event loops, so we avoid pooling.
    engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)

    sftp.connect()
    blob.startup()

    while True:
        try:
            entries = sftp.list_dir(REMOTE_UPLOAD_DIR)
        except (SFTPConnectError, paramiko.SSHException, OSError):
            log.exception("ingest.list_failed_reconnecting")
            try:
                _reconnect(sftp)
            except Exception:  # noqa: BLE001
                log.exception("ingest.reconnect_failed")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        for name in entries:
            request_id = str(uuid.uuid4())
            structlog.contextvars.bind_contextvars(request_id=request_id, filename=name)
            try:
                process_one(name, sftp, blob, queue, engine)
            except (BlobUnavailableError, QueueUnavailableError):
                log.exception("ingest.downstream_unavailable")
            except Exception:  # noqa: BLE001
                log.exception("ingest.unexpected_error")
            finally:
                structlog.contextvars.clear_contextvars()

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
