from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from fastapi_users import schemas
from pydantic import Field


class UserRole(str, Enum):
    admin    = "admin"
    reviewer = "reviewer"
    auditor  = "auditor"


class UserRead(schemas.BaseUser[uuid.UUID]):
    role:       UserRole
    created_at: datetime


class UserCreate(schemas.BaseUserCreate):
    role: UserRole = UserRole.reviewer


class UserUpdate(schemas.BaseUserUpdate):
    role: UserRole | None = None
