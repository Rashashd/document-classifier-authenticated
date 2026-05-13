"""
This is the exact shape of the message that sftp_ingest.py enqueues onto the Redis queue and that workers/inference.py deserialises on the other end. Both sides must import from here, never define this inline in worker code.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class InferenceJob(BaseModel):
    """Payload enqueued by sftp_ingest worker; consumed by inference worker."""

    job_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    batch_id: uuid.UUID
    blob_path: str = Field(
        description="MinIO object key for the uploaded document, e.g. 'batches/{batch_id}/scan.tif'"
    )
    filename: str = Field(description="Original filename from the SFTP drop.")
    enqueued_at: datetime = Field(
        description="UTC timestamp set by the ingest worker at enqueue time."
    )

    # Serialisation helpers used by both workers
    def to_rq_kwargs(self) -> dict:
        """Pass as **job.to_rq_kwargs() to q.enqueue()."""
        return {"kwargs": {"payload": self.model_dump_json()}}

    @classmethod
    def from_rq_kwargs(cls, payload: str) -> "InferenceJob":
        """Called inside the inference worker function."""
        return cls.model_validate_json(payload)
