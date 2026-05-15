"""Shared utilities for worker processes."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog

from app.infra.blob import MinioBlobClient
from app.infra.vault import VaultClient


def get_vault_client() -> VaultClient:
    """Read VAULT_ADDR and VAULT_TOKEN from env, exit on failure."""
    addr = os.environ.get("VAULT_ADDR", "http://vault:8200")
    token = os.environ.get("VAULT_TOKEN")
    if not token:
        structlog.get_logger().critical("vault.boot.missing_token")
        sys.exit(1)
    return VaultClient(addr=addr, token=token)


def configure_logging() -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    structlog.configure(
        processors=shared + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processor=structlog.processors.JSONRenderer(),
    )
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def build_blob(creds: dict[str, Any]) -> MinioBlobClient:
    return MinioBlobClient(
        endpoint=os.environ.get("MINIO_ENDPOINT", "minio:9000"),
        access_key=creds["access_key"],
        secret_key=creds["secret_key"],
        secure=False,
    )
