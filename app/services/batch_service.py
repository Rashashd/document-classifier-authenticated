"""Business logic for Batch entities. Owns transaction boundaries."""

from __future__ import annotations

import uuid
from typing import Sequence

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.batch_repo import BatchRepository
from app.domain.batch import BatchRead, BatchStatus, BatchUpdate
from app.services.cache_service import CacheService
from app.services.audit_service import AuditService

logger = structlog.get_logger(__name__)


class BatchService:
    """Orchestrates Batch creation and state transitions."""

    def __init__(
        self,
        session: AsyncSession,
        cache_service: CacheService | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self._session = session
        self._repo = BatchRepository(session)
        self._cache = cache_service
        self._audit = audit_service

    async def create_pending_batch(
        self, sftp_path: str, owner_id: uuid.UUID | None
    ) -> uuid.UUID:
        """Insert a PENDING batch and return its id.

        ``owner_id=None`` is valid for scanner-originated batches (no JWT
        subject). Cache invalidation is skipped in that case — nothing
        is keyed on a missing owner.
        """
        batch = await self._repo.create_batch(sftp_path=sftp_path, owner_id=owner_id)
        await self._session.commit()
        if self._cache and owner_id is not None:
            await self._cache.invalidate_user(owner_id)
        logger.info("batch.created", batch_id=str(batch.id), sftp_path=sftp_path)
        return batch.id

    # New methods for Person B
    async def get_batch(self, batch_id: uuid.UUID, user_id: uuid.UUID | None = None) -> BatchRead | None:
        batch = await self._repo.get_with_predictions(batch_id)
        if not batch:
            return None
        return BatchRead.model_validate(batch)

    async def list_batches(
        self, skip: int = 0, limit: int = 100
    ) -> tuple[Sequence[BatchRead], int]:
        """Return every batch + total count (owner-agnostic).

        Scanner-ingested batches have owner_id=NULL; filtering by owner
        would hide them from reviewers and admins.
        """
        batches, total = await self._repo.list_all(skip, limit)
        return [BatchRead.model_validate(b) for b in batches], total

    async def update_batch_status(
        self,
        batch_id: uuid.UUID,
        status: BatchStatus,
        actor_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> BatchRead | None:
        """Update only the status. Optionally log audit if actor provided."""
        batch = await self._repo.update_status(batch_id, status)
        if batch:
            await self._session.commit()
            if self._cache:
                await self._cache.invalidate_batch(batch_id)
                if batch.owner_id is not None:
                    await self._cache.invalidate_user(batch.owner_id)
            if self._audit and actor_id:
                await self._audit.log_event(
                    actor_id=actor_id,
                    action=f"batch_status_change_{status.value}",
                    target=f"/batches/{batch_id}",
                    request_id=request_id,
                )
            logger.info("batch.status_changed", batch_id=str(batch_id), status=status.value)
        return BatchRead.model_validate(batch) if batch else None

    async def update_batch(
        self,
        batch_id: uuid.UUID,
        updates: BatchUpdate,
        actor_id: uuid.UUID | None = None,
        request_id: str | None = None,
    ) -> BatchRead | None:
        """Generic batch update. If status changes and actor provided, log audit."""
        old_batch = await self._repo.get(batch_id)
        if not old_batch:
            return None
        old_status = old_batch.status
        batch = await self._repo.update(batch_id, updates)
        if batch:
            if self._audit and actor_id and updates.status is not None and updates.status != old_status:
                await self._audit.log_event(
                    actor_id=actor_id,
                    action=f"batch_status_change_{updates.status.value}",
                    target=f"/batches/{batch_id}",
                    request_id=request_id,
                )
            await self._session.commit()
            if self._cache:
                await self._cache.invalidate_batch(batch_id)
                if batch.owner_id is not None:
                    await self._cache.invalidate_user(batch.owner_id)
            logger.info("batch.updated", batch_id=str(batch_id))
        return BatchRead.model_validate(batch) if batch else None