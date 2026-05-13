from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tests.unit.conftest import make_test_app


@pytest.mark.anyio
async def test_require_role_returns_401_for_missing_token(restrictive_enforcer):
    """No token supplied → fastapi-users raises 401 before Casbin is even checked."""
    app = make_test_app(current_user=None, enforcer=restrictive_enforcer)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/protected")

    assert response.status_code == 401


@pytest.mark.anyio
async def test_require_role_returns_403_for_wrong_role(reviewer_user, restrictive_enforcer):
    """Valid token but wrong role → Casbin enforce() returns False → 403."""
    app = make_test_app(current_user=reviewer_user, enforcer=restrictive_enforcer)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/protected")

    assert response.status_code == 403
