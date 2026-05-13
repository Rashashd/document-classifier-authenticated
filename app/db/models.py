"""SQLAlchemy ORM models. Imported only by repositories."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func

from app.domain.batch import BatchStatus


class Base(DeclarativeBase):
    """Declarative base shared by all ORM models in this project."""


class Batch(Base):
    """A scanner-originated upload tracked through its classification."""

    __tablename__ = "batches"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    filename:            Mapped[str] = mapped_column(String, nullable=False)
    original_minio_path: Mapped[str] = mapped_column(String, nullable=False)

    # Reuses the domain BatchStatus enum so the lowercase values
    # ("pending", "processing", ...) match what the API serialises.
    status: Mapped[BatchStatus] = mapped_column(
        SAEnum(BatchStatus, name="batch_status"),
        nullable=False,
        default=BatchStatus.pending,
        server_default=BatchStatus.pending.value,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
