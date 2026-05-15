"""
Integration tests for the four ``app/infra/`` adapters.

These tests talk to **real** local containers (those defined in
``docker-compose.yml``). Bring the stack up first::

    docker compose up -d redis minio sftp

If any container is missing, the corresponding test will fail at the
connect step — which is exactly the signal we want: an adapter that
cannot reach its backing service is broken, full stop.

Scope
-----
The tests assert *the adapter can complete one round-trip against its
backing container*. They do **not** assert correctness of business
logic (that lives in service-layer tests) and they do not assert
end-to-end pipeline behaviour (that lives in ``tests/smoke/``).

Why integration tests for infra adapters at all?
------------------------------------------------
Mocks would prove only that the adapter compiles. Real-container tests
catch:
  • credential / endpoint / port misconfigurations
  • SDK-version surprises (e.g. ``minio.put_object`` arg signature
    changes between versions)
  • permission / chroot issues (e.g. atmoz/sftp chrooting ``scanner``
    so the share appears as ``/upload`` rather than
    ``/home/scanner/upload``)

We were burned by the third bullet in last week's project; that's why
this whole file exists.
"""

from __future__ import annotations

import os

import pytest

from app.infra.blob  import MinioBlobClient
from app.infra.cache import init_redis_cache
from app.infra.queue import RQClient
from app.infra.sftp  import SFTPClient


# ---------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------
# These match docker-compose.yml's *host-side* port mappings. Inside
# the compose network, services reach each other by service name
# (``minio:9000``, ``redis:6379``, …); from the host (where pytest
# runs) we use ``localhost`` and the published port.
#
# Credentials are intentional local-dev defaults — they will NEVER
# match production. Production secrets resolve from Vault at app
# startup (project rule, see CLAUDE.md §5 and SECURITY.md).

# MinIO -----------------------------------------------------------------
MINIO_ENDPOINT:   str = "localhost:9000"
MINIO_ACCESS_KEY: str = os.environ.get("MINIO_ROOT_USER", "admin")
MINIO_SECRET_KEY: str = os.environ.get("MINIO_ROOT_PASSWORD", "change-me-in-production")

# Redis (used by BOTH the RQ queue adapter AND the cache adapter — they
# share a single Redis container; in production we separate by logical
# DB number, but for these connectivity smoke-tests DB 0 is fine.) -----
REDIS_URL: str = "redis://localhost:6379/0"

# SFTP ------------------------------------------------------------------
SFTP_HOST:     str = "localhost"
SFTP_PORT:     int = 2222   # host-side mapping; container listens on 22
SFTP_USER:     str = os.environ.get("SFTP_USER", "scanner")
SFTP_PASSWORD: str = os.environ.get("SFTP_PASSWORD", "change-me-in-production")

# atmoz/sftp chroots the ``scanner`` user, so from inside the SFTP
# session the upload directory appears at ``/upload`` — NOT at the
# ``/home/scanner/upload`` path you'd see on the host. This is
# load-bearing; getting it wrong gives a confusing FileNotFoundError.
SFTP_REMOTE_DIR: str = "/upload"


# =====================================================================
# Test 1 — MinIO
# =====================================================================
def test_minio_blob_adapter() -> None:
    """Upload a small payload and confirm we get back an ``s3://`` URI.

    Exercises both adapter entry points:
      • ``startup()`` — bucket-ensure (idempotent: works whether or
        not the bucket already exists).
      • ``upload_file()`` — the actual write path.
    """
    client = MinioBlobClient(
        endpoint=MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        # Local compose is plaintext; secure=True would attempt TLS
        # against a non-TLS listener and hang.
        secure=False,
    )

    # Ensure the default ``documents`` bucket exists. If startup() ever
    # starts to silently swallow errors, this test will still surface
    # them on the subsequent put.
    client.startup()

    # Build a tiny in-memory payload. The exact bytes are irrelevant —
    # we are testing transport, not content.
    dummy_payload: bytes = b"\x49\x49\x2A\x00"  # "II*\x00" — TIFF magic
    object_key:    str   = "test_image.tiff"

    returned_uri: str = client.upload_file(object_key, dummy_payload)

    # The adapter contract is "return the fully-qualified S3 URI".
    # We assert both shape and content:
    assert isinstance(returned_uri, str), "upload_file must return a str path"
    assert returned_uri.startswith("s3://"), (
        f"expected s3:// URI, got {returned_uri!r}"
    )
    assert returned_uri.endswith(f"/{object_key}"), (
        f"URI should end with the object key, got {returned_uri!r}"
    )


# =====================================================================
# Test 2 — RQ (Redis Queue)
# =====================================================================
def test_redis_queue_adapter() -> None:
    """Enqueue a job and confirm we get back a non-empty job ID string.

    The job is NOT executed here — no worker is running and the
    function path we enqueue (``app.workers.inference.run``) does not
    yet exist. That's fine: RQ stores the dotted function path as a
    string at enqueue time and only resolves it inside the worker
    process at dequeue. We are testing the enqueue half of the
    contract.
    """
    queue_client = RQClient(redis_url=REDIS_URL)

    # The adapter requires an envelope payload (see RQClient docstring):
    #   • "func"   — dotted import path to the worker callable.
    #   • "kwargs" — forwarded verbatim to that callable.
    dummy_payload: dict[str, object] = {
        "func": "app.workers.inference.run",
        "kwargs": {
            "payload": '{"batch_id": "test-batch-001"}',
        },
    }

    job_id: str = queue_client.enqueue_job(
        queue_name="classification_queue",
        payload=dummy_payload,
    )

    # RQ generates UUID-ish job IDs; we don't pin the format but we do
    # assert it's a non-empty string so a silent failure of get_id()
    # can't slip through.
    assert isinstance(job_id, str), "enqueue_job must return a str"
    assert job_id, "job_id must not be empty"


# =====================================================================
# Test 3 — SFTP
# =====================================================================
def test_sftp_adapter() -> None:
    """Connect to SFTP, list+download the upload dir, assert we get a list.

    The directory may be empty (it almost always is in CI). The
    assertion is purely "we authenticated, opened a channel, and
    iterated the share without raising" — that is the smoke-test we
    actually need before the ingest worker is written.

    Note ``list_and_download_new_files`` is a *generator* — we
    materialise it into a list to (a) drive the iterator past the
    ``listdir`` call where connection failures would surface, and (b)
    give us a concrete shape to assert on.
    """
    # ``with SFTPClient(...) as s`` opens and closes the session
    # automatically (calls connect() / close()). Even if the inner
    # iteration raises, the context manager still tears the channel
    # down — important so we don't leak SSH connections across tests.
    with SFTPClient(
        host=SFTP_HOST,
        port=SFTP_PORT,
        username=SFTP_USER,
        password=SFTP_PASSWORD,
    ) as client:
        # Materialise the generator. If the share is empty we get [];
        # if there are leftover TIFFs from a previous test run we get
        # a list of (filename, bytes) tuples. Either is acceptable.
        results = list(client.list_and_download_new_files(SFTP_REMOTE_DIR))

    assert isinstance(results, list), (
        "list_and_download_new_files must yield a materialisable iterable"
    )


# =====================================================================
# Test 4 — fastapi-cache2 Redis backend
# =====================================================================
@pytest.mark.asyncio
async def test_redis_cache_adapter() -> None:
    """``init_redis_cache`` must complete without raising.

    The adapter pings Redis as part of its bootstrap so a misconfigured
    URL or down container surfaces here, not silently at first cached
    request. We wrap the call in a try/except so the failure message
    points at exactly which step blew up, rather than a bare stack
    trace.
    """
    try:
        await init_redis_cache(REDIS_URL)
    except Exception as exc:  # noqa: BLE001 — test boundary, catch-all is correct
        # ``pytest.fail`` produces a cleaner failure than letting the
        # exception propagate: it prints the explanatory message AND
        # the chained cause, so debugging is one scroll, not two.
        pytest.fail(f"init_redis_cache raised: {exc!r}")
