from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Batch
from app.domain.batch import BatchCreate, BatchUpdate

class BatchRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: BatchCreate, owner_id: UUID) -> Batch:
        """Converts Pydantic 'BatchCreate' to SQLAlchemy 'Batch' model and saves."""
        new_batch = Batch(
            **data.model_dump(),
            owner_id=owner_id
        )
        self.session.add(new_batch)
        await self.session.commit()
        await self.session.refresh(new_batch)
        return new_batch

    async def get_by_id(self, batch_id: UUID) -> Batch | None:
        """Fetches a single batch. Returns None if not found."""
        result = await self.session.execute(
            select(Batch).where(Batch.id == batch_id)
        )
        return result.scalars().first()

    async def list_all(self, owner_id: UUID | None = None) -> list[Batch]:
        """Lists batches, optionally filtered by owner."""
        query = select(Batch)
        if owner_id:
            query = query.where(Batch.owner_id == owner_id)
        
        result = await self.session.execute(query.order_by(Batch.created_at.desc()))
        return list(result.scalars().all())

    async def update(self, batch_id: UUID, data: BatchUpdate) -> Batch | None:
        """Updates batch status or document count."""
        batch = await self.get_by_id(batch_id)
        if not batch:
            return None
        
        # Only update fields that were actually provided in the request
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(batch, key, value)
        
        await self.session.commit()
        await self.session.refresh(batch)
        return batch