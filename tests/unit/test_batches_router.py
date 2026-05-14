"""Unit tests for app/api/routers/batches.py.

Mocks the BatchService entirely so these run in milliseconds. They cover
the route's wiring (auth dependency, role enforcement, response model
construction, status-code translation) — NOT the service or repo logic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_user
from app.api.routers.batches import get_batch_service, router as batches_router
from app.domain.batch import BatchRead, BatchStatus
from tests.unit.conftest import _make_enforcer, _make_user


def _make_batch_read(owner_id: uuid.UUID | None = None) -> BatchRead:
    now = datetime.now(timezone.utc)
    return BatchRead(
        id=uuid.uuid4(),
        sftp_path="/upload/scan.tif",
        owner_id=owner_id,
        status=BatchStatus.pending,
        document_count=0,
        created_at=now,
        updated_at=now,
    )


def _make_app(current_user, enforcer, service_mock) -> FastAPI:
    app = FastAPI()
    app.state.enforcer = enforcer
    app.include_router(batches_router)
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_batch_service] = lambda: service_mock
    return app


@pytest.mark.anyio
async def test_list_batches_returns_paginated_results():
    """GET /batches → 200, calls service.list_batches with the user's id."""
    user = _make_user("reviewer")
    batch_a = _make_batch_read(owner_id=user.id)
    batch_b = _make_batch_read(owner_id=user.id)

    service = MagicMock()
    service.list_batches = AsyncMock(return_value=[batch_a, batch_b])

    app = _make_app(user, _make_enforcer(True), service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/batches?skip=10&limit=20")

    assert resp.status_code == 200
    assert len(resp.json()) == 2
    service.list_batches.assert_awaited_once_with(owner_id=user.id, skip=10, limit=20)


@pytest.mark.anyio
async def test_get_batch_returns_403_when_not_owner_and_not_admin():
    """A reviewer querying someone else's batch by id gets 403."""
    user = _make_user("reviewer")
    someone_else = uuid.uuid4()
    other_batch = _make_batch_read(owner_id=someone_else)

    service = MagicMock()
    service.get_batch = AsyncMock(return_value=other_batch)

    app = _make_app(user, _make_enforcer(True), service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/batches/{other_batch.id}")

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_update_batch_requires_admin_role():
    """PATCH /batches/{id} as a non-admin → 403 from require_role enforcement."""
    reviewer = _make_user("reviewer")
    # Casbin enforcer denies — request never reaches the service.
    service = MagicMock()
    service.update_batch = AsyncMock()

    app = _make_app(reviewer, _make_enforcer(False), service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(f"/batches/{uuid.uuid4()}", json={"status": "done"})

    assert resp.status_code == 403
    service.update_batch.assert_not_called()
