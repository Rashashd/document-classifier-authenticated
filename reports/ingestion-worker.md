# ingestion-worker — Phase 2 / Worker 1

**Branch:** `feature/c-infra-adapters`
**Author:** Person C
**Date:** 2026-05-13
**Commit under test:** `1b001e1`
**Status:** ✅ All 4 triage paths verified end-to-end against the live compose stack.

---

## Why the DB is mocked

Person B's database environment is incomplete and the SQLAlchemy /
domain schema for `Batch` is mismatched (DB columns the worker needs
vs `BatchRead`'s required `owner_id` / `document_count` / `sftp_path`).
To unblock the ingestion pipeline, this PR generates a UUID in place
of a real DB insert and emits a `ingest.db_mocked.batch_creation` log
event. The swap to `BatchService.create_pending_batch(...)` is a
single-line change at [app/workers/sftp_ingest.py:163-165](../app/workers/sftp_ingest.py#L163-L165)
once Person B's environment lands.

---

## Hardening — the four triage paths

The worker treats `/upload` as an untrusted source. Every file passes
through four checks before its bytes touch MinIO:

| # | Check | Failure action | Log level | Reason |
|---|---|---|---|---|
| 1 | `os.path.basename(name)` — sanitise | warn + use safe basename | WARNING | path-traversal defence |
| 2 | `size_of(...) == 0` | delete | INFO | scanner produced an empty file — operational noise, not an attack |
| 3 | `size > 50 MiB` OR extension ∉ `{.tiff,.tif}` | delete | WARNING | rejected as wrong-kind; deletion is preferable to letting unbounded junk accumulate |
| 4 | first 4 bytes ∉ `{b"II*\x00", b"MM\x00*"}` | **move to `/quarantine`** | **CRITICAL** | a file claiming to be a TIFF but lacking the magic bytes is treated as a security event, not an honest mistake |

Notes on the design:

- **The TIFF magic check is the security boundary.** A file that
  *says* it's a TIFF (right extension) but isn't (wrong magic) is the
  signature of an attempt to deliver something else through the
  pipeline — disguised executables, polyglots, etc. We quarantine
  rather than delete so an analyst can inspect.
- **Triage 3 catches `.csv` and oversized TIFFs.** Path 3 collapses
  two failure classes (wrong extension and too big) because both
  produce the same response (delete + log warning) and both are
  "harmless mistake, not an attack". If telemetry shows we need to
  distinguish them later, the `reason` field in the log already
  differentiates: `"wrong_extension"` vs `"oversized"`.
- **The MAX_FILE_SIZE_BYTES gate (50 MiB) is generous** vs the
  RVL-CDIP TIFFs (most ≪ 10 MiB) — it's primarily a memory-pressure
  defence, not a content gate.
- **No `except Exception:` in `process_one`.** The outer poll loop
  is the only `except Exception:` boundary, so a single malformed
  file can't kill the worker (CLAUDE.md §10.1).

---

## Ingestion — the polling loop

[app/workers/sftp_ingest.py](../app/workers/sftp_ingest.py):

1. **Boot:** load config from env vars (SFTP_HOST/PORT/USERNAME/PASSWORD,
   MINIO_*, REDIS_URL), configure structlog so both worker and stdlib
   logs render as JSON, connect to SFTP, ensure the `documents`
   bucket exists in MinIO.
2. **Each tick (every 5 s):**
   - `sftp.list_dir("/upload")`. On any SSH/socket failure, close and
     reconnect — this is the *only* place a reconnect happens, so a
     single bad file does NOT trigger reconnects.
   - For each entry: bind `request_id` (fresh UUID per file) and
     `filename` as structlog contextvars, run `process_one`, then
     clear the contextvars in a `finally` so they don't leak across
     iterations.
3. **`process_one(...)`** applies the four triage checks in order
   (cheapest first), then the happy path.

Logging contract — every log line emitted while processing a file
carries `request_id` and `filename`, including stdlib logs from
`app/infra/*` (via `structlog.contextvars.merge_contextvars` in the
shared processor list). That makes per-file traces grep-able.

---

## Storage — MinIO upload + DB mock + RQ enqueue

The happy path, in order:

```
bytes  = sftp.read_file(/upload/<name>)          # SFTP → memory
uri    = blob.upload_file(<name>, bytes, ...)    # → "s3://documents/<name>"
batch_id = str(uuid.uuid4())                     # MOCKED — emits ingest.db_mocked.batch_creation
ticket  = {"batch_id": batch_id, "minio_file_path": uri}
job_id  = queue.enqueue_job("classification_queue", {
            "func":   "app.workers.inference.run",
            "kwargs": {"payload": json.dumps(ticket)},
          })
sftp.delete_file(/upload/<name>)                 # only after successful enqueue
```

Ordering rationale:

- **Delete only after enqueue succeeds.** If MinIO upload or RQ
  enqueue raises, the file stays on `/upload` and the next poll
  retries it. The `BlobUnavailableError` / `QueueUnavailableError`
  exception types added in commit `1518d9b` make this clean.
- **Idempotency requirement.** Re-delivery means the downstream
  inference worker MUST dedupe on `(batch_id, filename)`. Currently
  `batch_id` is fresh on every retry (mocked), so dedupe must key on
  `filename` until the real DB swap.
- **Ticket shape** matches what the future inference worker needs:
  `batch_id` to write back the prediction row, `minio_file_path` to
  load the bytes. Adding fields later is non-breaking; renaming is.

---

## Testing — what was run

```sh
docker compose down -v                            # wipe volumes
docker compose up -d --build                      # build worker image, start everything
docker compose ps                                 # confirm 4 services up + healthy
uv pip install pillow                             # Pillow is in the ml extra; tests need it
.venv/bin/python scripts/generate_test_drops.py   # drop the 4 test files
sleep 7                                           # wait one poll cycle
docker compose logs worker-ingest                 # observe triage decisions
docker compose exec sftp     ls /home/scanner/upload     /home/scanner/quarantine
docker compose exec minio    mc ls local/documents/
docker compose exec redis    redis-cli LLEN rq:queue:classification_queue
```

### Drop output

```
dropped /upload/empty_noise.tiff (0 bytes)
dropped /upload/honest_mistake.csv (16 bytes)
dropped /upload/malicious_payload.tiff (40 bytes)
dropped /upload/valid_document.tiff (222 bytes)
```

### Worker decisions (JSON, redacted to triage-events only)

```json
{"event":"ingest.empty_file_deleted",                  "filename":"empty_noise.tiff",       "request_id":"bdbfa564-…", "level":"info",     "size":0}
{"event":"ingest.rejected",                            "filename":"honest_mistake.csv",     "request_id":"61b576bf-…", "level":"warning",  "size":16,  "reason":"wrong_extension"}
{"event":"ingest.security.malicious_payload_quarantined","filename":"malicious_payload.tiff","request_id":"6415b953-…","level":"critical","magic_hex":"74686973","quarantine_path":"/quarantine/malicious_payload.tiff"}
{"event":"ingest.db_mocked.batch_creation",            "filename":"valid_document.tiff",    "request_id":"4788137f-…", "level":"info",     "batch_id":"96a3c80e-…", "minio_uri":"s3://documents/valid_document.tiff"}
{"event":"ingest.success",                             "filename":"valid_document.tiff",    "request_id":"4788137f-…", "level":"info",     "batch_id":"96a3c80e-…", "minio_uri":"s3://documents/valid_document.tiff", "job_id":"71b518b9-…", "bytes":222}
```

(Notice `request_id` is shared between the `db_mocked.batch_creation`
and `success` events for the valid file — same `process_one` invocation.)

### State after the run

| Surface | Expected | Observed |
|---|---|---|
| `/upload` contents | empty (all 4 files processed) | empty ✅ |
| `/quarantine` contents | only `malicious_payload.tiff` | only `malicious_payload.tiff` (40 B) ✅ |
| MinIO bucket `documents` | only `valid_document.tiff` | only `valid_document.tiff` (222 B) ✅ |
| RQ queue `classification_queue` (`LLEN`) | 1 | 1 ✅ |
| RQ job id matches worker log | `71b518b9-…` | `71b518b9-524b-4c13-b20f-3ef34836d0ec` ✅ |

**All four triage paths produced the expected effect, log level, and
downstream state. No false positives, no false negatives, no orphaned
files on `/upload`.**

---

## What was done in this PR

| Change | File | Note |
|---|---|---|
| Extended SFTP adapter with 6 primitives | [app/infra/sftp.py](../app/infra/sftp.py) | `list_dir`, `size_of`, `read_partial`, `read_file`, `delete_file`, `move_file`. Kept `list_and_download_new_files` (re-implemented in terms of the new primitives) so the existing integration test still passes. |
| Hardened ingest worker | [app/workers/sftp_ingest.py](../app/workers/sftp_ingest.py) | Polling loop, four-stage triage, JSON structlog with per-file `request_id`, reconnect on SSH session loss, mocked DB, RQ enqueue. |
| Worker image | [Dockerfile](../Dockerfile) | Minimal 2-stage uv build; `CMD` runs the ingest worker. Person A/B may extend (non-root, healthcheck). |
| Compose service | [docker-compose.yml](../docker-compose.yml) | sftp command now `scanner:password123:1001::upload,quarantine`; new `worker-ingest` service depends on healthy redis/minio. |
| Test data generator | [scripts/generate_test_drops.py](../scripts/generate_test_drops.py) | Drops the four test files via paramiko; `valid_document.tiff` is a genuine 10×10 grayscale TIFF via Pillow. |

---

## Follow-ups for the rest of the team

1. **Person B:** swap the mocked `batch_id = str(uuid.uuid4())` for
   `BatchService.create_pending_batch(filename, minio_uri)` once the
   DB env is online and `BatchRead`'s required fields are reconciled
   with the columns in [app/db/models.py](../app/db/models.py).
2. **Person D (inference worker):** consume the
   `classification_queue`. Ticket shape is
   `{"batch_id": str, "minio_file_path": str}` JSON-encoded inside
   the RQ payload's `kwargs.payload`. The dotted import path the
   worker enqueues against is `app.workers.inference.run` — change it
   if your entrypoint lives elsewhere.
3. **CI / observability:** the JSON log lines are stable identifiers
   (`ingest.empty_file_deleted`, `ingest.rejected`,
   `ingest.security.malicious_payload_quarantined`,
   `ingest.db_mocked.batch_creation`, `ingest.success`,
   `ingest.boot`, `ingest.list_failed_reconnecting`,
   `ingest.unexpected_error`). Alerting on `level=critical` from
   this service should page the on-call.
4. **Bucket lifecycle:** uploads go to the root of the `documents`
   bucket using the original filename. Once Person B introduces
   `batch_id` for real, switch the key to
   `batches/{batch_id}/{filename}` so we don't get collisions when
   two different scans share a filename.
