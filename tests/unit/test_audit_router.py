from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_user
from app.api.routers.audit import router as audit_router
from app.db.session import get_async_session
from tests.unit.conftest import _make_enforcer, _make_user


def _async_session_mock() -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.add = MagicMock()
    return session


def _make_app(current_user, enforcer: MagicMock, session: MagicMock) -> FastAPI:
    app = FastAPI()
    app.state.enforcer = enforcer
    app.include_router(audit_router)
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_async_session] = lambda: session
    return app


def _empty_audit_session() -> MagicMock:
    session = _async_session_mock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=mock_result)
    return session


@pytest.mark.anyio
async def test_audit_endpoint_admin_allowed():
    admin = _make_user("admin")
    app = _make_app(admin, _make_enforcer(True), _empty_audit_session())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/audit")

    assert resp.status_code == 200


@pytest.mark.anyio
async def test_audit_endpoint_auditor_allowed():
    auditor = _make_user("auditor")
    app = _make_app(auditor, _make_enforcer(True), _empty_audit_session())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/audit")

    assert resp.status_code == 200


@pytest.mark.anyio
async def test_audit_endpoint_reviewer_denied():
    reviewer = _make_user("reviewer")
    app = _make_app(reviewer, _make_enforcer(False), _async_session_mock())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/audit")

    assert resp.status_code == 403
