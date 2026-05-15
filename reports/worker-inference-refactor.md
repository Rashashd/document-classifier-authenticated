# worker-inference-refactor

**Branch:** `feature/mahdi-worker-inference`
**Author:** Person C
**Date:** 2026-05-14
**Companion docs:** [ingestion-worker.md](ingestion-worker.md) (Worker 1),
[vault-secrets-seeding.md](vault-secrets-seeding.md),
[env-and-compose.md](env-and-compose.md).

---

## TL;DR

Phase 4 of the project plan: turn Person A's raw ML worker into a clean
consumer of the team's adapters. Result is two clearly-separated files:

- `app/classifier/inference.py` (unchanged) — pure PyTorch. No env vars,
  no DB, no MinIO, no FastAPI.
- `app/workers/inference.py` (rewritten) — RQ entrypoint that talks to
  MinIO via `app.infra.blob`, fetches secrets via `app.infra.vault`,
  persists via `app.services.prediction_service`, and invalidates
  caches via async Redis. The ML pipeline (classify + overlay) sits
  behind one extractable seam called `run_classification`.

Six integration tests pass against the live compose stack.

---

## Before / After

### `app/workers/inference.py` before this commit

| Concern | How it was handled |
|---|---|
| Blob access | Two local classes `LocalBlobStore` + `MinioBlobStore` reinventing the wheel; a Protocol `BlobStore` for swap-ability; a `default_blob_store()` factory deciding by env var |
| MinIO credentials | `os.environ["MINIO_ENDPOINT"]`, `os.environ["MINIO_ACCESS_KEY"]`, `os.environ["MINIO_SECRET_KEY"]` directly |
| Prediction persistence | `JsonlPredictionSink` appending to `/tmp/week6-predictions.jsonl` (mock DB) |
| Batch status / cache | Untouched — batch stuck in `pending` forever |
| ML + overlay | Inline inside `run_inference`; no seam for testing |

That's three layering violations in ~250 lines:
1. Duplicate infrastructure code paralleling `app/infra/blob.py`.
2. Hardcoded secret-shaped env reads (violates the `grep -ri 'password' app/` rule).
3. A mock persistence sink that the team has now outgrown.

### After

| Concern | How it's handled now |
|---|---|
| Blob access | `app.infra.blob.MinioBlobClient` (same instance the ingestion worker uses), with the new `download_file` primitive |
| MinIO credentials | `fetch_vault_secrets()` reads `secret/minio` from Vault on boot; `sys.exit(1)` + critical log on failure |
| Prediction persistence | `app.services.prediction_service.PredictionService.save_prediction_and_complete_batch` — single transaction-boundary owner |
| Batch status | Flips to `BatchStatus.done` inside the same service call |
| Cache invalidation | `PredictionService` scans `dc-cache:*` and deletes via async redis (best-effort, doesn't roll back the persisted row) |
| ML + overlay | Bundled into `run_classification(image_bytes) -> tuple[str, float, bytes]` — the single seam tests mock |

5 classes deleted, ~70 lines removed in the worker itself, plus the new
56-line `prediction_service.py` and a 25-line addition to `blob.py`.

---

## Decisions (and why)

### 1. Why drop the `BlobStore` / `PredictionSink` `Protocol`s instead of conforming `MinioBlobClient` to them?

The Protocols were over-engineering for a single-team codebase where
we control every consumer. Our adapter (`MinioBlobClient`) has
slightly different ergonomics than the deleted `BlobStore` Protocol
(`upload_file` returns a URI, not None; `download_file` instead of
`read_bytes`). Refactoring `MinioBlobClient` to fit the worker's old
Protocol would be the tail wagging the dog; the team already agreed
on `app/infra/blob.py`'s shape in earlier PRs.

### 2. Why `run_classification(image_bytes) -> tuple[str, float, bytes]` rather than keeping the classifier call inline?

One seam, one mock. The test (`test_run_inference_end_to_end`) injects
a 3-line pure-Python stub via the `classify=` kwarg, exercising the
full DB + blob + cache path without needing torch (~700 MB) or the
trained ConvNeXt weights (~110 MB, git LFS) installed on the runner.

The tuple shape (string, float, bytes) avoids leaking the classifier's
internal `Prediction` dataclass into the worker's public contract.
The `Prediction` dataclass lives behind the seam in `app/classifier/`.

### 3. Why does `run_classification` bundle classify + overlay, instead of two functions?

Two reasons:

- The mock returns `(label, confidence, overlay_bytes)` in one shot.
  Splitting them in production but bundling them in the test would
  make the seam asymmetric.
- Overlay rendering wraps the classifier output (`prediction.label`
  + `prediction.confidence`) — they're naturally coupled. Splitting
  would force the worker to thread the `Prediction` dataclass between
  two calls, which is exactly what we're trying to avoid.

### 4. Why keep `create_overlay_png` in the worker rather than moving it to `app/classifier/`?

Per the project rule (Task brief, "Step 4"): the classifier file is
reserved for PyTorch math. Overlay rendering is presentation — it
uses `PIL.ImageDraw` and `ImageFont`, which are unrelated to model
inference. Mixing them would couple the ML team's editing surface to
the visualization code, which neither team owns cleanly.

### 5. Why does `PredictionService` own the second commit instead of `batch_repo.update_status`?

`batch_repo.update_status` only `flush()`es. Without an explicit
commit, the session closes via `async with` and SQLAlchemy rolls back
the dirty transaction. The first integration-test run caught this —
the worker logged `inference.success` but the batch stayed `pending`.

Options considered:

- Make `batch_repo.update_status` commit. Rejected because Sara owns
  `batch_repo.py`; changing its commit semantics under her could
  surprise other callers.
- Move all SQL into `PredictionService` and bypass `batch_repo`.
  Rejected — services don't write SQL, that's the repo layer's job
  (CLAUDE.md §2).
- **Adopted:** `PredictionService` calls `repo.update_status(...)`
  then `self._session.commit()` to flush the dirty tx. Documented in
  a one-line comment so the next reader knows why the explicit commit
  is there.

There's a secondary cost: `prediction_repo.create()` commits internally,
so we end up with two commits per inference call (prediction insert,
then batch+session commit). For our throughput that's fine. A future
cleanup would refactor `prediction_repo.create()` to use flush + leave
commits to the service.

### 6. Why `NullPool` + `asyncio.run(_persist(...))` per file instead of one shared event loop?

Same answer as the ingestion worker (see [ingestion-worker.md](ingestion-worker.md)).
RQ workers are sync; SQLAlchemy 2.0 async is async; `asyncio.run`
spawns a fresh event loop per call; **asyncpg connections aren't
safe across event loops**, so the engine needs `poolclass=NullPool`
to avoid checking out a connection from a dead loop. This cost is
~30 ms of postgres handshake per inference — well under our latency
budget.

### 7. Why does the worker keep `REDIS_URL` in env but fetch MinIO from Vault?

Redis runs without auth in our stack — the URL is a routing endpoint,
not a credential. Vault is for secrets. If we ever flip Redis to use
AUTH, we'd add a `secret/redis` path and migrate `REDIS_URL` to be
fetched from Vault then. Today it's not warranted.

### 8. Why mock the ML in the integration test instead of running the real classifier?

- **Speed.** The full classifier takes ~5 s to warm + ~1 s per inference
  on CPU. Tests need to run in seconds, not tens of seconds.
- **CI.** GitHub Actions runners don't have GPU and we can't ship the
  110 MB classifier weights through the CI cache reliably.
- **Separation of concerns.** Person A's golden-set replay test in
  `app/classifier/eval/golden.py` is the source of truth for *the
  model is correct*. Our test answers *the worker correctly wires the
  model output into the DB, blob, and cache*. Different questions,
  different tests.

The mock returns `("invoice", 0.99, image_bytes)` — chosen because
`"invoice"` is a valid `DocumentLabel` enum member, so the
`PredictionCreate(label=DocumentLabel(label))` mapping succeeds.

### 9. Why `Settings(extra="ignore")` instead of `extra="forbid"`?

Because the shared `.env` carries compose-only vars (POSTGRES_USER,
SFTP_PASSWORD, etc.) that `Settings` doesn't declare as fields. With
`extra="forbid"`, any host-side command that loads Settings (alembic,
pytest, ad-hoc scripts) crashes with a pydantic validation error.
Inside compose each service's `environment:` block is narrow enough
that the issue doesn't show, but host-side use is unavoidable.

`extra="ignore"` is the conventional default for pydantic-settings
exactly for this case. Security posture is unchanged: containerised
runs still get only the env vars in their compose block.

---

## File / function connection map

```text
  RQ classification_queue          (Redis db 0)
                │
                ▼
        rq.Worker.work()                                 ─┐
                │                                         │  app/workers/
                ▼                                         │  inference.py
        run_inference(payload)  ←──── injectable seams ───┤
                │                                         │
                ├─ fetch_vault_secrets() ──┐              │
                │                          ▼              │
                │              ┌─ app.infra.vault ────────┘
                │              │
                │              ▼
                │       VaultClient.get_secret("minio")
                │              │
                │              ▼
                │         hashicorp/vault dev (compose service)
                │
                ├─ blob = MinioBlobClient(creds)
                │              │
                │              ▼
                │       blob.download_file(blob_path) ─── input TIFF
                │
                ├─ run_classification(image_bytes) ────── single ML seam
                │              │
                │              ├─ classifier.predict_bytes() ─── app/classifier/inference.py
                │              │              │                 (pure PyTorch, untouched)
                │              │              ▼
                │              │       Prediction(label, confidence, top_k)
                │              │
                │              └─ create_overlay_png()  ─────── stays here, not in classifier
                │                              │
                │                              ▼
                │                       PNG bytes
                │
                ├─ blob.upload_file(overlay_key, png, "image/png")
                │              │
                │              ▼
                │       minio (compose service)
                │
                ├─ PredictionCreate(batch_id, filename, label=DocumentLabel(...),
                │                   confidence, overlay_path)
                │              │
                │              ▼
                ├─ asyncio.run(_persist(engine, cache, pred_in))  ─── async boundary
                │              │
                │              ▼
                │       AsyncSession(engine, expire_on_commit=False)
                │              │
                │              ▼
                │   PredictionService(session, cache_redis)
                │   .save_prediction_and_complete_batch(pred_in)
                │              │
                │              ├─ PredictionRepository.create(pred_in)
                │              │          │
                │              │          ▼  (commits its own tx)
                │              │      predictions row in postgres
                │              │
                │              ├─ BatchRepository.update_status(batch_id, BatchStatus.done)
                │              │          │
                │              │          ▼  (flush only)
                │              │      batch status pending → done
                │              │
                │              ├─ session.commit()  ── persists the status flip
                │              │
                │              └─ _invalidate_batch_caches()
                │                         │
                │                         ▼
                │                  async redis SCAN "dc-cache:*" → DELETE
                │                  (cache adapter shares this prefix with
                │                   fastapi-cache2 on the API side)
                │
                └─ return {batch_id, label, confidence, overlay_path}
                         (back to RQ for dashboard / inspection)
```

---

## Public surface of the refactored worker

```python
# app/workers/inference.py

ClassifyFn = Callable[[bytes], tuple[str, float, bytes]]

def fetch_vault_secrets() -> dict
def build_blob(minio_creds: dict) -> MinioBlobClient
def build_redis_url() -> str

def overlay_key_for(job: InferenceJob) -> str
def create_overlay_png(image_bytes: bytes, prediction: Prediction) -> bytes
def run_classification(image_bytes: bytes) -> tuple[str, float, bytes]

def run_inference(
    payload: str,
    *,
    blob:        MinioBlobClient | None = None,
    engine:      AsyncEngine     | None = None,
    cache_redis: AsyncRedis      | None = None,
    classify:    ClassifyFn      | None = None,
) -> dict

def configure_logging() -> None
def main() -> None
```

```python
# app/services/prediction_service.py

class PredictionService:
    def __init__(self, session: AsyncSession, *, cache_redis: AsyncRedis | None = None)
    async def save_prediction_and_complete_batch(
        self, prediction_in: PredictionCreate
    ) -> Prediction
```

```python
# app/infra/blob.py — added

def download_file(self, file_name: str, *, bucket: str = DEFAULT_BUCKET) -> bytes
```

---

## Verification

```sh
$ docker compose up -d db redis minio sftp vault
$ DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/app \
  VAULT_TOKEN=dev-root-token-change-in-prod \
  uv run alembic upgrade head
$ DATABASE_URL=...  VAULT_TOKEN=...  uv run pytest tests/integration/ -v

tests/integration/test_infra_adapters.py::test_minio_blob_adapter   PASSED
tests/integration/test_infra_adapters.py::test_redis_queue_adapter  PASSED
tests/integration/test_infra_adapters.py::test_sftp_adapter         PASSED
tests/integration/test_infra_adapters.py::test_redis_cache_adapter  PASSED
tests/integration/test_ingest_pipeline.py::test_ingest_pipeline_e2e PASSED
tests/integration/test_worker_inference.py::test_run_inference_end_to_end PASSED
============================== 6 passed in 2.90s ===============================
```

`ruff check app/ tests/integration/` — all checks passed.

---

## Follow-ups (not in this commit)

1. **Real ML golden-set test.** Person A's `app/classifier/eval/golden.py`
   replay test is still the production gate for *model correctness*.
   Our integration test only covers *pipeline plumbing*. Both run on
   CI; neither replaces the other.
2. **`prediction_repo.create()` should not commit.** Repositories
   shouldn't own transaction boundaries. Refactoring that lets us
   collapse the prediction-insert + batch-update into a single
   transaction, closing the small consistency window where the
   prediction lands but the batch status update fails. Sara's call.
3. **Cache invalidation granularity.** Today we wipe the whole
   `dc-cache:*` prefix. Once the API knows its key conventions (per
   batch ID, per user), we can scope the invalidation to just the
   affected batch's keys. Person B's call.
4. **Vault auth.** Worker still uses a root token. AppRole / Kubernetes
   auth is the prod path. See [vault-secrets-seeding.md](vault-secrets-seeding.md).
