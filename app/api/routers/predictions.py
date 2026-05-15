from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi_cache.decorator import cache

from app.api.deps import get_current_user, get_prediction_service, require_role
from app.db.models import User
from app.domain.prediction import PredictionListResponse, PredictionRead, PredictionUpdate
from app.infra.blob import MinioBlobClient
from app.infra.vault import VaultClient
from app.services.prediction_service import PredictionService

_OVERLAY_PREFIX = "s3://documents/"

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.get("/recent", response_model=PredictionListResponse)
@cache(expire=60)  # cache for 60 seconds
async def list_recent_predictions(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    service: PredictionService = Depends(get_prediction_service),
) -> PredictionListResponse:
    items, total = await service.list_recent_predictions(skip=skip, limit=limit)
    return PredictionListResponse(items=items, total=total, skip=skip, limit=limit)


@router.patch("/{prediction_id}", response_model=PredictionRead)
async def relabel_prediction(
    prediction_id: uuid.UUID,
    updates: PredictionUpdate,
    request: Request,
    current_user: User = Depends(require_role("reviewer")),  # <-- authentication + role
    service: PredictionService = Depends(get_prediction_service),
) -> PredictionRead:
    # Fetch current prediction to check confidence
    pred = await service.get_prediction(prediction_id)
    if not pred:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prediction not found")
    
    if pred.confidence >= 0.7:
        logger.warning(
            "prediction.relabel_blocked",
            prediction_id=str(prediction_id),
            confidence=pred.confidence,
            actor_id=str(current_user.id),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot relabel predictions with confidence >= 0.7"
        )
    
    request_id = getattr(request.state, "request_id", None)
    
    updated = await service.relabel_prediction(
        prediction_id=prediction_id,
        updates=updates,
        actor_id=current_user.id,   # passed internally, not from user
        request_id=request_id,
    )
    
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prediction not found")
    return updated


@router.get("/{prediction_id}/overlay")
async def get_prediction_overlay(
    prediction_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    service: PredictionService = Depends(get_prediction_service),
) -> Response:
    """Proxy the overlay PNG from MinIO so the browser can display it with auth."""
    pred = await service.get_prediction(prediction_id)
    if not pred:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prediction not found")
    if not pred.overlay_path or not pred.overlay_path.startswith(_OVERLAY_PREFIX):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Overlay not available")

    object_key = pred.overlay_path[len(_OVERLAY_PREFIX):]

    vault: VaultClient = request.app.state.vault
    settings = request.app.state.settings
    minio_creds: dict[str, Any] = vault.get_secret(settings.vault_minio_path)  # type: ignore[no-any-return]
    blob = MinioBlobClient(
        endpoint=settings.minio_endpoint,
        access_key=minio_creds["access_key"],
        secret_key=minio_creds["secret_key"],
        secure=False,
    )
    image_bytes = blob.download_file(object_key)
    return Response(content=image_bytes, media_type="image/png")