# c-infra-adapters

**Branch:** `feature/c-infra-adapters`
**Author:** Person C
**Date:** 2026-05-12

---

## Scope

**Ships:**

- `.gitignore` — Python / uv / Docker / volumes / secrets.
- `docker-compose.yml` — three infra services only (`sftp`, `minio`,
  `redis`), with healthchecks and named volumes.
- `app/infra/blob.py` — `MinioBlobClient` (`startup`, `upload_file`).
- `app/infra/queue.py` — `RQClient` (`enqueue_job`).
- `app/infra/sftp.py` — `SFTPClient` (`connect`, `close`,
  `list_and_download_new_files`).
- `app/infra/cache.py` — `init_redis_cache(redis_url)` async bootstrap
  for fastapi-cache2 against a `redis.asyncio` client (added in a
  follow-up commit on the same branch).
- `CLAUDE.md` (this repo's AI-orientation doc) and `reports/` directory
  with the standing report template documented in CLAUDE.md §8.

**Deliberately does NOT ship:**

- `app/workers/sftp_ingest.py` — the poll-loop + retry + quarantine
  logic is Person D's. The adapter is ready to plug into it.
- `app/infra/protocols.py` — referenced in the task brief but not
  required for these concrete classes; whoever centralises the
  Protocol/ABC interfaces can later have these three classes nominally
  implement them. No code change required on this side; the public
  method signatures (`startup`, `upload_file`, `enqueue_job`,
  `list_and_download_new_files`) are stable.
- Vault adapter — not in the Phase 1 scope I was assigned.
- Updates to `pyproject.toml` — currently empty, awaiting Person A's
  dependency-setup PR. My adapters import `minio`, `rq`, `redis`,
  `paramiko`; these need to be added to project deps before any worker
  importing my modules will run. **Flagging for Person A.**
- `api`, `worker`, `sftp-ingest`, `migrate` compose services — those
  belong to their respective owners (Persons B/D).

---

## Key decisions

### `MinioBlobClient` returns an `s3://` URI, not a presigned URL.

- **Why:** Presigned URLs have an expiry and are an authorisation
  concern; the adapter should not silently hard-code a TTL. The DB
  should hold a stable object identifier, and any HTTP-downloadable
  link is generated on demand by a separate (future) method.
- **Alternative considered:** Return just the object key. Rejected
  because callers would still need to remember the bucket; `s3://` is
  self-describing.

### `RQClient.enqueue_job(payload)` uses a `{"func": ..., "kwargs": ...}` envelope.

- **Why:** The task brief mandated the signature
  `enqueue_job(queue_name, payload) -> str`. RQ itself needs a function
  reference. Embedding the dotted import path in the payload preserves
  the signature while keeping the adapter ignorant of which worker
  functions exist — function resolution happens inside the RQ worker
  process via standard Python import.
- This composes cleanly with the existing
  `app/domain/jobs.InferenceJob.to_rq_kwargs()`, which already returns
  `{"kwargs": {"payload": <json>}}` — the caller just adds a `"func"`
  key with `"app.workers.inference.run"` (or wherever Person D lands
  the entrypoint).
- **Alternative considered:** Mapping `queue_name -> func_path` in
  settings. Rejected as too tightly coupled to runtime config; a config
  change shouldn't be needed to introduce a new job type.

### `SFTPClient` deletes the remote file *after* a successful read.

- **Why:** This is the at-most-once delivery contract for the pipeline.
  The trade-off — flagged loudly in the docstring — is that the
  downstream inference worker MUST be idempotent on
  `(batch_id, filename)`. Cross-team contract: Person D needs to honour
  this.
- **Failure mode:** If deletion fails after a successful read, we yield
  the bytes anyway and log loudly. Re-processing one image is cheap;
  losing a customer's scan is not.

### Queue + cache on the same Redis instance, separated by DB number.

- DB 0 = RQ queue. DB 1 = `fastapi-cache2`. Encoded in the
  connection-string suffix that the caller chooses, not hard-coded in
  the adapter.
- **Why:** One container, one volume, one healthcheck — and a cache
  `FLUSHDB` won't take the queue down with it.

### `init_redis_cache` is async and pings Redis at startup.

- **Why:** `FastAPICache.init` itself is sync, but issuing an
  `await client.ping()` inside the bootstrap lets a missing /
  unreachable Redis fail the FastAPI startup event loudly. The
  alternative — lazy connection on first cache read — would let
  `/healthz` return 200 while every cached route 500s. The
  refuse-to-start contract in the brief implies we want the former.
- **`decode_responses=False`** on the async client is deliberate and
  commented in the module: `RedisBackend` stores pickled bytes; auto-
  decoding to `str` would corrupt every read.
- **`CACHE_KEY_PREFIX = "dc-cache"`** centralises the key namespace so
  ops-time scans (`KEYS "dc-cache:*"`) work without nuking the queue
  keyspace on logical DB 1. Both `app/infra/queue.py` (DB 0) and this
  module (DB 1) share the same Redis container; the DB suffix in the
  URL is the only thing keeping them apart.

### Host ports bound to `127.0.0.1` only.

- **Why:** A dev laptop on a coffee-shop network shouldn't be exposing
  MinIO root creds to the LAN. Production overrides this via
  environment-specific compose overlays.

### `--appendonly yes` on Redis.

- **Why:** AOF gives durable queue semantics. Without it, an unclean
  shutdown drops in-flight jobs and forces a drain from the dead-letter
  queue (which doesn't even exist yet).

---

## Cross-team touchpoints

| Touchpoint | Affects | Action |
|---|---|---|
| `app/infra/blob.py` | Person D (workers) | Call `startup()` once at process boot; `upload_file()` is sync, plan accordingly in async contexts. |
| `app/infra/queue.py` | Person D | `payload` envelope contract: must include `"func"`; see RQClient docstring. |
| `app/infra/sftp.py` | Person D | `connect()` once; `list_and_download_new_files()` is a generator — iterate inside the polling tick, do NOT cache the iterator across ticks. Idempotency on `(batch_id, filename)` is required. |
| Bucket name `"documents"` | Person B | The future `presigned_url` service-layer code should import the bucket name from `MinioBlobClient.DEFAULT_BUCKET`, not re-string-literal it. |
| `pyproject.toml` deps | Person A | Add `minio`, `rq`, `redis`, `paramiko`, `fastapi-cache2` before workers/api can import my modules. |
| `app/api/main.py` startup | Person B | Call `await init_redis_cache(settings.REDIS_CACHE_URL)` from the FastAPI startup event handler. Must run *before* any router with `@cache` is hit. |
| Compose secrets vs. Vault | Whoever wires Vault | Compose-level `MINIO_ROOT_USER` / password are dev-only; production paths must resolve from Vault. SECURITY.md will own this story. |

---

## Open questions / follow-ups

1. **`protocols.py`** — should `BlobStorageProtocol`, `QueueProtocol`,
   and `SFTPProtocol` live in `app/infra/protocols.py` or
   `app/domain/protocols.py`? The brief implies the former; I'll
   default to that in a follow-up unless Person B argues otherwise.
2. **Dead-letter queue** — not in scope here, but RQ's `FailedJobRegistry`
   needs a retention policy. Open card on Trello.
3. **SFTP host-key pinning** — for the local dev stack we trust the
   atmoz/sftp container's generated key (persisted in the `sftp-data`
   volume). Production needs an explicit pinned key — flagging for the
   security review.
4. **Bucket lifecycle policy** — uploaded blobs grow unbounded. Once
   we ship beyond the bootcamp deadline, a lifecycle rule for cold
   blobs would be sensible. Not in scope this week.

---

## How to verify locally

```sh
# 1) Bring up only the three infra services this PR owns.
docker compose up -d redis minio sftp

# 2) Sanity-check Redis.
docker compose exec redis redis-cli ping            # → PONG

# 3) Sanity-check MinIO from the host.
open http://127.0.0.1:9001                          # admin / password123

# 4) Sanity-check SFTP from the host.
echo "test" > /tmp/scan.tif
sftp -P 2222 scanner@127.0.0.1                      # password123
> cd upload
> put /tmp/scan.tif
> ls
> bye

# 5) Adapter smoke (once pyproject deps land):
python -c "
from app.infra.blob  import MinioBlobClient
from app.infra.queue import RQClient
from app.infra.sftp  import SFTPClient

blob = MinioBlobClient('127.0.0.1:9000', 'admin', 'password123')
blob.startup()
print(blob.upload_file('smoke/hello.txt', b'hello world'))

rq = RQClient('redis://127.0.0.1:6379/0')
print(rq.enqueue_job('inference', {'func':'builtins.print','kwargs':{'value':'hi'}}))

with SFTPClient('127.0.0.1', 2222, 'scanner', 'password123') as s:
    for name, data in s.list_and_download_new_files('/upload'):
        print(name, len(data))
"
```

All three calls should succeed; the SFTP iterator should drain the
share and leave it empty.

---

## Notes for the Friday demo

When the examiner asks me to trace a request through the layers, the
SFTP → MinIO → RQ → inference path goes:

```
sftp drop
   └─→ sftp_ingest worker (Person D)
         └─→ SFTPClient.list_and_download_new_files()         [this PR]
         └─→ MinioBlobClient.upload_file()                    [this PR]
         └─→ RQClient.enqueue_job("inference", {...})         [this PR]
                └─→ inference worker (Person D)
                      └─→ MinioBlobClient.upload_file()  (overlay) [this PR]
                      └─→ batch_service.mark_done()       (Person B)
                            └─→ cache invalidation             (Person B)
```

The infra layer here is intentionally dumb — every architectural
decision (retries, idempotency, cache invalidation) is *above* this
layer. That's the point.
