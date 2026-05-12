from __future__ import annotations

import uuid
from datetime import datetime

from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTableUUID
from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
