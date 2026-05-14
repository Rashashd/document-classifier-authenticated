from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class BatchStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    done = "done"
    failed = "failed"


class BatchBase(BaseModel):
    sftp_path: str = Field(
        description="Original SFTP drop path, e.g. /drop/2026-05-12/scan.tif"
    )


class BatchCreate(BatchBase):
    pass  # owner_id injected by the service from the JWT subject


class BatchRead(BatchBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: BatchStatus
    # Nullable: scanner-originated batches have no JWT subject.
    owner_id: uuid.UUID | None
    document_count: int
    created_at: datetime
    updated_at: datetime


class BatchUpdate(BaseModel):
    # ``document_count`` is a computed @property on the ORM model
    # (len(predictions)) and therefore intentionally not updatable.
    status: BatchStatus | None = None


class BatchListResponse(BaseModel):
    """Paginated GET /batches envelope."""
    items: list[BatchRead]
    total: int
    skip: int
    limit: int
