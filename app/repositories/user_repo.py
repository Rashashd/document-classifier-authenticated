from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.domain.user import UserRole


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def update_role(self, user_id: uuid.UUID, new_role: UserRole) -> User | None:
        user = await self.get_by_id(user_id)
        if user is None:
            return None
        user.role = new_role.value
        await self._session.flush()
        return user

    async def count_admins(self) -> int:
        result = await self._session.execute(
            select(func.count()).where(User.role == UserRole.admin.value)
        )
        return result.scalar_one()
