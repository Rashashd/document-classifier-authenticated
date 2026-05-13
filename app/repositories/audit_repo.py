from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEntry
from app.domain.audit import AuditEntryCreate


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(self, entry: AuditEntryCreate) -> AuditEntry:
        row = AuditEntry(
            actor_id=entry.actor_id,
            action=entry.action,
            target=entry.target,
            request_id=entry.request_id,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_all(self, limit: int = 200) -> Sequence[AuditEntry]:
        result = await self._session.execute(
            select(AuditEntry).order_by(AuditEntry.timestamp.desc()).limit(limit)
        )
        return result.scalars().all()

    async def list_by_actor(self, actor_id: uuid.UUID, limit: int = 200) -> Sequence[AuditEntry]:
        result = await self._session.execute(
            select(AuditEntry)
            .where(AuditEntry.actor_id == actor_id)
            .order_by(AuditEntry.timestamp.desc())
            .limit(limit)
        )
        return result.scalars().all()
