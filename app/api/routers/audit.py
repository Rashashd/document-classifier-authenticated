from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.db.models import User
from app.db.session import get_async_session
from app.domain.audit import AuditEntryRead
from app.repositories.audit_repo import AuditRepository

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditEntryRead])
async def list_audit_entries(
    _: Annotated[User, Depends(require_role("admin", "auditor"))],
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> list[AuditEntryRead]:
    repo = AuditRepository(session)
    rows = await repo.list_all()
    return [AuditEntryRead.model_validate(r) for r in rows]
