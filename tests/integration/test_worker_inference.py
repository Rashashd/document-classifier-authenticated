"""End-to-end inference-worker test against the local compose stack.

Drops a real TIFF into MinIO, inserts a PENDING batch row, calls
``run_inference`` with a mocked ML callable, and asserts the four
post-conditions:

  1. the overlay PNG exists in MinIO
  2. the batch status flipped to ``done``
  3. exactly one prediction row exists for the batch, with the mocked
     label/confidence
  4. cache invalidation ran without crashing (no keys assertion)

The classifier is mocked via the ``classify`` kwarg of ``run_inference``
so the test is fast and torch-free. The DB, blob, and cache are NOT
mocked — they hit the live compose stack.
"""

from __future__ import annotations

import asyncio
import datetime
import io
import os
import uuid
from typing import Iterator

import pytest
from PIL import Image
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.db.models import Batch, Prediction
from app.domain.batch import BatchStatus
from app.domain.jobs import InferenceJob
from app.infra.blob import MinioBlobClient
from app.workers.inference import run_inference


DATABASE_URL: str = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5433/app",
)
MINIO_ENDPOINT:    str = "localhost:9000"
MINIO_ACCESS_KEY:  str = os.environ.get("MINIO_ROOT_USER",     "admin")
MINIO_SECRET_KEY:  str = os.environ.get("MINIO_ROOT_PASSWORD", "change-me-in-production")
REDIS_URL:         str = "redis://localhost:6379/0"

INPUT_KEY_PREFIX = "test/inference"

# Mocked classifier return — chosen so ``label`` matches a real
# DocumentLabel enum member.
MOCK_LABEL:      str   = "invoice"
MOCK_CONFIDENCE: float = 0.99


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------
@pytest.fixture
def blob_client() -> MinioBlobClient:
    client = MinioBlobClient(
        endpoint=MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False,
    )
    client.startup()
    return client


@pytest.fixture
def db_engine() -> Iterator[AsyncEngine]:
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    yield engine
    asyncio.run(engine.dispose())


# cache_redis fixture removed — the worker now initialises FastAPICache /
# CacheService internally via init_redis_cache(redis_url). The test passes
# the REDIS_URL through the redis_url kwarg.


def _build_tiff() -> bytes:
    img = Image.new("L", (224, 224), color=200)
    buf = io.BytesIO()
    img.save(buf, format="TIFF")
    return buf.getvalue()


async def _truncate_test_rows(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE predictions, batches CASCADE"))


async def _seed_pending_batch(engine: AsyncEngine, sftp_path: str) -> uuid.UUID:
    batch_id = uuid.uuid4()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add(
            Batch(
                id=batch_id,
                sftp_path=sftp_path,
                owner_id=None,
                status=BatchStatus.pending,
            )
        )
        await session.commit()
    return batch_id


async def _read_batch_status(engine: AsyncEngine, batch_id: uuid.UUID) -> str:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        row = (await session.execute(select(Batch).where(Batch.id == batch_id))).scalar_one()
        return str(row.status)


async def _read_predictions(engine: AsyncEngine, batch_id: uuid.UUID) -> list[Prediction]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        rows = (
            await session.execute(select(Prediction).where(Prediction.batch_id == batch_id))
        ).scalars().all()
        return list(rows)


def _mock_classify(image_bytes: bytes) -> tuple[str, float, bytes]:
    """Stub for the ML pipeline. Returns the input bytes as the 'overlay'."""
    return MOCK_LABEL, MOCK_CONFIDENCE, image_bytes


# ---------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------
def test_run_inference_end_to_end(
    blob_client: MinioBlobClient,
    db_engine:   AsyncEngine,
) -> None:
    """Upload TIFF → seed batch → run worker (mock ML) → assert state."""
    asyncio.run(_truncate_test_rows(db_engine))

    filename  = "valid_document.tiff"
    blob_key  = f"{INPUT_KEY_PREFIX}/{uuid.uuid4()}/{filename}"
    blob_client.upload_file(blob_key, _build_tiff(), content_type="image/tiff")

    batch_id = asyncio.run(
        _seed_pending_batch(db_engine, sftp_path=f"/upload/{filename}")
    )

    job = InferenceJob(
        batch_id=batch_id,
        blob_path=blob_key,
        filename=filename,
        enqueued_at=datetime.datetime.now(datetime.timezone.utc),
    )

    result = run_inference(
        payload=job.model_dump_json(),
        blob=blob_client,
        engine=db_engine,
        redis_url=REDIS_URL,
        classify=_mock_classify,
    )

    # 1. Result envelope is shaped as expected.
    assert result["batch_id"] == str(batch_id)
    assert result["label"] == MOCK_LABEL
    assert result["confidence"] == MOCK_CONFIDENCE
    assert result["overlay_path"].startswith("s3://documents/")

    # 2. Overlay PNG is downloadable from MinIO (the mock returns the
    #    TIFF bytes back, so we just check the object exists).
    overlay_key = result["overlay_path"].removeprefix("s3://documents/")
    overlay_bytes = blob_client.download_file(overlay_key)
    assert len(overlay_bytes) > 0

    # 3. Batch flipped to "done".
    assert asyncio.run(_read_batch_status(db_engine, batch_id)) == "done"

    # 4. Exactly one prediction row exists for the batch.
    preds = asyncio.run(_read_predictions(db_engine, batch_id))
    assert len(preds) == 1
    only = preds[0]
    assert only.filename     == filename
    assert only.label        == MOCK_LABEL
    assert only.confidence   == pytest.approx(MOCK_CONFIDENCE)
    assert only.overlay_path == result["overlay_path"]
