"""Unit tests for app/api/routers/predictions.py.

Mocks PredictionService entirely. Covers route wiring + the
confidence-guard branch on `PATCH /predictions/{id}`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_user
from app.api.routers.predictions import (
    get_prediction_service,
    router as predictions_router,
)
from app.domain.prediction import DocumentLabel, PredictionRead
from app.services.prediction_service import PredictionService
from tests.unit.conftest import _make_enforcer, _make_user


def _make_prediction_read(*, confidence: float, batch_id: uuid.UUID | None = None) -> PredictionRead:
    return PredictionRead(
        id=uuid.uuid4(),
        batch_id=batch_id or uuid.uuid4(),
        filename="scan.tif",
        label=DocumentLabel.invoice,
        confidence=confidence,
        overlay_path=None,
        created_at=datetime.now(timezone.utc),
    )


def _make_app(current_user, enforcer, service_mock) -> FastAPI:
    app = FastAPI()
    app.state.enforcer = enforcer
    app.include_router(predictions_router)
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_prediction_service] = lambda: service_mock
    return app


@pytest.mark.anyio
async def test_list_recent_predictions_returns_list():
    """GET /predictions/recent → 200 with PredictionListResponse envelope."""
    user = _make_user("reviewer")
    preds = [_make_prediction_read(confidence=0.92), _make_prediction_read(confidence=0.55)]

    service = MagicMock()
    # Sara's service returns (items, total) tuple for pagination.
    service.list_recent_predictions = AsyncMock(return_value=(preds, 2))

    app = _make_app(user, _make_enforcer(True), service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/predictions/recent?skip=0&limit=25")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["limit"] == 25
    assert len(body["items"]) == 2
    service.list_recent_predictions.assert_awaited_once_with(skip=0, limit=25)


@pytest.mark.anyio
async def test_relabel_prediction_blocked_when_confidence_too_high():
    """PATCH /predictions/{id} → 403 when confidence ≥ 0.7 (brief's guardrail).

    The service's `relabel_prediction` should NOT be invoked — the route
    must return 403 *before* mutating anything.
    """
    reviewer = _make_user("reviewer")
    confident_pred = _make_prediction_read(confidence=0.95)

    service = MagicMock()
    service.get_prediction = AsyncMock(return_value=confident_pred)
    service.relabel_prediction = AsyncMock()

    app = _make_app(reviewer, _make_enforcer(True), service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            f"/predictions/{confident_pred.id}",
            json={"label": "resume"},
        )

    assert resp.status_code == 403
    service.relabel_prediction.assert_not_called()


@pytest.mark.anyio
async def test_relabel_prediction_writes_audit_entry():
    """PATCH /predictions/{id} happy path triggers AuditService.log_event(action='relabel').

    Uses a real ``PredictionService`` with mocked sub-components so the
    route → service → audit path is actually exercised. A future refactor
    that bypassed the service (e.g. raw repo.update from the route) would
    break this test even though all the other route tests would still pass.
    """
    reviewer = _make_user("reviewer")
    low_conf_pred = _make_prediction_read(confidence=0.4)
    updated_pred = _make_prediction_read(
        confidence=0.4, batch_id=low_conf_pred.batch_id
    )

    mock_session = MagicMock()
    mock_session.commit = AsyncMock()

    mock_cache = MagicMock()
    mock_cache.invalidate_batch = AsyncMock()
    mock_cache.invalidate_recent_predictions = AsyncMock()

    mock_audit = MagicMock()
    mock_audit.log_event = AsyncMock()

    service = PredictionService(mock_session, mock_cache, mock_audit)
    # Replace the repo instance the service built in __init__ so we can
    # control its return values without hitting a real DB.
    service.repo = MagicMock()
    service.repo.get = AsyncMock(return_value=low_conf_pred)
    service.repo.update = AsyncMock(return_value=updated_pred)

    app = _make_app(reviewer, _make_enforcer(True), service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            f"/predictions/{low_conf_pred.id}",
            json={"label": "resume"},
        )

    assert resp.status_code == 200
    mock_audit.log_event.assert_awaited_once()
    audit_kwargs = mock_audit.log_event.await_args.kwargs
    assert audit_kwargs["action"]   == "relabel"
    assert audit_kwargs["actor_id"] == reviewer.id
    assert audit_kwargs["target"]   == f"/predictions/{low_conf_pred.id}"
