from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_role
from app.db.models import User
from app.db.session import get_async_session
from app.domain.user import UserRead, UserRole
from app.repositories.user_repo import UserRepository
from app.services.audit_service import AuditService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
async def get_me(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    return current_user


@router.get("", response_model=list[UserRead])
async def list_users(
    _: Annotated[User, Depends(require_role("admin"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[User]:
    result = await session.execute(select(User))
    return list(result.scalars().all())


@router.post("/admin/{user_id}/role", response_model=UserRead)
async def set_user_role(
    user_id: uuid.UUID,
    role: UserRole,
    current_user: Annotated[User, Depends(require_role("admin"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> User:
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Admins cannot change their own role.",
        )

    repo = UserRepository(session)
    updated = await repo.update_role(user_id, role)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    await AuditService(session).log_event(
        actor_id=current_user.id,
        action="role_toggle",
        target=f"/users/{user_id}/role",
    )
    await session.commit()
    logger.info("user.role_changed", target_user_id=str(user_id), new_role=role, actor_id=str(current_user.id))
    return updated
