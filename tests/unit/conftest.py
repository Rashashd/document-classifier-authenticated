from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi_cache import FastAPICache
from typing import Annotated

from app.api.deps import get_current_user, require_role
from app.db.models import User


@pytest.fixture(autouse=True)
def _reset_fastapi_cache():
    """Init FastAPICache with an in-memory backend for every unit test.

    Sara's @cache decorators on /batches and /predictions/recent need
    ``FastAPICache.get_prefix()`` to return non-None, or the request
    handler asserts at import-time-but-late. The e2e integration test
    can also leave a closed AsyncRedis behind in the singleton; resetting
    then re-initialising clears that state.
    """
    from fastapi_cache.backends.inmemory import InMemoryBackend

    FastAPICache.reset()
    FastAPICache.init(InMemoryBackend(), prefix="unit-test")
    yield
    FastAPICache.reset()


async def _raise_401():
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")


def _make_user(role: str) -> User:
    user = User()
    user.id = uuid.uuid4()
    user.email = f"{role}@example.com"
    user.hashed_password = "hashed"
    user.is_active = True
    user.is_superuser = False
    user.is_verified = True
    user.role = role
    user.created_at = datetime.now(timezone.utc)
    return user


def _make_enforcer(allows: bool) -> MagicMock:
    enforcer = MagicMock()
    enforcer.enforce = MagicMock(return_value=allows)
    return enforcer


@pytest.fixture
def admin_user() -> User:
    return _make_user("admin")


@pytest.fixture
def reviewer_user() -> User:
    return _make_user("reviewer")


@pytest.fixture
def permissive_enforcer() -> MagicMock:
    return _make_enforcer(True)


@pytest.fixture
def restrictive_enforcer() -> MagicMock:
    return _make_enforcer(False)


def make_test_app(current_user: User | None, enforcer: MagicMock) -> FastAPI:
    """Minimal FastAPI app with a single admin-only endpoint for testing deps."""
    app = FastAPI()
    app.state.enforcer = enforcer

    @app.get("/protected")
    async def protected(user: Annotated[User, Depends(require_role("admin"))]):
        return {"role": user.role}

    if current_user is not None:
        app.dependency_overrides[get_current_user] = lambda: current_user
    else:
        app.dependency_overrides[get_current_user] = _raise_401

    return app
