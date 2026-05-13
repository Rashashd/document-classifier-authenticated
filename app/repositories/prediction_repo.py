from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Prediction
from app.domain.prediction import PredictionCreate, PredictionUpdate

class PredictionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, data: PredictionCreate) -> Prediction:
        new_prediction = Prediction(**data.model_dump())
        self.session.add(new_prediction)
        await self.session.commit()
        await self.session.refresh(new_prediction)
        return new_prediction

    async def get_by_id(self, prediction_id: UUID) -> Prediction | None:
        result = await self.session.execute(
            select(Prediction).where(Prediction.id == prediction_id)
        )
        return result.scalars().first()

    async def get_by_batch(self, batch_id: UUID) -> list[Prediction]:
        """Useful for the API to show all results for one batch."""
        result = await self.session.execute(
            select(Prediction).where(Prediction.batch_id == batch_id)
        )
        return list(result.scalars().all())

    async def update(self, prediction_id: UUID, data: PredictionUpdate) -> Prediction | None:
        """Used for 'Relabeling' by an analyst."""
        prediction = await self.get_by_id(prediction_id)
        if not prediction:
            return None
        
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(prediction, key, value)
            
        await self.session.commit()
        await self.session.refresh(prediction)
        return prediction