# infra-adapters — integration test run

**Branch:** `feature/c-infra-adapters`
**Author:** Person C
**Date:** 2026-05-13
**Run by:** local — `.venv/bin/python -m pytest tests/integration/test_infra_adapters.py -v`

---

## Summary

### Initial run

| # | Test | Result |
|---|------|--------|
| 1 | `test_minio_blob_adapter`   | ✅ PASS |
| 2 | `test_redis_queue_adapter`  | ❌ FAIL — `AttributeError: 'Job' object has no attribute 'get_id'` |
| 3 | `test_sftp_adapter`         | ✅ PASS |
| 4 | `test_redis_cache_adapter`  | ✅ PASS |

**3 passed, 1 failed in 2.62s.** The failure was a real bug in the
queue adapter — exactly the class of issue mocks would have hidden.

### After fix (applied in this PR)

| # | Test | Result |
|---|------|--------|
| 1 | `test_minio_blob_adapter`   | ✅ PASS |
| 2 | `test_redis_queue_adapter`  | ✅ PASS |
| 3 | `test_sftp_adapter`         | ✅ PASS |
| 4 | `test_redis_cache_adapter`  | ✅ PASS |

**4 passed in 1.94s.** Fix landed in
[app/infra/queue.py:155](../app/infra/queue.py#L155) — replaced
`rq_job.get_id()` with `rq_job.id` (RQ 2.x).

---

## Environment

- **Host:** Linux WSL2 (Ubuntu, Python 3.11.15 inside `.venv`)
- **uv:** 0.11.3
- **Docker:** stack defined in `docker-compose.yml`
- **pytest:** 9.0.3 / pytest-asyncio 1.3.0 (`asyncio_mode = "auto"`)
- **Key package versions:** `rq==2.8.0`, `redis==4.6.0`, `minio==7.2.x`,
  `paramiko==5.0.0`, `fastapi-cache2==0.2.1`, `starlette==1.0.0`

---

## Pre-flight

Port conflicts had to be resolved before bringing the stack up — other
projects' containers were holding the ports we publish:

| Conflicting container | Port held | Action |
|---|---|---|
| `drift-triage-agent-redis-1` | 6379 | stopped |
| `wrc_minio`                  | 9000 / 9001 | stopped |

Both stopped via `docker stop`; both left stopped per the user's
direction. Restart with `docker start drift-triage-agent-redis-1
wrc_minio` when needed.

After that:

```sh
docker compose up -d redis minio sftp
```

Container health after ~10 s:

```
dc_minio   minio/minio:latest   Up (healthy)
dc_redis   redis:7              Up (healthy)
dc_sftp    atmoz/sftp:latest    Up
```

(`dc_sftp` has no healthcheck defined — the docker-compose.yml does
not currently declare one for it.)

---

## Test-by-test detail

### 1. `test_minio_blob_adapter` — PASS

What ran:

1. `MinioBlobClient("localhost:9000", "admin", "password123", secure=False)`.
2. `client.startup()` — first run created the `documents` bucket.
3. `client.upload_file("test_image.tiff", b"II*\x00")` — uploaded the
   TIFF magic bytes.
4. Asserted return value is a `str` starting with `s3://` and ending
   with `/test_image.tiff`.

Proves: bucket-ensure, credentials, host-side port 9000 mapping, and
the `put_object` round-trip all work.

### 2. `test_redis_queue_adapter` — FAIL

What ran:

1. `RQClient("redis://localhost:6379/0")`.
2. `enqueue_job("classification_queue", {"func": "app.workers.inference.run", "kwargs": {...}})`.
3. Inside the adapter: `queue.enqueue(...)` succeeded — the job
   *did* land on Redis. The failure is in the very next line, where
   the adapter tried to extract the job id.

Traceback (excerpted):

```
app/infra/queue.py:155: AttributeError
>           job_id = rq_job.get_id()
E           AttributeError: 'Job' object has no attribute 'get_id'
```

**Root cause:** RQ 2.x removed `Job.get_id()`. The canonical accessor
is now the `Job.id` property (a `str`). Verified directly against
the live Redis:

```py
>>> import rq; rq.VERSION
'2.8.0'
>>> j = q.enqueue('builtins.print', 'hello')
>>> j.id
'dee3085e-0a59-4ebb-bc45-29723d164e78'
>>> hasattr(j, 'get_id')
False
```

**Recommended fix (1-character change):**

```diff
- job_id = rq_job.get_id()
+ job_id = rq_job.id
```

Located at [app/infra/queue.py:155](../app/infra/queue.py#L155). No
other change required — the test itself is correct; the adapter just
needs to match the installed RQ version's API.

**Why this is the right kind of failure to find this way.** A mock
would have happily returned whatever string we told it to. The
integration test ran the *real* RQ against *real* Redis and surfaced
the actual SDK-version drift. This is exactly why §"Why integration
tests for infra adapters at all?" in the test file exists.

### 3. `test_sftp_adapter` — PASS

What ran:

1. `SFTPClient("localhost", 2222, "scanner", "password123")` inside a
   `with` block.
2. `list(client.list_and_download_new_files("/upload"))` — empty list
   on a freshly-booted share. Generator was fully drained.
3. Asserted the materialised value is a `list`.

Proves: TCP to host port 2222, SSH key-exchange, password auth, atmoz
chroot (the `/upload` path resolves correctly inside the chrooted
view), `listdir` round-trip, and clean teardown via `__exit__`.

### 4. `test_redis_cache_adapter` — PASS

What ran (`@pytest.mark.asyncio`):

1. `await init_redis_cache("redis://localhost:6379/0")`.
2. Inside: `AsyncRedis.from_url(...)` → `await client.ping()` →
   `FastAPICache.init(RedisBackend(client), prefix="dc-cache")`.
3. No exception ⇒ the `try/except → pytest.fail(...)` boundary in the
   test was never tripped.

Proves: `redis.asyncio` client constructs cleanly, the ping liveness
probe works (refuse-to-start gate is real), and the `RedisBackend`
plugs into `FastAPICache.init` without further error.

This is also the test that surfaced the `jinja2` upstream issue
earlier (fastapi-cache2 importing `starlette.templating`, starlette
1.0.0 hard-requiring jinja2). Fixed in commit `25b5c24` by adding
`jinja2>=3.1.0` to project deps.

---

## What the run validated about the test suite itself

- All four tests are **discovered automatically** under
  `tests/integration/` thanks to `[tool.pytest.ini_options].testpaths
  = ["tests"]` in pyproject.
- `asyncio_mode = "auto"` correctly handles the async cache test
  alongside the three sync tests.
- The tests **fail loudly** on real bugs rather than silently passing
  on stubbed values (see test 2).
- The compose stack is sufficient to satisfy three of four adapters;
  the fourth would have passed too without the SDK drift.

---

## Recommended follow-ups

1. **Apply the 1-line fix at [app/infra/queue.py:155](../app/infra/queue.py#L155)** and re-run.
2. **Add a quick CI version pin / floor** so RQ doesn't silently
   upgrade across a breaking-API line again. (Either pin `rq<3` or
   move to `rq>=2,<3`.)
3. **Add an SFTP healthcheck** in `docker-compose.yml` (current state:
   no healthcheck → `Up` not `Up (healthy)`).
4. **Coordinate with the testing/CI owner** to run these against a
   compose-spun stack on every push; the smoke test in
   `tests/smoke/` should drive the full pipeline once Persons B & D
   land their pieces.

---

## How to reproduce

```sh
# from document-classifier-authenticated/
docker compose up -d redis minio sftp
uv sync --extra dev
uv run pytest tests/integration/test_infra_adapters.py -v
```

Expected after the queue-adapter fix: 4 passed in ~3s.
