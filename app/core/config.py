from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Ignore compose-only vars (POSTGRES_*, MINIO_*, SFTP_*) that
        # live alongside Settings fields in the shared .env file.
        extra="ignore",
    )

    # Database
    database_url: str = Field(
        description="Async SQLAlchemy URL, e.g. postgresql+asyncpg://user:pass@db/dbname"
    )

    # Vault
    vault_addr: str = Field(
        default="http://vault:8200",
        description="Vault server address",
    )
    vault_token: str = Field(
        description="Root or app-role token for Vault (injected by compose)",
    )
    vault_jwt_secret_path: str = Field(
        default="secret/data/jwt",
        description="KV v2 path where the JWT signing secret is stored",
    )

    # JWT (populated from Vault at startup, not read from env directly)
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60

    # Redis
    redis_url: str = Field(
        default="redis://redis:6379",
        description="Redis URL used by fastapi-cache2 and RQ",
    )

    # MinIO (used by demo endpoint; workers read from env directly)
    vault_minio_path: str = Field(
        default="minio",
        description="Vault KV v2 mount-relative path for MinIO credentials",
    )
    minio_endpoint: str = Field(
        default="minio:9000",
        description="MinIO endpoint (host:port)",
    )

    # App
    app_title: str = "Document Classifier"
    app_version: str = "0.1.0"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
