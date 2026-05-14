from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_role
from app.db.session import get_async_session
from app.db.models import Batch, Prediction
from app.domain.batch import BatchStatus
from app.domain.prediction import DocumentLabel
from app.db.models import User   

router = APIRouter(prefix="/test", tags=["test"])

@router.post("/batches", status_code=status.HTTP_201_CREATED)
async def create_test_batch(
    sftp_path: str = "/test/scan.tif",
    current_user: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    batch = Batch(
        id=uuid.uuid4(),
        sftp_path=sftp_path,
        owner_id=current_user.id,
        status=BatchStatus.pending,
    )
    session.add(batch)
    await session.commit()
    await session.refresh(batch)
     # Convert status to string safely
    status_str = batch.status.value if hasattr(batch.status, "value") else str(batch.status)
    return {"id": str(batch.id), "sftp_path": batch.sftp_path, "status": status_str}



@router.post("/predictions", status_code=status.HTTP_201_CREATED)
async def create_test_prediction(
    batch_id: uuid.UUID,
    filename: str = "test_document.tif",
    label: DocumentLabel = DocumentLabel.resume,
    confidence: float = 0.95,
    current_user: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    batch = await session.get(Batch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    prediction = Prediction(
        id=uuid.uuid4(),
        batch_id=batch_id,
        filename=filename,
        label=label,
        confidence=confidence,
        overlay_path=None,
    )
    session.add(prediction)
    await session.commit()
    await session.refresh(prediction)
    # Convert label to string
    label_str = prediction.label.value if hasattr(prediction.label, "value") else str(prediction.label)
    return {
        "id": str(prediction.id),
        "batch_id": str(prediction.batch_id),
        "label": label_str,
        "confidence": prediction.confidence,
    }