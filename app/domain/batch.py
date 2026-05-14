from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class BatchStatus(str, Enum):
    pending    = "pending"
    processing = "processing"
    done       = "done"
    failed     = "failed"


class BatchBase(BaseModel):
    sftp_path: str = Field(description="Original SFTP drop path, e.g. /drop/2026-05-12/scan.tif")


class BatchCreate(BatchBase):
    pass  # owner_id injected by the service from the JWT subject


class BatchRead(BatchBase):
    model_config = ConfigDict(from_attributes=True)

    id:             uuid.UUID
    status:         BatchStatus
    owner_id:       uuid.UUID
    document_count: int
    created_at:     datetime
    updated_at:     datetime


class BatchUpdate(BaseModel):
    status:         BatchStatus | None = None
    #document_count: int | None        = None
