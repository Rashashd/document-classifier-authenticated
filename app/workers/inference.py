"""RQ inference worker entrypoint.

Consumes ``app.domain.jobs.InferenceJob`` payloads from Redis,  downloads the document from MinIO, classifies it, writes an annotated overlay PNG back to MinIO, persists the prediction in Postgres, marks the parent batch ``done``, and invalidates API caches.
"""

from __future__ import annotations

import asyncio
import os
import sys
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

import structlog
from PIL import Image, ImageDraw, ImageFont
from redis import Redis
from rq import Worker

from app.workers._common import build_blob, configure_logging, get_vault_client
from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.classifier.inference import (
    ClassifierArtifactError,
    Prediction,
    assert_classifier_artifacts,
    get_default_classifier,
)
from app.domain.jobs import InferenceJob
from app.domain.prediction import DocumentLabel, PredictionCreate
from app.infra.blob import MinioBlobClient
from app.infra.cache import init_redis_cache
from app.services.audit_service import AuditService
from app.services.cache_service import CacheService
from app.services.prediction_service import PredictionService

# The integration-test seam: callers can substitute a fake classifier (returning a known label / confidence / overlay) to exercise the full pipeline without torch + the ConvNeXt weights installed.
ClassifyFn = Callable[[bytes], tuple[str, float, bytes]]

log = structlog.get_logger("inference_worker")

QUEUE_NAME = os.environ.get("INFERENCE_QUEUE", "classification_queue")

# secrets bootstrap

def fetch_vault_secrets() -> dict[str, Any]:
    minio_path = os.environ.get("VAULT_MINIO_PATH", "minio")
    try:
        return get_vault_client().get_secret(minio_path)
    except Exception as exc:  # noqa: BLE001
        log.critical("vault.boot.fetch_failed", minio_path=minio_path, error=str(exc))
        sys.exit(1)


# factories 

def build_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://redis:6379/0")

# overlay rendering (stays in the worker; classifier file remains ML-only)

def overlay_key_for(job: InferenceJob) -> str:
    source = Path(job.filename)
    return f"batches/{job.batch_id}/overlays/{source.stem}.overlay.png"

def create_overlay_png(image_bytes: bytes, prediction: Prediction) -> bytes:
    """Render a small annotated preview PNG with the predicted label."""
    with Image.open(BytesIO(image_bytes)) as image:
        preview = image.convert("RGB")
        preview.thumbnail((1200, 1200))

    draw = ImageDraw.Draw(preview)
    text = f"{prediction.label} ({prediction.confidence:.2%})"
    padding = 12
    font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.rectangle(
        [0, 0, width + padding * 2, height + padding * 2], fill=(0, 0, 0)
    )
    draw.text((padding, padding), text, fill=(255, 255, 255), font=font)

    out = BytesIO()
    preview.save(out, format="PNG")
    return out.getvalue()


# classification + overlay (the single ML seam)

def run_classification(image_bytes: bytes) -> tuple[str, float, bytes]:
    """
    Classify + render overlay in one step. Returns (label, confidence, overlay_png).
    """
    prediction: Prediction = get_default_classifier().predict_bytes(image_bytes)
    overlay_png = create_overlay_png(image_bytes, prediction)
    return prediction.label, prediction.confidence, overlay_png


# DB integration points

async def _persist(
    engine: AsyncEngine,
    redis_url: str,
    prediction_in: PredictionCreate,
) -> None:
    """
    Open one session, save the prediction, flip the batch to done.
    """
    from fastapi_cache import FastAPICache
    from fastapi_cache.backends.redis import RedisBackend

    # FastAPICache.init() short-circuits when already initialised, so a stale (closed-loop) AsyncRedis from a prior asyncio.run would leak into this run. Reset clears _init and _backend.
    FastAPICache.reset()
    await init_redis_cache(redis_url)
    cache_service = CacheService()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            audit_service = AuditService(session)
            await PredictionService(
                session,
                cache_service=cache_service,
                audit_service=audit_service,
            ).save_prediction_and_complete_batch(prediction_in)
    finally:
        backend = FastAPICache.get_backend()
        if isinstance(backend, RedisBackend):
            await backend.redis.close()


# the RQ target function

def run_inference(
    payload: str,
    *,
    blob: MinioBlobClient | None = None,
    engine: AsyncEngine | None = None,
    redis_url: str | None = None,
    classify: ClassifyFn | None = None,
) -> dict[str, Any]:
    """
    RQ-callable inference entrypoint.

    Returns a JSON-friendly summary so RQ dashboards can inspect runs.
    """
    job = InferenceJob.from_rq_kwargs(payload)
    structlog.contextvars.bind_contextvars(
        job_id=str(job.job_id), batch_id=str(job.batch_id)
    )

    try:
        blob_client = blob or build_blob(fetch_vault_secrets())
        db_engine = engine or create_async_engine(
            os.environ["DATABASE_URL"], poolclass=NullPool
        )
        classify_fn: ClassifyFn = classify or run_classification

        log.info("inference.start", filename=job.filename)
        image_bytes = blob_client.download_file(job.blob_path)
        label, confidence, overlay_png = classify_fn(image_bytes)

        overlay_key = overlay_key_for(job)
        overlay_uri = blob_client.upload_file(
            overlay_key, overlay_png, content_type="image/png"
        )

        # DocumentLabel enforces the 16-class RVL-CDIP taxonomy.
        # The model card MUST list class names matching the enum; a mismatch surfaces as ValueError right here.
        prediction_in = PredictionCreate(
            batch_id=job.batch_id,
            filename=job.filename,
            label=DocumentLabel(label),
            confidence=confidence,
            overlay_path=overlay_uri,
        )
        resolved_redis_url = redis_url or os.environ.get(
            "REDIS_URL", "redis://redis:6379/0"
        )
        asyncio.run(_persist(db_engine, resolved_redis_url, prediction_in))

        log.info(
            "inference.success",
            label=label,
            confidence=confidence,
            overlay_uri=overlay_uri,
        )
        return {
            "job_id": str(job.job_id),
            "batch_id": str(job.batch_id),
            "label": label,
            "confidence": confidence,
            "overlay_path": overlay_uri,
        }
    finally:
        structlog.contextvars.clear_contextvars()


__all__ = ["run_inference", "run_classification", "create_overlay_png", "main"]


# main loop

def main() -> None:
    """Start an RQ worker that consumes inference jobs."""
    configure_logging()
    log.info("inference_worker.boot", queue=QUEUE_NAME)

    # Refuse-to-start: classifier weights + card must be present and SHA-256-match the model_card.json. A late discovery (first job) would already be in-flight by the time the worker realises.
    try:
        assert_classifier_artifacts()
    except ClassifierArtifactError as exc:
        log.critical("refuse_to_boot", reason="classifier_artifacts", error=str(exc))
        sys.exit(1)

    # Bootstrap Vault and prove we can reach all backing services before joining the queue — refuse-to-start semantics.
    fetch_vault_secrets()  # exit-on-failure side effect

    connection = Redis.from_url(build_redis_url())
    Worker([QUEUE_NAME], connection=connection).work()


if __name__ == "__main__":
    main()
