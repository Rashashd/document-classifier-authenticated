"""Business logic for Prediction entities. Owns transaction boundaries.

The inference worker calls :meth:`save_prediction_and_complete_batch` after a
document is classified — it persists the prediction, flips the parent batch
to ``done``, and invalidates the API caches that referenced it. Reviewer
endpoints use :meth:`relabel_prediction` and the read methods.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.batch import BatchStatus
from app.domain.prediction import PredictionCreate, PredictionRead, PredictionUpdate
from app.repositories.batch_repo import BatchRepository
from app.repositories.prediction_repo import PredictionRepository
from app.services.audit_service import AuditService
from app.services.cache_service import CacheService


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
        """Save a prediction without touching the parent batch's status.

        Kept as a separate method so future code paths (re-prediction, manual
        annotation) can persist a prediction without implying the batch is
        completed.
        """
        prediction = await self.repo.create(prediction_data)
        await self.session.commit()

        await self.cache.invalidate_batch(prediction_data.batch_id)
        await self.cache.invalidate_recent_predictions()

        return PredictionRead.model_validate(prediction)

    async def save_prediction_and_complete_batch(
        self, prediction_data: PredictionCreate
    ) -> PredictionRead:
        """Inference-worker entrypoint: save prediction + mark batch ``done``.

        Two transactions in sequence (``PredictionRepository.create`` commits
        internally, then ``BatchRepository.update_status`` is its own
        transaction). If the second fails, the prediction is durable and the
        next poll will see a stale batch status — acceptable for the worker.
        """
        prediction = await self.repo.create(prediction_data)
        await BatchRepository(self.session).update_status(
            prediction_data.batch_id, BatchStatus.done
        )
        await self.session.commit()

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
        """Reviewer endpoint. Logs an audit entry in the same transaction."""
        prediction = await self.repo.update(prediction_id, updates)
        if prediction:
            # Audit before commit so both rows land atomically.
            await self.audit.log_event(
                actor_id=actor_id,
                action="relabel",
                target=f"/predictions/{prediction_id}",
                request_id=request_id,
            )
            await self.session.commit()

            await self.cache.invalidate_batch(prediction.batch_id)
            await self.cache.invalidate_recent_predictions()

        return PredictionRead.model_validate(prediction) if prediction else None
