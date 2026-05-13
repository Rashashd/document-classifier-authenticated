"""End-to-end ingestion pipeline test.

Drops the four canonical fixture files onto the running SFTP container,
invokes the worker's ``process_one`` per file against the live MinIO /
Redis / Postgres stack, and asserts the post-conditions of each triage
path: SFTP state, MinIO bucket, and Postgres ``batches`` table.

Mutates real services — therefore cleans up SFTP + Postgres state both
before AND after the test so the suite is robust to ordering vs
``test_infra_adapters.py``.

Stack required (any of these missing → the test fails at setup):

* postgres on localhost:5432   (database "app", schema from alembic)
* sftp     on localhost:2222   (scanner / password123, /upload + /quarantine)
* minio    on localhost:9000   (admin / password123)
* redis    on localhost:6379   (db 0)
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Iterator

import pytest
import structlog
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.db.models       import Batch
from app.infra.blob      import MinioBlobClient
from app.infra.queue     import RQClient
from app.infra.sftp      import SFTPClient
from app.workers.sftp_ingest import process_one
from scripts.generate_test_drops import drop_files


# ---------------------------------------------------------------------
# Connection settings — match docker-compose.yml host-side bindings
# ---------------------------------------------------------------------
DATABASE_URL: str = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/app",
)
SFTP_HOST:      str = "localhost"
SFTP_PORT:      int = 2222
SFTP_USER:      str = os.environ.get("SFTP_USER",       "scanner")
SFTP_PASS:      str = os.environ.get("SFTP_PASSWORD",   "change-me-in-production")
MINIO_ENDPOINT: str = "localhost:9000"
MINIO_USER:     str = os.environ.get("MINIO_ROOT_USER",     "admin")
MINIO_PASS:     str = os.environ.get("MINIO_ROOT_PASSWORD", "change-me-in-production")
REDIS_URL:      str = "redis://localhost:6379/0"

UPLOAD_DIR:     str = "/upload"
QUARANTINE_DIR: str = "/quarantine"


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------
@pytest.fixture
def sftp_client() -> Iterator[SFTPClient]:
    client = SFTPClient(
        host=SFTP_HOST, port=SFTP_PORT,
        username=SFTP_USER, password=SFTP_PASS,
    )
    client.connect()
    yield client
    client.close()


@pytest.fixture
def blob_client() -> MinioBlobClient:
    client = MinioBlobClient(
        endpoint=MINIO_ENDPOINT,
        access_key=MINIO_USER, secret_key=MINIO_PASS,
        secure=False,
    )
    client.startup()
    return client


@pytest.fixture
def queue_client() -> RQClient:
    return RQClient(redis_url=REDIS_URL)


@pytest.fixture
def db_engine() -> Iterator[AsyncEngine]:
    # NullPool — process_one uses asyncio.run per file, so connections
    # would otherwise leak across event loops (asyncpg is loop-bound).
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    yield engine
    asyncio.run(engine.dispose())


def _clear_remote_dir(sftp: SFTPClient, remote_dir: str) -> None:
    for name in sftp.list_dir(remote_dir):
        sftp.delete_file(f"{remote_dir}/{name}")


async def _truncate_batches(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE batches CASCADE"))


@pytest.fixture
def fresh_state(
    sftp_client: SFTPClient,
    db_engine:   AsyncEngine,
) -> Iterator[None]:
    """Clean SFTP dirs + truncate batches before AND after the test."""
    _clear_remote_dir(sftp_client, UPLOAD_DIR)
    _clear_remote_dir(sftp_client, QUARANTINE_DIR)
    asyncio.run(_truncate_batches(db_engine))
    yield
    _clear_remote_dir(sftp_client, UPLOAD_DIR)
    _clear_remote_dir(sftp_client, QUARANTINE_DIR)
    asyncio.run(_truncate_batches(db_engine))


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
async def _list_batches(engine: AsyncEngine) -> list[Batch]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        rows = (await session.execute(select(Batch))).scalars().all()
        return list(rows)


# ---------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------
def test_ingest_pipeline_e2e(
    fresh_state,
    sftp_client:  SFTPClient,
    blob_client:  MinioBlobClient,
    queue_client: RQClient,
    db_engine:    AsyncEngine,
) -> None:
    """Drop 4 fixtures, run process_one on each, assert the four outcomes."""
    drop_files(sftp_client, UPLOAD_DIR)

    for name in sftp_client.list_dir(UPLOAD_DIR):
        request_id = str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=request_id, filename=name)
        try:
            process_one(name, sftp_client, blob_client, queue_client, db_engine)
        finally:
            structlog.contextvars.clear_contextvars()

    # SFTP — every file processed; only the malicious one survives, in /quarantine.
    assert sftp_client.list_dir(UPLOAD_DIR)     == []
    assert sftp_client.list_dir(QUARANTINE_DIR) == ["malicious_payload.tiff"]

    # Postgres — exactly one PENDING batch row for the valid file.
    rows = asyncio.run(_list_batches(db_engine))
    assert len(rows) == 1
    only = rows[0]
    assert only.status     == "pending"
    assert only.sftp_path  == f"{UPLOAD_DIR}/valid_document.tiff"
    assert only.owner_id   is None
