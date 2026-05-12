from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AuditEntryCreate(BaseModel):
    actor_id:   uuid.UUID
    action:     str = Field(description="Verb, e.g. 'relabel', 'role_toggle', 'login'")
    target:     str = Field(description="Resource URI or free-text, e.g. '/predictions/{id}'")
    request_id: str | None = Field(
        default=None,
        description="X-Request-ID propagated from the structured logger."
    )


class AuditEntryRead(AuditEntryCreate):
    model_config = ConfigDict(from_attributes=True)

    id:        uuid.UUID
    timestamp: datetime
