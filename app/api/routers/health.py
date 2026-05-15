"""Liveness probe.

Unauthenticated. If this returns 200 the FastAPI process is up and the
ASGI loop is serving. Backing-service health (Postgres / Redis / Vault)
is asserted at startup via the lifespan; once lifespan completes the
process either serves or has already exited 1.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
