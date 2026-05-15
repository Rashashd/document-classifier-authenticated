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
    """GET /batches → 200 with BatchListResponse envelope; service called with pagination."""
    user = _make_user("reviewer")
    batch_a = _make_batch_read(owner_id=uuid.uuid4())      # someone else's batch
    batch_b = _make_batch_read(owner_id=None)              # scanner-ingested

    service = MagicMock()
    # Sara's service returns (items, total) tuple for pagination.
    service.list_batches = AsyncMock(return_value=([batch_a, batch_b], 2))

    app = _make_app(user, _make_enforcer(True), service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/batches?skip=10&limit=20")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["skip"]  == 10
    assert body["limit"] == 20
    assert len(body["items"]) == 2
    service.list_batches.assert_awaited_once_with(skip=10, limit=20)


@pytest.mark.anyio
async def test_get_batch_returns_200_for_any_authenticated_role():
    """A reviewer can read any batch by id (incl. scanner-ingested NULL-owner)."""
    user = _make_user("reviewer")
    other_batch = _make_batch_read(owner_id=None)   # scanner batch

    service = MagicMock()
    service.get_batch = AsyncMock(return_value=other_batch)

    app = _make_app(user, _make_enforcer(True), service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/batches/{other_batch.id}")

    assert resp.status_code == 200
    assert resp.json()["id"] == str(other_batch.id)


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
