from __future__ import annotations

import uuid

import structlog
from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.session import get_async_session

logger = structlog.get_logger(__name__)


# Database adapter
async def get_user_db(session: AsyncSession = Depends(get_async_session)):
    yield SQLAlchemyUserDatabase(session, User)


# User manager
class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    """Thin user manager. JWT secret is injected at startup via app.state."""

    async def on_after_register(
        self, user: User, request: Request | None = None
    ) -> None:
        logger.info("user.registered", user_id=str(user.id), email=user.email)


def _get_jwt_secret(request: Request) -> str:
    return request.app.state.jwt_secret


async def get_user_manager(
    request: Request,
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
):
    manager = UserManager(user_db)
    manager.reset_password_token_secret = _get_jwt_secret(request)
    manager.verification_token_secret = _get_jwt_secret(request)
    yield manager


# JWT strategy (secret resolved from app.state at runtime)


def get_jwt_strategy(request: Request) -> JWTStrategy:
    return JWTStrategy(
        secret=_get_jwt_secret(request),
        lifetime_seconds=request.app.state.settings.jwt_access_token_expire_minutes
        * 60,
        algorithm=request.app.state.settings.jwt_algorithm,
    )


bearer_transport = BearerTransport(tokenUrl="/auth/login")

auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,  # type: ignore[arg-type]
)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])
