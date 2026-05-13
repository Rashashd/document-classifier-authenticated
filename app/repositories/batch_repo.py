"""Repository for the batches table. SQL only — no business logic, no HTTP."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Batch
from app.domain.batch import BatchStatus


class BatchRepository:
    """SQL access for batches. Caller owns the session and transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_batch(self, sftp_path: str, owner_id: uuid.UUID) -> Batch:
        """Insert a PENDING batch row and return the ORM model.

        Uses ``flush`` (not ``commit``) so the row's auto-generated
        ``id`` / ``created_at`` are populated within the caller's
        transaction; the service owns the commit.
        """
        batch = Batch(
            sftp_path=sftp_path,
            owner_id=owner_id,
            status=BatchStatus.pending,
        )
        self._session.add(batch)
        await self._session.flush()
        return batch
