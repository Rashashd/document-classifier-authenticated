"""DB session helpers.

The API uses ``get_async_session`` as a FastAPI dependency (engine is constructed once in lifespan, stored on ``app.state.engine``). Workers have no FastAPI app, so they build their own engine via
``make_async_engine`` and open sessions directly with ``AsyncSession(engine)``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_async_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency. Reads the engine from ``app.state.engine``."""
    async with AsyncSession(request.app.state.engine) as session:
        yield session