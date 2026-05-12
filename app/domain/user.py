from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class Role(str, Enum):
    admin   = "admin"
    reviewer = "reviewer"
    auditor  = "auditor"


class UserBase(BaseModel):
    email: EmailStr
    role:  Role = Role.reviewer


class UserCreate(UserBase):
    password: str = Field(min_length=8)


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id:         uuid.UUID
    is_active:  bool
    created_at: datetime


class UserUpdate(BaseModel):
    """Partial update, all fields optional."""
    role:      Role | None = None
    is_active: bool | None = None
