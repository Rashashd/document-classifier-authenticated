from __future__ import annotations

from fastapi import FastAPI

from app.api.routers.auth import router as auth_router
from app.core.config import get_settings
from app.core.lifespan import lifespan


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_title,
        version=settings.app_version,
        lifespan=lifespan,
    )
    app.include_router(auth_router)
    return app


app = create_app()
