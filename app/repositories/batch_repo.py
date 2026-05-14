# app/repositories/batch_repo.py
from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Batch
from app.domain import batch
from app.domain.batch import BatchStatus, BatchCreate, BatchUpdate


class BatchRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, batch_data: BatchCreate, owner_id: uuid.UUID) -> Batch:
        """Create a new batch row. Returns the ORM object."""
        batch = Batch(
            sftp_path=batch_data.sftp_path,
            owner_id=owner_id,
            status=BatchStatus.pending,
        )
        self.session.add(batch)
        await self.session.flush()   # assigns id, but doesn't commit
        return batch

    async def get(self, batch_id: uuid.UUID) -> Batch | None:
        stmt = select(Batch).where(Batch.id == batch_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_with_predictions(self, batch_id: uuid.UUID) -> Batch | None:
        """Eagerly load predictions for the batch (used in service to avoid N+1)."""
        from sqlalchemy.orm import selectinload
        stmt = select(Batch).where(Batch.id == batch_id).options(selectinload(Batch.predictions))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_owner(self, owner_id: uuid.UUID, skip: int = 0, limit: int = 100) -> Sequence[Batch]:
        stmt = select(Batch).where(Batch.owner_id == owner_id).offset(skip).limit(limit).order_by(Batch.created_at.desc())
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_status(self, batch_id: uuid.UUID, status: BatchStatus) -> Batch | None:
        """Update only the status. Returns the updated object or None if not found."""
        stmt = update(Batch).where(Batch.id == batch_id).values(status=status).returning(Batch)
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one_or_none()

    async def update(self, batch_id: uuid.UUID, updates: BatchUpdate) -> Batch | None:
        """Generic update using the domain Pydantic model."""
        data = updates.model_dump(exclude_unset=True)
        if not data:
            return await self.get(batch_id)
        
        updatable_columns = {"sftp_path", "status", "owner_id"}  # add any other actual columns
        filtered_data = {k: v for k, v in data.items() if k in updatable_columns}
        
        if not filtered_data:
            # Nothing to update (e.g., only document_count provided)
            return await self.get(batch_id)

        stmt = update(Batch).where(Batch.id == batch_id).values(**data).returning(Batch)
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one_or_none()
    
    # used by mahdi for worker:
    async def create_batch(self, sftp_path: str, owner_id: uuid.UUID) -> Batch:
        batch = Batch(sftp_path=sftp_path, owner_id=owner_id, status=BatchStatus.pending)
        self.session.add(batch)
        await self.session.flush()
        return batch