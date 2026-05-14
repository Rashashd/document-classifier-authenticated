from __future__ import annotations

import sys
from contextlib import asynccontextmanager

import casbin
import structlog
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import get_settings
from app.db.models import CasbinRule
from app.infra.vault import VaultClient

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings

    # Must initialize FastAPI-Cache here + redis client 

    # 1. Database engine
    engine = create_async_engine(settings.database_url, echo=False)
    app.state.engine = engine

    # 2. Vault client + JWT secret
    try:
        vault = VaultClient(addr=settings.vault_addr, token=settings.vault_token)
        # vault_jwt_secret_path is the full KV v2 path e.g. "secret/data/jwt"
        # hvac expects the mount-relative path: "jwt"
        _, _, secret_path = settings.vault_jwt_secret_path.partition("/data/")
        secret = vault.get_secret(secret_path)
        jwt_secret: str = secret["secret"]
    except Exception as exc:
        logger.critical("refuse_to_boot", reason="vault_unreachable_or_jwt_missing", error=str(exc))
        sys.exit(1)

    app.state.vault = vault
    app.state.jwt_secret = jwt_secret

    # 3. Casbin enforcer + non-empty policy guard
    try:
        enforcer = casbin.AsyncEnforcer("app/casbin/model.conf", "app/casbin/policy.csv")
        await enforcer.load_policy()
    except Exception as exc:
        logger.critical("refuse_to_boot", reason="casbin_load_failed", error=str(exc))
        sys.exit(1)

    async with AsyncSession(engine) as session:
        result = await session.execute(select(CasbinRule))
        if result.scalars().first() is None:
            logger.critical("refuse_to_boot", reason="casbin_policy_table_empty")
            #sys.exit(1)

    app.state.enforcer = enforcer

    logger.info("startup_complete")
    yield

    # Shutdown
    await engine.dispose()
    logger.info("shutdown_complete")
