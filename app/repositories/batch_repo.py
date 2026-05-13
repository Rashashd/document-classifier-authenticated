"""Repository for the batches table. SQL only — no business logic, no HTTP."""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Batch
from app.domain.batch import BatchStatus, BatchUpdate


class BatchRepository:
    """SQL access for batches. Caller owns the session and transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_batch(self, sftp_path: str, owner_id: uuid.UUID | None) -> Batch:
        """Insert a PENDING batch row and return the ORM model.

        Uses ``flush`` (not ``commit``) so the row's auto-generated
        ``id`` / ``created_at`` are populated within the caller's
        transaction; the service owns the commit.
        """
        batch = Batch(
            sftp_path=sftp_path,
            owner_id=owner_id,
            status=BatchStatus.pending,
        )
        self._session.add(batch)
        await self._session.flush()
        return batch

    async def get(self, batch_id: uuid.UUID) -> Batch | None:
        stmt = select(Batch).where(Batch.id == batch_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_with_predictions(self, batch_id: uuid.UUID) -> Batch | None:
        """Eagerly load predictions for the batch (used in service to avoid N+1)."""
        from sqlalchemy.orm import selectinload
        stmt = select(Batch).where(Batch.id == batch_id).options(selectinload(Batch.predictions))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_owner(self, owner_id: uuid.UUID, skip: int = 0, limit: int = 100) -> Sequence[Batch]:
        stmt = select(Batch).where(Batch.owner_id == owner_id).offset(skip).limit(limit).order_by(Batch.created_at.desc())
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def update_status(self, batch_id: uuid.UUID, status: BatchStatus) -> Batch | None:
        """Update only the status. Returns the updated object or None if not found."""
        stmt = update(Batch).where(Batch.id == batch_id).values(status=status).returning(Batch)
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.scalar_one_or_none()

    async def update(self, batch_id: uuid.UUID, updates: BatchUpdate) -> Batch | None:
        """Generic update using the domain Pydantic model."""
        data = updates.model_dump(exclude_unset=True)
        if not data:
            return await self.get(batch_id)
        stmt = update(Batch).where(Batch.id == batch_id).values(**data).returning(Batch)
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.scalar_one_or_none()