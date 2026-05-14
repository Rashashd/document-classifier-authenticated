from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_async_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """
    The engine is created once in lifespan and stored on app.state.engine.
    FastAPI injects Request automatically, callers just use Depends(get_async_session).
    """
    async with AsyncSession(request.app.state.engine, expire_on_commit=False) as session:
        yield session