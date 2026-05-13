"""RQ inference worker entrypoint.

The worker consumes ``app.domain.jobs.InferenceJob`` payloads, reads the input
document from blob storage, runs the classifier, writes an annotated overlay
PNG, and records a prediction event. The persistence adapter is intentionally
thin because the service/repository layer is owned by another teammate.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageDraw, ImageFont

from app.classifier.inference import Prediction, get_default_classifier
from app.domain.jobs import InferenceJob


LOGGER = logging.getLogger(__name__)


class BlobStore(Protocol):
    def read_bytes(self, key: str) -> bytes:
        """Read an object from blob storage."""

    def write_bytes(self, key: str, content: bytes, content_type: str) -> None:
        """Write an object to blob storage."""


class LocalBlobStore:
    """Filesystem-backed blob store for local development and tests."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(os.getenv("BLOB_ROOT", "/tmp/week6-blobs"))
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve_key(self, key: str) -> Path:
        path = (self.root / key).resolve()
        root = self.root.resolve()
        if root not in path.parents and path != root:
            raise ValueError(f"Blob key escapes local blob root: {key}")
        return path

    def read_bytes(self, key: str) -> bytes:
        return self._resolve_key(key).read_bytes()

    def write_bytes(self, key: str, content: bytes, content_type: str) -> None:
        _ = content_type
        path = self._resolve_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


class MinioBlobStore:
    """MinIO-backed blob store.

    The import is lazy so unit tests can exercise the worker without installing
    the MinIO client.
    """

    def __init__(self) -> None:
        from minio import Minio

        endpoint = os.environ["MINIO_ENDPOINT"]
        access_key = os.environ["MINIO_ACCESS_KEY"]
        secret_key = os.environ["MINIO_SECRET_KEY"]
        secure = os.getenv("MINIO_SECURE", "false").lower() == "true"
        self.bucket = os.getenv("MINIO_BUCKET", "documents")
        self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)

    def read_bytes(self, key: str) -> bytes:
        response = self.client.get_object(self.bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def write_bytes(self, key: str, content: bytes, content_type: str) -> None:
        self.client.put_object(
            self.bucket,
            key,
            BytesIO(content),
            length=len(content),
            content_type=content_type,
        )


class PredictionSink(Protocol):
    def record(self, record: dict) -> None:
        """Persist a prediction record."""


class JsonlPredictionSink:
    """Development fallback until the prediction service/repository is wired."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(os.getenv("PREDICTION_OUTBOX_PATH", "/tmp/week6-predictions.jsonl"))
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, record: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")


def default_blob_store() -> BlobStore:
    if os.getenv("MINIO_ENDPOINT"):
        return MinioBlobStore()
    return LocalBlobStore()


def default_prediction_sink() -> PredictionSink:
    return JsonlPredictionSink()


def overlay_key_for(job: InferenceJob) -> str:
    source = Path(job.filename)
    name = f"{source.stem}.overlay.png"
    return f"batches/{job.batch_id}/overlays/{name}"


def create_overlay_png(image_bytes: bytes, prediction: Prediction) -> bytes:
    """Create a simple annotated preview PNG for the classified document."""

    with Image.open(BytesIO(image_bytes)) as image:
        preview = image.convert("RGB")
        preview.thumbnail((1200, 1200))

    draw = ImageDraw.Draw(preview)
    text = f"{prediction.label} ({prediction.confidence:.2%})"
    padding = 12
    font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    draw.rectangle(
        [0, 0, width + padding * 2, height + padding * 2],
        fill=(0, 0, 0),
    )
    draw.text((padding, padding), text, fill=(255, 255, 255), font=font)

    out = BytesIO()
    preview.save(out, format="PNG")
    return out.getvalue()


def build_prediction_record(
    *,
    job: InferenceJob,
    prediction: Prediction,
    overlay_path: str,
) -> dict:
    return {
        "job_id": str(job.job_id),
        "batch_id": str(job.batch_id),
        "filename": job.filename,
        "blob_path": job.blob_path,
        "label": prediction.label,
        "label_id": prediction.label_id,
        "confidence": prediction.confidence,
        "top_k": prediction.top_k,
        "overlay_path": overlay_path,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def run_inference(
    payload: str,
    *,
    blob_store: BlobStore | None = None,
    prediction_sink: PredictionSink | None = None,
) -> dict:
    """RQ target function.

    ``payload`` must be ``InferenceJob.model_dump_json()``. The return value is
    JSON-serialisable so RQ dashboards and tests can inspect it.
    """

    job = InferenceJob.from_rq_kwargs(payload)
    store = blob_store or default_blob_store()
    sink = prediction_sink or default_prediction_sink()

    LOGGER.info("inference_job_started", extra={"job_id": str(job.job_id), "batch_id": str(job.batch_id)})

    image_bytes = store.read_bytes(job.blob_path)
    classifier = get_default_classifier()
    prediction = classifier.predict_bytes(image_bytes)

    overlay_path = overlay_key_for(job)
    overlay_png = create_overlay_png(image_bytes, prediction)
    store.write_bytes(overlay_path, overlay_png, content_type="image/png")

    record = build_prediction_record(job=job, prediction=prediction, overlay_path=overlay_path)
    sink.record(record)

    LOGGER.info(
        "inference_job_finished",
        extra={
            "job_id": str(job.job_id),
            "batch_id": str(job.batch_id),
            "label": prediction.label,
            "confidence": prediction.confidence,
        },
    )
    return record


__all__ = [
    "BlobStore",
    "LocalBlobStore",
    "MinioBlobStore",
    "PredictionSink",
    "JsonlPredictionSink",
    "run_inference",
    "main",
]


def main() -> None:
    """Run an RQ worker for inference jobs."""

    from redis import Redis
    from rq import Worker

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    queue_name = os.getenv("INFERENCE_QUEUE", "inference")
    connection = Redis.from_url(redis_url)

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    LOGGER.info("inference_worker_started", extra={"queue": queue_name, "redis_url": redis_url})
    Worker([queue_name], connection=connection).work()


if __name__ == "__main__":
    main()
