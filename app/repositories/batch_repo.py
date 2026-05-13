"""Repository for the batches table. SQL only — no business logic, no HTTP."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Batch
from app.domain.batch import BatchStatus


class BatchRepository:
    """SQL access for batches. Caller owns the session and the transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_batch(self, filename: str, minio_path: str) -> Batch:
        """Insert a PENDING batch row and return the ORM model.

        Uses ``flush`` (not ``commit``) so the row's auto-generated
        ``id`` / ``created_at`` are populated within the caller's
        transaction. The service owns the commit.
        """
        batch = Batch(
            filename=filename,
            original_minio_path=minio_path,
            status=BatchStatus.pending,
        )
        self._session.add(batch)
        self._session.flush()
        return batch
