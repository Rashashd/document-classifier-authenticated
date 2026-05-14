from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.prediction import PredictionCreate, PredictionRead, PredictionUpdate
from app.repositories.prediction_repo import PredictionRepository
from app.services.cache_service import CacheService
from app.services.audit_service import AuditService


class PredictionService:
    def __init__(
        self,
        session: AsyncSession,
        cache_service: CacheService,
        audit_service: AuditService,
    ) -> None:
        self.repo = PredictionRepository(session)
        self.cache = cache_service
        self.audit = audit_service
        self.session = session

    async def save_prediction(self, prediction_data: PredictionCreate) -> PredictionRead:
        """Called by Person C's inference worker after a document is classified."""
        prediction = await self.repo.create(prediction_data)
        # Commit the prediction to the database
        await self.session.commit()

        # Invalidate caches that include this batch or recent predictions list
        await self.cache.invalidate_batch(prediction_data.batch_id)
        await self.cache.invalidate_recent_predictions()

        return PredictionRead.model_validate(prediction)

    async def get_prediction(self, prediction_id: uuid.UUID) -> PredictionRead | None:
        prediction = await self.repo.get(prediction_id)
        return PredictionRead.model_validate(prediction) if prediction else None

    async def list_recent_predictions(self, limit: int = 100) -> Sequence[PredictionRead]:
        predictions = await self.repo.list_recent(limit)
        return [PredictionRead.model_validate(p) for p in predictions]

    async def relabel_prediction(
        self,
        prediction_id: uuid.UUID,
        updates: PredictionUpdate,
        actor_id: uuid.UUID,
        request_id: str | None = None,
    ) -> PredictionRead | None:
        """Used by reviewer endpoint. Logs an audit entry automatically."""
        prediction = await self.repo.update(prediction_id, updates)
        if prediction:
            # Write audit log BEFORE commit so it's in the same transaction
            await self.audit.log_event(
                actor_id=actor_id,
                action="relabel",
                target=f"/predictions/{prediction_id}",
                request_id=request_id,
            )
            # Commit both the prediction update and the audit entry
            await self.session.commit()

            # Invalidate affected caches
            await self.cache.invalidate_batch(prediction.batch_id)
            await self.cache.invalidate_recent_predictions()

        return PredictionRead.model_validate(prediction) if prediction else None