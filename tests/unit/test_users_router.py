from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_user
from app.api.routers.users import router as users_router
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
    app.include_router(users_router)
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_async_session] = lambda: session
    return app


@pytest.mark.anyio
async def test_me_hides_hashed_password():
    user = _make_user("reviewer")
    app = _make_app(user, _make_enforcer(True), _async_session_mock())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/users/me")

    assert resp.status_code == 200
    assert "hashed_password" not in resp.json()


@pytest.mark.anyio
async def test_role_toggle_self_demote_returns_409():
    admin = _make_user("admin")
    app = _make_app(admin, _make_enforcer(True), _async_session_mock())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/users/admin/{admin.id}/role",
            params={"role": "reviewer"},
        )

    assert resp.status_code == 409


@pytest.mark.anyio
async def test_role_toggle_writes_audit_row():
    admin = _make_user("admin")
    target = _make_user("reviewer")
    session = _async_session_mock()

    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=target)
    session.execute = AsyncMock(return_value=mock_result)

    app = _make_app(admin, _make_enforcer(True), session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/users/admin/{target.id}/role",
            params={"role": "auditor"},
        )

    assert resp.status_code == 200
    # flush called at least twice: once for update_role, once for audit insert
    assert session.flush.call_count >= 2
