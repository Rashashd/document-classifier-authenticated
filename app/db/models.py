from __future__ import annotations
import uuid
from datetime import datetime

from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTableUUID
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy import Enum as SAEnum
from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from app.domain.batch import BatchStatus


class Base(DeclarativeBase):
    pass

# Users (fastapi-users owns the columns; Rasha added role + created_at)

class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "users"

    # Redeclared for type-checker visibility — columns are owned by SQLAlchemyBaseUserTableUUID
    id: Mapped[uuid.UUID]
    email: Mapped[str]

    role: Mapped[str] = mapped_column(String(32), nullable=False, default="reviewer")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# Audit log

class AuditEntry(Base):
    __tablename__ = "audit_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target: Mapped[str] = mapped_column(String(512), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# Casbin rule table (the SQLAlchemy adapter expects this exact shape)

class CasbinRule(Base):
    __tablename__ = "casbin_rule"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ptype: Mapped[str] = mapped_column(String(255), nullable=False)
    v0: Mapped[str | None] = mapped_column(String(255), nullable=True)
    v1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    v2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    v3: Mapped[str | None] = mapped_column(String(255), nullable=True)
    v4: Mapped[str | None] = mapped_column(String(255), nullable=True)
    v5: Mapped[str | None] = mapped_column(String(255), nullable=True)


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

    predictions: Mapped[list["Prediction"]] = relationship(
    "Prediction", back_populates="batch", cascade="all, delete-orphan"
)


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    annotated_png_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    reviewer_corrected_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationship back to batch
    batch: Mapped["Batch"] = relationship("Batch", back_populates="predictions")

    