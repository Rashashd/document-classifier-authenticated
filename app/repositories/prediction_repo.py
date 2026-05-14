"""Repository for the predictions table. SQL only — no business logic, no HTTP."""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Prediction
from app.domain.prediction import PredictionCreate, PredictionUpdate


class PredictionRepository:
    """SQL access for predictions. Caller owns the session and transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, data: PredictionCreate) -> Prediction:
        new_prediction = Prediction(**data.model_dump())
        self.session.add(new_prediction)
        await self.session.commit()
        await self.session.refresh(new_prediction)
        return new_prediction

    async def get(self, prediction_id: uuid.UUID) -> Prediction | None:
        stmt = select(Prediction).where(Prediction.id == prediction_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_batch(self, batch_id: uuid.UUID) -> Sequence[Prediction]:
        stmt = (
            select(Prediction)
            .where(Prediction.batch_id == batch_id)
            .order_by(Prediction.created_at)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_recent(
        self, skip: int = 0, limit: int = 100
    ) -> Sequence[Prediction]:
        stmt = (
            select(Prediction)
            .order_by(Prediction.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update(
        self, prediction_id: uuid.UUID, updates: PredictionUpdate
    ) -> Prediction | None:
        """Reviewer relabel. Caller commits."""
        data = updates.model_dump(exclude_unset=True)
        if not data:
            return await self.get(prediction_id)
        stmt = (
            update(Prediction)
            .where(Prediction.id == prediction_id)
            .values(**data)
            .returning(Prediction)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one_or_none()

    async def count_all(self) -> int:
        stmt = select(func.count()).select_from(Prediction)
        result = await self.session.execute(stmt)
        return result.scalar_one()
