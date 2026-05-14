from __future__ import annotations

import uuid
from typing import Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_role
from app.db.session import get_async_session
from app.db.models import User   # <-- changed
from app.domain.batch import BatchRead, BatchUpdate, BatchListResponse
from app.services.batch_service import BatchService
from app.services.cache_service import CacheService
from fastapi_cache.decorator import cache
from app.services.audit_service import AuditService


router = APIRouter(prefix="/batches", tags=["batches"])


def get_audit_service(session: AsyncSession = Depends(get_async_session)) -> AuditService:
    return AuditService(session)


def get_batch_service(
    session: AsyncSession = Depends(get_async_session),
    cache: CacheService = Depends(CacheService),
    audit: AuditService = Depends(get_audit_service),
) -> BatchService:
    return BatchService(session, cache, audit)


@router.get("", response_model=BatchListResponse)
@cache(expire=60)  # cache for 60 seconds
async def list_batches(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    current_user: User = Depends(get_current_user),  # still authenticated, but not used
    service: BatchService = Depends(get_batch_service),
) -> BatchListResponse:
    items, total = await service.list_batches(skip=skip, limit=limit)
    return BatchListResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/{batch_id}", response_model=BatchRead)
@cache(expire=60)  # cache for 60 seconds
async def get_batch(
    request: Request,
    batch_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: BatchService = Depends(get_batch_service),
) -> BatchRead:
    batch = await service.get_batch(batch_id, user_id=current_user.id)
    if not batch:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
    if batch.owner_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions")
    return batch


@router.patch("/{batch_id}", response_model=BatchRead)
async def update_batch(
    batch_id: uuid.UUID,
    updates: BatchUpdate,
    request: Request,
    current_user: User = Depends(require_role("admin")),
    service: BatchService = Depends(get_batch_service),
) -> BatchRead:
    request_id = request.headers.get("X-Request-ID")
    updated = await service.update_batch(
        batch_id,
        updates,
        actor_id=current_user.id,
        request_id=request_id,
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")
    return updated