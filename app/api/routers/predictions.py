from __future__ import annotations

import uuid
from typing import Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_role
from app.db.session import get_async_session
from app.domain.prediction import PredictionRead, PredictionUpdate, PredictionListResponse
from app.db.models import User
from app.services.prediction_service import PredictionService
from app.services.cache_service import CacheService
from fastapi_cache.decorator import cache
from app.services.audit_service import AuditService

router = APIRouter(prefix="/predictions", tags=["predictions"])

def get_audit_service(session: AsyncSession = Depends(get_async_session)) -> AuditService:
    return AuditService(session)

def get_prediction_service(
    session: AsyncSession = Depends(get_async_session),
    cache: CacheService = Depends(CacheService),
    audit: AuditService = Depends(get_audit_service),
) -> PredictionService:
    return PredictionService(session, cache, audit)


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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot relabel predictions with confidence >= 0.7"
        )
    
    request_id = request.headers.get("X-Request-ID")
    
    updated = await service.relabel_prediction(
        prediction_id=prediction_id,
        updates=updates,
        actor_id=current_user.id,   # passed internally, not from user
        request_id=request_id,
    )
    
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prediction not found")
    return updated