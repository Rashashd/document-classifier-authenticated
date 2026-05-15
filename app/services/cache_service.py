from __future__ import annotations

import uuid

from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend


class CacheService:
    """Lazy cache service – only initializes backend when needed."""

    def __init__(self) -> None:
        self._backend: RedisBackend | None = None

    @property
    def backend(self) -> RedisBackend | None:
        if self._backend is None:
            try:
                self._backend = FastAPICache.get_backend()  # type: ignore[assignment]
            except AssertionError:
                # FastAPICache.init() not called – cache disabled
                return None
        return self._backend

    async def invalidate_user(self, user_id: uuid.UUID) -> None:
        if not self.backend:
            return
        pattern = f"*user:{user_id}*"
        await self._delete_by_pattern(pattern)

    async def invalidate_batch(self, batch_id: uuid.UUID) -> None:
        if not self.backend:
            return
        pattern1 = f"*/batches/{batch_id}*"
        pattern2 = "*/batches?*"
        await self._delete_by_pattern(pattern1)
        await self._delete_by_pattern(pattern2)

    async def invalidate_recent_predictions(self) -> None:
        if not self.backend:
            return
        pattern = "*/predictions/recent*"
        await self._delete_by_pattern(pattern)

    async def _delete_by_pattern(self, pattern: str) -> None:
        backend = self.backend
        if not backend or not isinstance(backend, RedisBackend):
            return
        client = backend.redis
        cursor = 0
        while True:
            cursor, keys = await client.scan(cursor, match=pattern, count=100)
            if keys:
                await client.delete(*keys)
            if cursor == 0:
                break