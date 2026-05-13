"""
Cross-team coupling: log_event() is called from batch_service and prediction_service on every relabel and batch state change.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.audit import AuditEntryCreate
from app.repositories.audit_repo import AuditRepository


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = AuditRepository(session)

    async def log_event(
        self,
        actor_id: uuid.UUID,
        action: str,
        target: str,
        request_id: str | None = None,
    ) -> None:
        """
        Record an audit event.
        actor_id   — UUID of the user performing the action (from JWT subject)
        action     — short verb, e.g. 'relabel', 'role_toggle', 'batch_status_change'
        target     — resource identifier, e.g. '/predictions/{id}' or '/batches/{id}'
        request_id — X-Request-ID from the structured logger (optional but recommended)
        """
        await self._repo.insert(
            AuditEntryCreate(
                actor_id=actor_id,
                action=action,
                target=target,
                request_id=request_id,
            )
        )
