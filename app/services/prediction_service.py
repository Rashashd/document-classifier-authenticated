"""Business logic for Prediction entities. Owns transaction boundaries.

After an inference job completes, the worker calls
``save_prediction_and_complete_batch`` to persist the prediction row,
flip the batch status to ``done``, and invalidate any API caches that
referenced the batch.
"""

from __future__ import annotations

from redis.asyncio import Redis as AsyncRedis
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Prediction
from app.domain.batch import BatchStatus
from app.domain.prediction import PredictionCreate
from app.infra.cache import CACHE_KEY_PREFIX
from app.repositories.batch_repo import BatchRepository
from app.repositories.prediction_repo import PredictionRepository


class PredictionService:
    """Save predictions and complete the parent batch atomically (best-effort).

    Cache invalidation is best-effort: a transient Redis failure must not
    roll back the persisted prediction.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        cache_redis: AsyncRedis | None = None,
    ) -> None:
        self._session = session
        self._prediction_repo = PredictionRepository(session)
        self._batch_repo = BatchRepository(session)
        self._cache_redis = cache_redis

    async def save_prediction_and_complete_batch(
        self,
        prediction_in: PredictionCreate,
    ) -> Prediction:
        """Persist the prediction, flip the batch to ``done``, invalidate caches.

        The repo's ``create`` commits its own transaction, so the status
        update runs as a second transaction. If the second one fails the
        prediction row is already durable — the worker's retry path will
        observe a stale batch status and re-attempt only the update.
        """
        prediction = await self._prediction_repo.create(prediction_in)
        await self._batch_repo.update_status(prediction_in.batch_id, BatchStatus.done)
        # batch_repo.update_status flushes but does not commit; without
        # this the status change rolls back when the session closes.
        await self._session.commit()
        await self._invalidate_batch_caches()
        return prediction

    async def _invalidate_batch_caches(self) -> None:
        """Delete every API cache entry. No-op if cache_redis was not supplied."""
        if self._cache_redis is None:
            return
        # The API caches GET /batches, GET /batches/{bid}, GET
        # /predictions/recent, etc. — all under the same fastapi-cache2
        # prefix. A coarse wipe is cheap on this workload and keeps the
        # invalidation rules from drifting from the cache-key conventions.
        pattern = f"{CACHE_KEY_PREFIX}:*"
        async for key in self._cache_redis.scan_iter(match=pattern):
            await self._cache_redis.delete(key)
