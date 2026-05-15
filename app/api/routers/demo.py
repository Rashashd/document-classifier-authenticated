"""Demo pipeline endpoint.

Lets the frontend inject a synthetic TIFF document directly into the
classification pipeline without needing SFTP access. Useful for live demos.
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
from fastapi import APIRouter, Depends, Request
from PIL import Image, ImageDraw, ImageFont
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


_DEMO_STYLES = ("email", "file_folder", "advertisement", "scientific_pub", "news_article")

_LOREM = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua ut enim ad minim "
    "veniam quis nostrud exercitation ullamco laboris nisi ut aliquip ex "
    "ea commodo consequat duis aute irure dolor in reprehenderit in "
    "voluptate velit esse cillum dolore eu fugiat nulla pariatur excepteur "
    "sint occaecat cupidatat non proident sunt in culpa qui officia deserunt"
)

# Loaded once at import time — Pillow 10+ bundles its own TTF so no system
# font is required (python:3.11-slim has none installed).
_FONT_SM = ImageFont.load_default(size=8)
_FONT_MD = ImageFont.load_default(size=11)
_FONT_LG = ImageFont.load_default(size=16)
_FONT_XL = ImageFont.load_default(size=22)


def _wrap(text: str, chars: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > chars:
            if current:
                lines.append(current)
            current = word
        else:
            current = (current + " " + word).strip()
    if current:
        lines.append(current)
    return lines


def _pick_document() -> tuple[bytes, str]:
    """Return (image_bytes, extension) for the next demo injection.

    Prefers a real RVL-CDIP sample from demo_samples/ for high-confidence
    predictions. Falls back to a synthetic PIL image when the folder is
    empty or missing (e.g. first boot before samples are added).
    """
    if _SAMPLES_DIR.is_dir():
        candidates = [
            p for p in _SAMPLES_DIR.iterdir()
            if p.suffix.lower() in {".tif", ".tiff", ".png"}
        ]
        if candidates:
            chosen = random.choice(candidates)
            return chosen.read_bytes(), chosen.suffix.lstrip(".")

    return _generate_demo_tiff(), "tiff"


def _generate_demo_tiff() -> bytes:
    """Return a synthetic document TIFF with real rendered text.

    Each call picks one of five document styles (email, file_folder,
    advertisement, scientific_pub, news_article) with randomised content so
    the ML model sees genuinely different inputs and produces varied predictions.
    Pillow's bundled font is used — no system fonts required.
    """
    rng = random.Random()
    style = rng.choice(_DEMO_STYLES)
    size = 224
    img = Image.new("RGB", (size, size), color=(250, 248, 245))
    draw = ImageDraw.Draw(img)

    if style == "email":
        y = 8
        headers = [
            ("From:", rng.choice(["j.smith@company.com", "a.jones@firm.org", "m.lee@corp.net"])),
            ("To:", rng.choice(["team@company.com", "board@firm.org", "all@corp.net"])),
            ("Date:", rng.choice(["15 Jan 2024", "03 Mar 2024", "28 Nov 2023"])),
            ("Subject:", rng.choice(["Q4 Report", "Project Update", "Meeting Follow-up"])),
        ]
        for label, value in headers:
            draw.text((8, y), label, font=_FONT_SM, fill=(20, 20, 20))
            draw.text((52, y), value, font=_FONT_SM, fill=(50, 50, 50))
            y += 12
        y += 2
        draw.line([(8, y), (216, y)], fill=(180, 180, 180), width=1)
        y += 8
        for line in _wrap(_LOREM, 38)[:9]:
            draw.text((8, y), line, font=_FONT_SM, fill=(60, 60, 60))
            y += 10
        y += 4
        draw.text((8, y), "Best regards,", font=_FONT_SM, fill=(40, 40, 40))
        y += 10
        draw.text((8, y), rng.choice(["John Smith", "Sarah Jones", "Mark Davis"]), font=_FONT_SM, fill=(20, 20, 20))

    elif style == "file_folder":
        draw.rectangle([8, 8, 88, 24], fill=(210, 185, 140), outline=(130, 100, 60), width=1)
        draw.text((14, 11), rng.choice(["FOLDER A", "SECTION 3", "ARCHIVE"]), font=_FONT_SM, fill=(70, 40, 10))
        draw.rectangle([8, 24, 216, 216], fill=(228, 208, 165), outline=(130, 100, 60), width=2)
        y = 34
        draw.text((16, y), rng.choice(["CONFIDENTIAL", "PERSONNEL FILE", "PROJECT DOCS"]), font=_FONT_MD, fill=(60, 35, 10))
        y += 18
        draw.line([(16, y), (200, y)], fill=(130, 100, 60), width=1)
        y += 10
        items = ["Report_Q4.pdf", "Budget_2024.xlsx", "Meeting_Notes.docx", "Ref_Letter.pdf", "Contract.pdf"]
        rng.shuffle(items)
        for item in items[: rng.randint(3, 5)]:
            draw.text((20, y), "• " + item, font=_FONT_SM, fill=(50, 30, 10))
            y += 12
        y += 6
        draw.text((16, y), "Filed: " + rng.choice(["Jan 2024", "Mar 2024", "Dec 2023"]), font=_FONT_SM, fill=(80, 55, 20))
        y += 12
        draw.text((16, y), "Ref: FLD-" + str(rng.randint(100, 999)), font=_FONT_SM, fill=(80, 55, 20))

    elif style == "advertisement":
        img = Image.new("RGB", (size, size), color=(255, 255, 240))
        draw = ImageDraw.Draw(img)
        draw.rectangle([3, 3, 221, 221], outline=(180, 0, 0), width=3)
        headline = rng.choice(["SPECIAL OFFER!", "LIMITED TIME DEAL!", "SALE NOW ON!"])
        bbox = draw.textbbox((0, 0), headline, font=_FONT_LG)
        w = bbox[2] - bbox[0]
        draw.text(((size - w) // 2, 16), headline, font=_FONT_LG, fill=(180, 0, 0))
        y = 50
        draw.line([(20, y), (204, y)], fill=(180, 0, 0), width=1)
        y += 10
        promo = rng.choice(["50% OFF", "BUY 1 GET 1", "FREE GIFT"])
        bbox2 = draw.textbbox((0, 0), promo, font=_FONT_XL)
        w2 = bbox2[2] - bbox2[0]
        draw.text(((size - w2) // 2, y), promo, font=_FONT_XL, fill=(20, 20, 20))
        y += 34
        sub = rng.choice(["TODAY ONLY", "GET ONE FREE", "WITH EVERY ORDER"])
        bbox3 = draw.textbbox((0, 0), sub, font=_FONT_MD)
        w3 = bbox3[2] - bbox3[0]
        draw.text(((size - w3) // 2, y), sub, font=_FONT_MD, fill=(100, 100, 100))
        y += 24
        draw.line([(20, y), (204, y)], fill=(200, 200, 200), width=1)
        y += 10
        for line in [
            "Call: 1-800-555-" + str(rng.randint(1000, 9999)),
            "www." + rng.choice(["store", "shop", "deals"]) + ".com",
        ]:
            bbox4 = draw.textbbox((0, 0), line, font=_FONT_SM)
            w4 = bbox4[2] - bbox4[0]
            draw.text(((size - w4) // 2, y), line, font=_FONT_SM, fill=(60, 60, 60))
            y += 14

    elif style == "scientific_pub":
        title = rng.choice([
            "Neural Methods for Document Classification",
            "Visual Layout Analysis with Deep Learning",
            "Convolutional Networks Applied to OCR Tasks",
        ])
        draw.text((4, 5), title, font=_FONT_SM, fill=(10, 10, 10))
        y = 17
        draw.text((4, y), "A. Author, B. Author  —  Conf. 2024", font=_FONT_SM, fill=(90, 90, 90))
        y += 12
        draw.line([(4, y), (220, y)], fill=(0, 0, 0), width=1)
        y += 6
        draw.line([(112, y), (112, 218)], fill=(180, 180, 180), width=1)
        sections = rng.sample(["Abstract", "Introduction", "Methods", "Results", "Discussion"], 2)
        for cx, section in zip((4, 116), sections):
            cy = y
            draw.text((cx, cy), section, font=_FONT_SM, fill=(10, 10, 10))
            cy += 11
            for line in _wrap(_LOREM, 18)[:12]:
                draw.text((cx, cy), line, font=_FONT_SM, fill=(55, 55, 55))
                cy += 9

    else:  # news_article
        draw.rectangle([0, 0, size, 30], fill=(15, 15, 15))
        headline = rng.choice(["ECONOMY GROWS 3.5%", "NEW POLICY PASSED", "RECORD 2024 RESULTS"])
        bbox = draw.textbbox((0, 0), headline, font=_FONT_MD)
        w = bbox[2] - bbox[0]
        draw.text(((size - w) // 2, 8), headline, font=_FONT_MD, fill=(255, 255, 255))
        draw.line([(0, 30), (size, 30)], fill=(180, 0, 0), width=2)
        col_w = 68
        section_labels = rng.sample(["Report", "Analysis", "Comment", "Opinion"], 3)
        for i, (cx, label) in enumerate(zip((4, 78, 152), section_labels)):
            y = 38
            draw.text((cx, y), label, font=_FONT_SM, fill=(15, 15, 15))
            y += 11
            draw.line([(cx, y), (cx + col_w - 4, y)], fill=(120, 120, 120), width=1)
            y += 5
            for line in _wrap(_LOREM[i * 40 :], 11)[:14]:
                draw.text((cx, y), line, font=_FONT_SM, fill=(55, 55, 55))
                y += 9

    out = BytesIO()
    img.save(out, format="TIFF")
    return out.getvalue()


def _build_blob(request: Request) -> MinioBlobClient:
    """Create a MinIO client using Vault credentials from app.state."""
    vault: VaultClient = request.app.state.vault
    settings = request.app.state.settings
    # Synchronous Vault call — acceptable for a low-frequency demo endpoint.
    minio_creds: dict[str, Any] = vault.get_secret(settings.vault_minio_path)  # type: ignore[no-any-return]
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
    """Generate a synthetic TIFF, upload it to MinIO, and enqueue a classification job."""
    image_bytes, ext = _pick_document()

    # Pre-resize to 512×512 so the worker downloads and decodes a smaller file.
    # The classifier internally resizes to 224×224 anyway, so accuracy is unaffected.
    with Image.open(BytesIO(image_bytes)) as img:
        img = img.convert("RGB")
        img.thumbnail((512, 512))
        buf = BytesIO()
        img.save(buf, format="PNG")
        image_bytes = buf.getvalue()
    ext = "png"

    filename = f"demo_{uuid.uuid4().hex[:8]}.{ext}"
    content_type = "image/png"

    blob = _build_blob(request)
    blob.upload_file(filename, image_bytes, content_type=content_type)

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
    return BatchListResponse(
        items=list(batches),
        total=total,
        skip=0,
        limit=limit,
    )
