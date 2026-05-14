"""DB session helpers.

The API uses ``get_async_session`` as a FastAPI dependency — the engine is
constructed once in lifespan and stored on ``app.state.engine``. Workers
build their own engine and open sessions directly.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_async_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency. Reads the engine from ``app.state.engine``.

    ``expire_on_commit=False`` keeps ORM attributes readable after commit
    without triggering a lazy SELECT outside the greenlet.
    """
    async with AsyncSession(request.app.state.engine, expire_on_commit=False) as session:
        yield session
