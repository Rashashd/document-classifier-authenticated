"""End-to-end smoke test for the full docker-compose stack.

Exercises the production code path with zero mocking:

  1. POST /auth/register + POST /auth/login → JWT.
  2. GET /batches with the JWT — primes the 60s @cache layer.
  3. SFTP-drop a generated TIFF into /upload — worker-ingest picks it
     up via polling, uploads to MinIO, inserts a PENDING batch, and
     enqueues an InferenceJob.
  4. Poll GET /batches every 2 s. The cache stays warm for 60 s, so a
     new batch only becomes visible once the inference worker's
     ``CacheService.invalidate_batches()`` call fires — proving the
     ingest → inference → cache-invalidation chain end-to-end.
  5. Once a *new* batch (not in the pre-drop set) shows up with the
     terminal ``done`` status, query GET /predictions/recent and assert
     a prediction with the dropped filename is present.

Exit codes (run as ``python tests/smoke/test_full_stack.py``):
  * ``0`` — full chain observed within ``POLL_TIMEOUT_S``.
  * ``1`` — timeout or any other failure.

The brief's wording says "status == completed". The actual enum value
in ``app/domain/batch.py`` is ``done`` (the terminal success state); we
assert against the real value and surface this in the smoke output.
"""

from __future__ import annotations

import io
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import httpx
import paramiko
from PIL import Image


# ---------------------------------------------------------------------
# Compose-stack endpoints. All host-side bindings — match docker-compose.yml.
# ---------------------------------------------------------------------
API_BASE_URL: str = os.environ.get("API_BASE_URL", "http://localhost:8000")

SFTP_HOST: str = os.environ.get("SFTP_HOST", "localhost")
SFTP_PORT: int = int(os.environ.get("SFTP_PORT", "2222"))
SFTP_USER: str = os.environ.get("SFTP_USER", "scanner")
# Mirrors test_e2e_mocked_pipeline.py: fall through to compose-default password.
SFTP_PASSWORD: str = os.environ.get("SFTP_PASSWORD", "change-me-in-production")

UPLOAD_DIR: str = "/upload"

# ---------------------------------------------------------------------
# Test-user credentials. Re-used across runs — the smoke test treats a
# 400 (email already registered) as a no-op and proceeds to login.
# ---------------------------------------------------------------------
SMOKE_EMAIL: str = "smoke@example.com"
SMOKE_PASSWORD: str = "smoke-pw-not-a-secret"

# ---------------------------------------------------------------------
# Polling. The first inference can be slow (cold model load + a real
# ConvNeXt forward pass on CPU). 120 s gives headroom on a cold runner.
# ---------------------------------------------------------------------
POLL_INTERVAL_S: float = 2.0
POLL_TIMEOUT_S: float = 120.0


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def log(msg: str) -> None:
    """One-line progress with a UTC timestamp — visible in CI logs."""
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[smoke {ts}] {msg}", flush=True)


def make_tiff_bytes() -> bytes:
    """Produce a deterministic single-page TIFF that PIL + the worker
    happily accept. Contents don't matter — the model will classify
    *something* and the worker writes the result through.
    """
    image = Image.new("RGB", (224, 224), color=(255, 255, 255))
    buf = io.BytesIO()
    image.save(buf, format="TIFF")
    return buf.getvalue()


def register_user(client: httpx.Client) -> None:
    """Idempotent: a 201 (newly created) and a 400 (already exists)
    both leave the smoke test ready to log in.
    """
    resp = client.post(
        "/auth/register",
        json={"email": SMOKE_EMAIL, "password": SMOKE_PASSWORD},
    )
    if resp.status_code == 201:
        log(f"registered smoke user {SMOKE_EMAIL}")
    elif resp.status_code == 400:
        log(f"smoke user already exists ({SMOKE_EMAIL}) — proceeding")
    else:
        raise RuntimeError(
            f"register failed: HTTP {resp.status_code} body={resp.text!r}"
        )


def login(client: httpx.Client) -> str:
    """POST /auth/login — fastapi-users issues a JWT, form-encoded."""
    resp = client.post(
        "/auth/login",
        data={"username": SMOKE_EMAIL, "password": SMOKE_PASSWORD},
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"login failed: HTTP {resp.status_code} body={resp.text!r}"
        )
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"login response missing access_token: {resp.text!r}")
    log("acquired JWT")
    return token


def list_batches(client: httpx.Client) -> list[dict]:
    resp = client.get("/batches?skip=0&limit=100")
    resp.raise_for_status()
    return resp.json().get("items", [])


def list_recent_predictions(client: httpx.Client) -> list[dict]:
    resp = client.get("/predictions/recent?skip=0&limit=100")
    resp.raise_for_status()
    return resp.json().get("items", [])


def sftp_drop(filename: str, content: bytes) -> None:
    """Upload ``content`` to ``/upload/{filename}`` over SFTP."""
    transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
    try:
        transport.connect(username=SFTP_USER, password=SFTP_PASSWORD)
        sftp = paramiko.SFTPClient.from_transport(transport)
        assert sftp is not None  # paramiko returns Optional
        try:
            sftp.chdir(UPLOAD_DIR)
            with sftp.file(filename, "wb") as remote:
                remote.write(content)
        finally:
            sftp.close()
    finally:
        transport.close()


def find_done_batch_for(
    batches: Iterable[dict], pre_drop_ids: set[str]
) -> dict | None:
    """Return the first batch that wasn't there before the drop AND is
    in the terminal ``done`` state. ``None`` means keep polling.
    """
    for batch in batches:
        if batch.get("id") in pre_drop_ids:
            continue
        if batch.get("status") == "done":
            return batch
    return None


# ---------------------------------------------------------------------
# Smoke flow
# ---------------------------------------------------------------------
def run_smoke() -> int:
    drop_name = f"smoke-{uuid.uuid4().hex[:8]}.tif"

    with httpx.Client(base_url=API_BASE_URL, timeout=10.0) as client:

        # 0) liveness
        h = client.get("/health")
        if h.status_code != 200 or h.json().get("status") != "ok":
            log(f"health check failed: HTTP {h.status_code} body={h.text!r}")
            return 1
        log("GET /health → ok")

        # 1) auth
        register_user(client)
        token = login(client)
        client.headers["Authorization"] = f"Bearer {token}"

        # 2) prime cache + capture baseline
        pre_batches = list_batches(client)
        pre_ids = {b["id"] for b in pre_batches}
        log(f"GET /batches primed ({len(pre_ids)} existing) — cache warm")

        # 3) SFTP drop
        tiff = make_tiff_bytes()
        sftp_drop(drop_name, tiff)
        log(f"SFTP-dropped {drop_name} ({len(tiff)} bytes) to {UPLOAD_DIR}")

        # 4) poll until the new batch lands AND is terminal-done.
        deadline = time.monotonic() + POLL_TIMEOUT_S
        new_batch: dict | None = None
        while time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL_S)
            current = list_batches(client)
            new_batch = find_done_batch_for(current, pre_ids)
            if new_batch is not None:
                break
            log(
                "still waiting: "
                f"{len(current)} batches visible, "
                f"{len(current) - len(pre_ids)} new"
            )
        if new_batch is None:
            log(f"TIMEOUT after {POLL_TIMEOUT_S:.0f}s waiting for done batch")
            return 1

        log(
            f"new batch {new_batch['id']} reached status=done "
            f"(sftp_path={new_batch.get('sftp_path')!r})"
        )

        # 5) prediction must be in /predictions/recent with the same filename.
        predictions = list_recent_predictions(client)
        matches = [p for p in predictions if p.get("filename") == drop_name]
        if not matches:
            log(
                f"FAIL: no prediction visible for filename={drop_name!r}; "
                f"recent contained {len(predictions)} items"
            )
            return 1
        pred = matches[0]
        log(
            f"prediction OK: label={pred.get('label')!r} "
            f"confidence={pred.get('confidence'):.4f} "
            f"overlay_path={pred.get('overlay_path')!r}"
        )

    log("SMOKE PASS")
    return 0


def main() -> int:
    try:
        return run_smoke()
    except Exception as exc:
        log(f"FAIL: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
