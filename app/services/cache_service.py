from __future__ import annotations

import uuid


class CacheService:
    """Stub for cache invalidation. Replace with actual fastapi-cache2."""
    async def invalidate_user(self, user_id: uuid.UUID) -> None:
        pass
    async def invalidate_batch(self, batch_id: uuid.UUID) -> None:
        pass
    async def invalidate_recent_predictions(self) -> None:
        pass