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
    """GET /predictions/recent → 200, service called with the limit param."""
    user = _make_user("reviewer")
    preds = [_make_prediction_read(confidence=0.92), _make_prediction_read(confidence=0.55)]

    service = MagicMock()
    service.list_recent_predictions = AsyncMock(return_value=preds)

    app = _make_app(user, _make_enforcer(True), service)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/predictions/recent?limit=25")

    assert resp.status_code == 200
    assert len(resp.json()) == 2
    service.list_recent_predictions.assert_awaited_once_with(limit=25)


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
