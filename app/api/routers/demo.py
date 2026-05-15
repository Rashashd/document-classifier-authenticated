"""Demo pipeline endpoint.

Lets the frontend inject a real RVL-CDIP sample document directly into the classification pipeline without needing SFTP access. Useful for live demos.
Endpoints are auth-gated (any active user) but not role-restricted.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.models import Batch, User
from app.db.session import get_async_session
from app.domain.batch import BatchListResponse
from app.domain.jobs import InferenceJob
from app.infra.blob import MinioBlobClient
from app.infra.queue import RQClient
from app.infra.vault import VaultClient
from app.services.batch_service import BatchService

router = APIRouter(prefix="/demo", tags=["demo"])
logger = structlog.get_logger(__name__)

_SAMPLES_DIR = Path(__file__).parent.parent / "demo_samples"

QUEUE_NAME = "classification_queue"
INFERENCE_FUNC_PATH = "app.workers.inference.run_inference"


class TriggerResponse(BaseModel):
    batch_id: str
    job_id: str
    filename: str


class QueueStatsResponse(BaseModel):
    pending: int
    processing: int
    done: int
    failed: int


def _pick_document() -> bytes:
    """Return image bytes for a random sample from demo_samples/."""
    candidates = [
        p for p in _SAMPLES_DIR.iterdir()
        if p.suffix.lower() in {".tif", ".tiff", ".png"}
    ] if _SAMPLES_DIR.is_dir() else []
    if not candidates:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No sample images found in demo_samples/. Add .tif/.png files.",
        )
    return random.choice(candidates).read_bytes()


def _build_blob(request: Request) -> MinioBlobClient:
    vault: VaultClient = request.app.state.vault
    settings = request.app.state.settings
    minio_creds: dict[str, Any] = vault.get_secret(settings.vault_minio_path)
    blob = MinioBlobClient(
        endpoint=settings.minio_endpoint,
        access_key=minio_creds["access_key"],
        secret_key=minio_creds["secret_key"],
        secure=False,
    )
    blob.startup()
    return blob


@router.post("/trigger", response_model=TriggerResponse)
async def trigger_demo(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> TriggerResponse:
    """Pick a random RVL-CDIP sample, upload it to MinIO, and enqueue a classification job."""
    image_bytes = _pick_document()

    # Pre-resize to 512×512 so the worker downloads and decodes a smaller file.
    # The classifier internally resizes to 224×224 anyway, so accuracy is unaffected.
    with Image.open(BytesIO(image_bytes)) as raw:
        converted = raw.convert("RGB")
        converted.thumbnail((224, 224))
        buf = BytesIO()
        converted.save(buf, format="PNG")
        image_bytes = buf.getvalue()

    filename = f"demo_{uuid.uuid4().hex[:8]}.png"

    blob = _build_blob(request)
    blob.upload_file(filename, image_bytes, content_type="image/png")

    batch_service = BatchService(session)
    batch_id = await batch_service.create_pending_batch(
        sftp_path=f"/demo/{filename}",
        owner_id=current_user.id,
    )

    inference_job = InferenceJob(
        batch_id=batch_id,
        blob_path=filename,
        filename=filename,
        enqueued_at=datetime.now(timezone.utc),
    )

    settings = request.app.state.settings
    queue = RQClient(settings.redis_url)
    job_id = queue.enqueue_job(
        queue_name=QUEUE_NAME,
        payload={
            "func": INFERENCE_FUNC_PATH,
            "kwargs": {"payload": inference_job.model_dump_json()},
        },
    )

    logger.info("demo.triggered", batch_id=str(batch_id), job_id=job_id, filename=filename)
    return TriggerResponse(batch_id=str(batch_id), job_id=job_id, filename=filename)


@router.get("/queue", response_model=QueueStatsResponse)
async def queue_stats(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> QueueStatsResponse:
    """Return batch counts grouped by status for pipeline visualization."""
    result = await session.execute(
        select(Batch.status, func.count(Batch.id)).group_by(Batch.status)
    )
    counts: dict[str, int] = {}
    for status_val, cnt in result:
        key = status_val.value if hasattr(status_val, "value") else str(status_val)
        counts[key] = int(cnt)

    return QueueStatsResponse(
        pending=counts.get("pending", 0),
        processing=counts.get("processing", 0),
        done=counts.get("done", 0),
        failed=counts.get("failed", 0),
    )


@router.get("/batches", response_model=BatchListResponse)
async def list_demo_batches(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
    limit: int = 20,
) -> BatchListResponse:
    """Return the most recent batches without Redis caching, for the live demo feed."""
    batch_service = BatchService(session)
    batches, total = await batch_service.list_batches(skip=0, limit=limit)
    return BatchListResponse(items=list(batches), total=total, skip=0, limit=limit)
