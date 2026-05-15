from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.session import get_async_session
from app.services.audit_service import AuditService
from app.services.auth_service import fastapi_users
from app.services.batch_service import BatchService
from app.services.cache_service import CacheService
from app.services.prediction_service import PredictionService

logger = structlog.get_logger(__name__)

# Resolves the bearer token and returns the current active user
# Return a FastAPI dependency that enforces Casbin RBAC
get_current_user = fastapi_users.current_user(active=True)


def get_audit_service(session: AsyncSession = Depends(get_async_session)) -> AuditService:
    return AuditService(session)


def get_batch_service(
    session: AsyncSession = Depends(get_async_session),
    cache: CacheService = Depends(CacheService),
    audit: AuditService = Depends(get_audit_service),
) -> BatchService:
    return BatchService(session, cache, audit)


def get_prediction_service(
    session: AsyncSession = Depends(get_async_session),
    cache: CacheService = Depends(CacheService),
    audit: AuditService = Depends(get_audit_service),
) -> PredictionService:
    return PredictionService(session, cache, audit)


def require_role(*roles: str) -> Callable[..., Awaitable[User]]:
    """
    Raises 401 if no valid token is present (via get_current_user).
    Raises 403 if the authenticated user's role is not permitted.
    """

    async def _dependency(
        request: Request,
        current_user: User = Depends(get_current_user),
    ) -> User:
        enforcer = request.app.state.enforcer
        allowed = any(enforcer.enforce(current_user.role, role) for role in roles)
        if not allowed:
            logger.warning(
                "access.denied",
                user_id=str(current_user.id),
                role=current_user.role,
                required_roles=list(roles),
                path=request.url.path,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions.",
            )
        return current_user

    return _dependency
