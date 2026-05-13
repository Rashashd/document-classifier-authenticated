"""
Cross-team coupling: log_event() is called from batch_service and prediction_service on every relabel and batch state change.
"""

from __future__ import annotations

import uuid

from app.domain.audit import AuditEntryCreate


class AuditService:
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

        # TODO (D): implement the actual persistence logic, e.g. audit_repo.insert(entry)
        """
        _ = AuditEntryCreate(
            actor_id=actor_id,
            action=action,
            target=target,
            request_id=request_id,
        )
        return
