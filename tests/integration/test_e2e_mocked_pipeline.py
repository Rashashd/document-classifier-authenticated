"""Full end-to-end pipeline test: SFTP drop → Postgres prediction row.

Mocks ONLY ``get_default_classifier`` so torch + the ConvNeXt weights
are not needed. Every other layer hits the live compose stack:

    sftp ─► worker-1 (process_one)
                   ├─ MinIO upload
                   ├─ Postgres insert (PENDING)
                   └─ Redis enqueue (rq:queue:classification_queue)

    redis ─► worker-2 (run_inference)
                   ├─ MinIO download
                   ├─ MOCKED classifier → Prediction(label="invoice", ...)
                   ├─ MinIO upload (overlay)
                   └─ Postgres insert (prediction) + status → done

The ``fresh_state`` fixture cleans up before AND after so this test
doesn't pollute ``test_infra_adapters.py`` or ``test_ingest_pipeline.py``.
"""

from __future__ import annotations

import asyncio
import io
import os
from typing import Iterator
from unittest.mock import patch

import pytest
from PIL import Image
from redis import Redis as SyncRedis
from rq.job import Job
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.classifier.inference import Prediction
from app.db.models import Batch, Prediction as PredictionRow
from app.infra.blob import MinioBlobClient
from app.infra.queue import RQClient
from app.infra.sftp import SFTPClient
from app.workers.inference import run_inference
from app.workers.sftp_ingest import process_one


# ---------------------------------------------------------------------
# Connection settings — match docker-compose.yml host-side bindings
# ---------------------------------------------------------------------
DATABASE_URL: str = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5433/app",
)
SFTP_HOST:        str = "localhost"
SFTP_PORT:        int = 2222
SFTP_USER:        str = os.environ.get("SFTP_USER",       "scanner")
SFTP_PASS:        str = os.environ.get("SFTP_PASSWORD",   "change-me-in-production")
MINIO_ENDPOINT:   str = "localhost:9000"
MINIO_USER:       str = os.environ.get("MINIO_ROOT_USER",     "admin")
MINIO_PASS:       str = os.environ.get("MINIO_ROOT_PASSWORD", "change-me-in-production")
REDIS_URL:        str = "redis://localhost:6379/0"

UPLOAD_DIR:     str = "/upload"
QUARANTINE_DIR: str = "/quarantine"
QUEUE_NAME:     str = "classification_queue"
QUEUE_LIST_KEY: str = f"rq:queue:{QUEUE_NAME}"


# Mocked classifier return — chosen so DocumentLabel("invoice") resolves.
MOCK_PREDICTION = Prediction(
    label="invoice",
    label_id=6,
    confidence=0.99,
    top_k=[{"label": "invoice", "label_id": 6, "confidence": 0.99}],
)


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
def redis_sync() -> Iterator[SyncRedis]:
    client = SyncRedis.from_url(REDIS_URL)
    yield client
    client.close()


# cache_redis fixture removed — the worker now initialises CacheService
# internally from REDIS_URL.


@pytest.fixture
def db_engine() -> Iterator[AsyncEngine]:
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    yield engine
    asyncio.run(engine.dispose())


def _clear_remote_dir(sftp: SFTPClient, remote_dir: str) -> None:
    for name in sftp.list_dir(remote_dir):
        sftp.delete_file(f"{remote_dir}/{name}")


async def _truncate_test_rows(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE predictions, batches CASCADE"))


@pytest.fixture
def fresh_state(
    sftp_client: SFTPClient,
    db_engine:   AsyncEngine,
    redis_sync:  SyncRedis,
) -> Iterator[None]:
    """Reset all four mutable surfaces before AND after the test."""
    _clear_remote_dir(sftp_client, UPLOAD_DIR)
    _clear_remote_dir(sftp_client, QUARANTINE_DIR)
    asyncio.run(_truncate_test_rows(db_engine))
    redis_sync.delete(QUEUE_LIST_KEY)
    yield
    _clear_remote_dir(sftp_client, UPLOAD_DIR)
    _clear_remote_dir(sftp_client, QUARANTINE_DIR)
    asyncio.run(_truncate_test_rows(db_engine))
    redis_sync.delete(QUEUE_LIST_KEY)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _build_valid_tiff() -> bytes:
    img = Image.new("L", (224, 224), color=200)
    buf = io.BytesIO()
    img.save(buf, format="TIFF")
    return buf.getvalue()


async def _list_batches(engine: AsyncEngine) -> list[Batch]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        return list((await session.execute(select(Batch))).scalars().all())


async def _list_predictions(engine: AsyncEngine) -> list[PredictionRow]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        return list((await session.execute(select(PredictionRow))).scalars().all())


# ---------------------------------------------------------------------
# E2E test
# ---------------------------------------------------------------------
def test_full_ingestion_to_inference_pipeline(
    fresh_state,
    sftp_client:  SFTPClient,
    blob_client:  MinioBlobClient,
    queue_client: RQClient,
    redis_sync:   SyncRedis,
    db_engine:    AsyncEngine,
) -> None:
    """Trace one TIFF from SFTP through both workers to the prediction row."""
    filename = "valid_document.tiff"

    # ---- Phase 1: SFTP drop -----------------------------------------
    sftp_client.write_file(f"{UPLOAD_DIR}/{filename}", _build_valid_tiff())

    # ---- Phase 2: Worker 1 processes the drop -----------------------
    process_one(
        filename,
        sftp_client, blob_client, queue_client, db_engine,
    )

    # ---- Phase 3: Queue verification --------------------------------
    assert redis_sync.llen(QUEUE_LIST_KEY) == 1, "worker 1 must enqueue exactly one job"

    # ---- Phase 4: Mock the ML + pop the job from Redis --------------
    job_id = redis_sync.lpop(QUEUE_LIST_KEY).decode()
    rq_job = Job.fetch(job_id, connection=redis_sync)
    payload = rq_job.kwargs["payload"]

    class _MockClassifier:
        def predict_bytes(self, _image_bytes: bytes) -> Prediction:
            return MOCK_PREDICTION

    # ---- Phase 5: Worker 2 runs against the popped payload ----------
    with patch(
        "app.workers.inference.get_default_classifier",
        return_value=_MockClassifier(),
    ):
        result = run_inference(
            payload,
            blob=blob_client,
            engine=db_engine,
            redis_url=REDIS_URL,
        )

    # ---- Phase 6: Final assertions ----------------------------------
    # SFTP /upload is empty (worker 1 deleted the file after enqueue).
    assert sftp_client.list_dir(UPLOAD_DIR) == []

    # Exactly one batch row, status flipped to done.
    batches = asyncio.run(_list_batches(db_engine))
    assert len(batches) == 1
    only_batch = batches[0]
    assert str(only_batch.status) == "done"
    assert only_batch.sftp_path == f"{UPLOAD_DIR}/{filename}"

    # Exactly one prediction row, linked to the batch.
    predictions = asyncio.run(_list_predictions(db_engine))
    assert len(predictions) == 1
    only_pred = predictions[0]
    assert only_pred.batch_id   == only_batch.id
    assert only_pred.label      == "invoice"
    assert only_pred.confidence == pytest.approx(0.99)
    assert only_pred.filename   == filename
    assert only_pred.overlay_path == result["overlay_path"]

    # Overlay PNG is downloadable from MinIO.
    overlay_key = result["overlay_path"].removeprefix("s3://documents/")
    overlay_bytes = blob_client.download_file(overlay_key)
    assert overlay_bytes.startswith(b"\x89PNG")
