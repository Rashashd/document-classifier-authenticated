from __future__ import annotations

import os

# config.py runs `settings = get_settings()` at module level, which requires
# DATABASE_URL. Set a placeholder before any app import so collection succeeds
# on machines whose .env only has compose-level POSTGRES_* vars.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost:5432/test",
)

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from app.core.lifespan import lifespan


def _mock_settings() -> MagicMock:
    s = MagicMock()
    s.redis_url = "redis://localhost:6379"
    s.vault_addr = "http://vault:8200"
    s.vault_token = "test-token"
    s.vault_jwt_secret_path = "secret/data/jwt"
    s.database_url = "postgresql+asyncpg://test:test@localhost/test"
    s.jwt_algorithm = "HS256"
    s.jwt_access_token_expire_minutes = 60
    s.app_title = "Test"
    s.app_version = "0.0.0"
    return s


def _make_mock_engine() -> MagicMock:
    engine = MagicMock()
    engine.dispose = AsyncMock()
    return engine


@pytest.mark.anyio
async def test_vault_unreachable_at_startup_exits_nonzero() -> None:
    """API refuses to boot if Vault is unreachable at startup."""
    with (
        patch("app.core.lifespan.get_settings", return_value=_mock_settings()),
        patch("app.core.lifespan.init_redis_cache", new_callable=AsyncMock),
        patch("app.core.lifespan.assert_classifier_artifacts"),
        patch("app.core.lifespan.create_async_engine", return_value=_make_mock_engine()),
        patch("app.core.lifespan.VaultClient") as mock_vault_cls,
    ):
        mock_vault_cls.return_value.get_secret.side_effect = RuntimeError("connection refused")

        with pytest.raises(SystemExit) as exc_info:
            async with lifespan(FastAPI()):
                pass  # pragma: no cover

        assert exc_info.value.code == 1


@pytest.mark.anyio
async def test_casbin_policy_empty_at_startup_exits_nonzero() -> None:
    """API refuses to boot if the casbin_rule table is empty after migrations."""
    mock_vault = MagicMock()
    mock_vault.get_secret.return_value = {"secret": "test-jwt-secret"}

    mock_enforcer = MagicMock()
    mock_enforcer.load_policy = AsyncMock()

    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = None

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_session_cm = MagicMock()
    mock_session_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.core.lifespan.get_settings", return_value=_mock_settings()),
        patch("app.core.lifespan.init_redis_cache", new_callable=AsyncMock),
        patch("app.core.lifespan.assert_classifier_artifacts"),
        patch("app.core.lifespan.create_async_engine", return_value=_make_mock_engine()),
        patch("app.core.lifespan.VaultClient", return_value=mock_vault),
        patch("app.core.lifespan.casbin.AsyncEnforcer", return_value=mock_enforcer),
        patch("app.core.lifespan.AsyncSession", return_value=mock_session_cm),
    ):
        with pytest.raises(SystemExit) as exc_info:
            async with lifespan(FastAPI()):
                pass  # pragma: no cover

        assert exc_info.value.code == 1
