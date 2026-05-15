from __future__ import annotations

from fastapi import FastAPI

from app.api.routers.audit import router as audit_router
from app.api.routers.auth import router as auth_router
from app.api.routers.batches import router as batches_router
from app.api.routers.health import router as health_router
from app.api.routers.predictions import router as predictions_router
from app.api.routers.users import router as users_router
from app.core.config import get_settings
from app.core.lifespan import lifespan


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_title,
        version=settings.app_version,
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(audit_router)
    app.include_router(batches_router)
    app.include_router(predictions_router)
    return app


app = create_app()
