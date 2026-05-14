# app/repositories/prediction_repo.py
from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Prediction
from app.domain.prediction import PredictionCreate, PredictionUpdate


class PredictionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, prediction_data: PredictionCreate) -> Prediction:
        """Store a new prediction result."""
        prediction = Prediction(
            batch_id=prediction_data.batch_id,
            filename=prediction_data.filename,
            label=prediction_data.label,
            confidence=prediction_data.confidence,
            overlay_path=prediction_data.overlay_path,
        )
        self.session.add(prediction)
        await self.session.flush()
        return prediction

    async def get(self, prediction_id: uuid.UUID) -> Prediction | None:
        stmt = select(Prediction).where(Prediction.id == prediction_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_batch(self, batch_id: uuid.UUID) -> Sequence[Prediction]:
        stmt = select(Prediction).where(Prediction.batch_id == batch_id).order_by(Prediction.created_at)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_recent(self, skip: int = 0, limit: int = 100) -> Sequence[Prediction]:
        stmt = select(Prediction).order_by(Prediction.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update(self, prediction_id: uuid.UUID, updates: PredictionUpdate) -> Prediction | None:
        data = updates.model_dump(exclude_unset=True)
        if not data:
            return await self.get(prediction_id)
        stmt = update(Prediction).where(Prediction.id == prediction_id).values(**data).returning(Prediction)
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one_or_none()

    async def count_all(self) -> int:
        stmt = select(func.count()).select_from(Prediction)
        result = await self.session.execute(stmt)
        return result.scalar_one()