"""Business logic for Batch entities. Owns transaction boundaries."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.batch_repo import BatchRepository


class BatchService:
    """Orchestrates Batch creation and state transitions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = BatchRepository(session)

    async def create_pending_batch(
        self, sftp_path: str, owner_id: uuid.UUID
    ) -> uuid.UUID:
        """Insert a PENDING batch and return its id.

        Returns just the id rather than a full BatchRead because the
        ingestion worker (the only current caller) only needs the id
        to attach to its enqueued inference job. Read endpoints in
        the API layer build BatchRead from the ORM row directly.
        """
        batch = await self._repo.create_batch(sftp_path=sftp_path, owner_id=owner_id)
        await self._session.commit()
        return batch.id
