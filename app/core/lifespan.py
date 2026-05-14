from __future__ import annotations

import sys
from contextlib import asynccontextmanager

import casbin
import structlog
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.classifier.inference import (
    ClassifierArtifactError,
    assert_classifier_artifacts,
)
from app.core.config import get_settings
from app.db.models import CasbinRule
from app.infra.cache import init_redis_cache
from app.infra.vault import VaultClient

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings

    # 0. FastAPICache against Redis. The @cache decorators on
    #    /batches and /predictions/recent assert ``init`` was called;
    #    skipping this surfaces as a 500 on the first cached read.
    await init_redis_cache(settings.redis_url)

    # 1. Model artifacts. The classifier weights + card live on disk in
    #    the image (Git LFS materialises them at build time). Verifying
    #    presence + SHA-256 here means a corrupted/missing weight file
    #    fails boot rather than surfacing as a runtime 500 on first
    #    inference.
    try:
        assert_classifier_artifacts()
    except ClassifierArtifactError as exc:
        logger.critical("refuse_to_boot", reason="classifier_artifacts", error=str(exc))
        sys.exit(1)

    # 2. Database engine
    engine = create_async_engine(settings.database_url, echo=False)
    app.state.engine = engine

    # 3. Vault client + JWT secret
    try:
        vault = VaultClient(addr=settings.vault_addr, token=settings.vault_token)
        # vault_jwt_secret_path is the full KV v2 path e.g. "secret/data/jwt"
        # hvac expects the mount-relative path: "jwt"
        _, _, secret_path = settings.vault_jwt_secret_path.partition("/data/")
        secret = vault.get_secret(secret_path)
        jwt_secret: str = secret["secret"]
    except Exception as exc:
        logger.critical(
            "refuse_to_boot", reason="vault_unreachable_or_jwt_missing", error=str(exc)
        )
        sys.exit(1)

    app.state.vault = vault
    app.state.jwt_secret = jwt_secret

    # 4. Casbin enforcer + non-empty policy guard. The CSV load failing
    #    is structural (model.conf / policy.csv wrong); the table-empty
    #    branch catches a missing Alembic seed migration that would
    #    otherwise let unauthorised requests through silently.
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
            sys.exit(1)

    app.state.enforcer = enforcer

    logger.info("startup_complete")
    yield

    # Shutdown
    await engine.dispose()
    logger.info("shutdown_complete")
