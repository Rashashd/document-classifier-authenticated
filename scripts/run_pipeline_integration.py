"""Run one pass of the ingest pipeline against the local compose stack.

Exercises the worker's ``process_one`` directly so we can verify the
SFTP → MinIO → Postgres wiring without needing Vault (the worker's
production entrypoint requires Vault for credential bootstrap, which
is not in the local compose yet).

Run from the host with the stack up::

    docker compose up -d db sftp minio redis
    uv run python scripts/run_pipeline_integration.py

Reads its own credentials from environment variables (local-dev only).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models import Batch
from app.infra.blob import MinioBlobClient
from app.infra.queue import RQClient
from app.infra.sftp import SFTPClient
from app.workers.sftp_ingest import configure_logging, process_one


TEST_NAME = "postgres-integration"


def _build_clients() -> tuple[SFTPClient, MinioBlobClient, RQClient, object]:
    sftp = SFTPClient(
        host=os.environ.get("SFTP_HOST", "localhost"),
        port=int(os.environ.get("SFTP_PORT", "2222")),
        username=os.environ["SFTP_USERNAME"],
        password=os.environ["SFTP_PASSWORD"],
    )
    blob = MinioBlobClient(
        endpoint=os.environ.get("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=False,
    )
    queue = RQClient(redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    # NullPool — see app/workers/sftp_ingest.py for rationale.
    engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
    return sftp, blob, queue, engine


async def _query_batches(engine) -> list[dict]:
    async with AsyncSession(engine) as session:
        rows = (await session.execute(select(Batch))).scalars().all()
        return [
            {
                "id":        str(b.id),
                "sftp_path": b.sftp_path,
                "owner_id":  None if b.owner_id is None else str(b.owner_id),
                "status":    str(b.status),
            }
            for b in rows
        ]


def main() -> int:
    configure_logging()
    log = structlog.get_logger("pipeline_integration")

    sftp, blob, queue, engine = _build_clients()
    sftp.connect()
    blob.startup()

    started_at = datetime.now(timezone.utc).isoformat()
    run_id     = str(uuid.uuid4())
    log.info("pipeline.start", run_id=run_id, test_name=TEST_NAME)

    listed = sftp.list_dir("/upload")
    log.info("pipeline.listed", count=len(listed), names=listed)

    per_file: list[dict] = []
    for name in listed:
        request_id = str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=request_id, filename=name)
        try:
            process_one(name, sftp, blob, queue, engine)
            per_file.append({"filename": name, "outcome": "processed", "error": None})
        except Exception as exc:  # noqa: BLE001 — boundary
            log.exception("pipeline.file_failed")
            per_file.append({"filename": name, "outcome": "error", "error": str(exc)})
        finally:
            structlog.contextvars.clear_contextvars()

    batches = asyncio.run(_query_batches(engine))
    log.info("pipeline.batches_after", count=len(batches))

    finished_at = datetime.now(timezone.utc).isoformat()
    record = {
        "test_name":   TEST_NAME,
        "run_id":      run_id,
        "started_at":  started_at,
        "finished_at": finished_at,
        "input_files": listed,
        "per_file":    per_file,
        "batches_in_db": batches,
        "expected_pending_batches": 1,
        "passed": len(batches) == 1 and batches[0]["status"] == "pending",
    }

    log_dir = Path("logs") / TEST_NAME
    log_dir.mkdir(parents=True, exist_ok=True)
    out_path = log_dir / f"{int(time.time())}_{run_id[:8]}.json"
    out_path.write_text(json.dumps(record, indent=2, sort_keys=True))
    log.info("pipeline.log_written", path=str(out_path), passed=record["passed"])

    return 0 if record["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
