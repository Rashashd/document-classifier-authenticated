# Architecture

Technical reference for the document-classifier-authenticated service.

---

## System overview

TIFF scans arrive over SFTP. Worker 1 (ingestion) polls the drop folder,
validates and uploads each file to MinIO, writes a `Batch` record to Postgres,
and enqueues an inference job on Redis. Worker 2 (inference) pops the job,
loads the image from MinIO, runs it through the ConvNeXt Tiny classifier, writes
an overlay PNG back to MinIO, and persists the `Prediction` row. The FastAPI
service serves the results through a role-gated REST API with JWT auth and
Casbin RBAC; a React/nginx frontend proxies all API calls.

---

## Layer structure

```
app/api/          → HTTP boundary: routers, auth deps, request/response shapes
app/services/     → business logic, cache invalidation, audit writes
app/repositories/ → SQL queries only; no HTTP errors; flush, not commit
app/domain/       → pure Python: enums, dataclasses, Pydantic schemas
app/infra/        → external SDK wrappers: blob, queue, sftp, cache, vault
app/db/           → SQLAlchemy models + Alembic migrations
app/classifier/   → PyTorch model load, preprocessing, inference — no infra imports
app/workers/      → RQ entrypoints: sftp_ingest, inference
app/core/         → settings, lifespan startup
```

Import rules (enforced in code review):

| Layer | May import from | Must NOT |
|---|---|---|
| API | services, domain | touch SQLAlchemy, cache, or infra directly |
| Services | repositories, domain, infra | raise `HTTPException` |
| Repositories | `app/db/models.py`, domain | raise HTTP errors; invalidate caches |
| Domain | (nothing internal) | depend on SQLAlchemy |
| Infra | external SDKs only | contain business logic; touch DB |
| DB models | — | be imported outside repositories |

Cache invalidation is the **service layer's** responsibility only.

---

## Startup sequence

The API lifespan (`app/core/lifespan.py`) runs five checks in order. Any failure
calls `sys.exit(1)` — the container crash-loops rather than serving broken
responses.

| Step | Check | Failure log key |
|---|---|---|
| 0 | Redis reachable (cache ping) | `redis_unreachable` |
| 1 | `classifier.pt` present and SHA-256 matches `model_card.json` | `classifier_weights_missing` / `classifier_sha256_mismatch` |
| 2 | Postgres async engine created | `db_engine_failed` |
| 3 | Vault reachable and `secret/jwt` present | `vault_unreachable_or_jwt_missing` |
| 4 | Casbin enforcer loads; `casbin_rule` table non-empty | `casbin_load_failed` / `casbin_policy_table_empty` |

The JWT secret is held in `app.state.jwt_secret`. The Casbin enforcer is held in
`app.state.enforcer`. Neither is read from environment variables.

---

## API routes

| Method | Path | Auth | Notes |
|---|---|---|---|
| `GET` | `/health` | none | liveness check |
| `POST` | `/auth/register` | none | creates a user with role `reviewer` |
| `POST` | `/auth/login` | none | returns Bearer JWT (60-min TTL, HS256) |
| `POST` | `/auth/logout` | bearer | revokes token |
| `GET` | `/users/me` | any role | returns `UserRead`; `hashed_password` never exposed |
| `GET` | `/users` | admin | lists all users |
| `POST` | `/users/admin/{uid}/role` | admin | toggles role; 409 on self-demotion |
| `GET` | `/audit` | admin, auditor | last 200 entries newest-first |
| `GET` | `/batches` | any role | paginated; filters by `owner_id` for non-admin |
| `GET` | `/batches/{id}` | any role | 403 if not owner and not admin |
| `PATCH` | `/batches/{id}` | admin | update batch status |
| `GET` | `/predictions/recent` | any role | last N predictions |
| `PATCH` | `/predictions/{id}` | reviewer | relabel; blocked if confidence ≥ 0.7 |

All routes except health and auth require a valid Bearer JWT. Role checks use
`require_role(*roles)` from `app/api/deps.py`, which calls `enforcer.enforce()`.

---

## Authentication and RBAC

**Authentication:** `fastapi-users` with a JWT Bearer backend. The signing key
is fetched from Vault KV v2 at startup (`secret/jwt`) and stored on
`app.state`. Token TTL is 60 minutes. Tokens are HS256.

**Roles:** three roles enforced by Casbin.

| Role | Can do |
|---|---|
| `reviewer` | relabel predictions, read batches and predictions |
| `auditor` | read audit log only; cannot relabel |
| `admin` | inherits both reviewer and auditor; manages users |

Casbin model (`app/casbin/model.conf`): RBAC with role inheritance.
Policy (`app/casbin/policy.csv`): seeded at migration time via Alembic
`op.bulk_insert`. The lifespan also checks `casbin_rule` is non-empty.

The enforcer is file-based (reads `policy.csv` at startup, no DB round-trip per
request). The DB table provides durability and a deploy guard.

**Audit log:** `AuditService.log_event` is called inside the same session
transaction as any role change or relabel. Each row records `actor_id`, `action`
(e.g. `"relabel"`, `"batch_status_change"`), `target` (resource path), and a
per-request `request_id`.

---

## Data models

Five tables in Postgres, managed by Alembic.

### `users`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `email` | varchar(320) unique | indexed |
| `hashed_password` | text | bcrypt via fastapi-users |
| `role` | varchar(32) | default `reviewer` |
| `is_active` | bool | fastapi-users field |
| `created_at` | timestamptz | server default |

### `batches`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `sftp_path` | text | original filename from SFTP |
| `owner_id` | UUID FK → users | nullable (scanner-ingested batches have no owner) |
| `status` | varchar(32) | `pending` → `processing` → `done` / `failed` |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | auto-updated |

`document_count` is an ORM `@property` that counts the `predictions` relationship
— not a stored column.

### `predictions`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `batch_id` | UUID FK → batches | |
| `filename` | text | |
| `label` | varchar(32) | `DocumentLabel` enum value |
| `confidence` | float | top-1 softmax probability |
| `overlay_path` | text | `s3://documents/<key>` for the overlay PNG, nullable |
| `created_at` | timestamptz | |

### `audit_entries`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `actor_id` | UUID FK → users | nullable (SET NULL on user delete) |
| `action` | varchar(128) | short verb e.g. `relabel` |
| `target` | varchar(512) | resource path |
| `request_id` | varchar(128) | per-request UUID for log correlation |
| `timestamp` | timestamptz | server default |

### `casbin_rule`

Shape required by the casbin-sqlalchemy-adapter: `ptype`, `v0`–`v5` varchar columns.

---

## Infra adapters

All adapters live under `app/infra/`. They wrap external SDKs and raise typed
exceptions from `app/infra/exceptions.py` (`BlobUnavailableError`,
`QueueUnavailableError`, `CacheUnavailableError`, `SFTPConnectError`). The
service layer catches these typed exceptions — no SDK imports above the infra
layer.

### `MinioBlobClient` (`app/infra/blob.py`)

- `startup()` — ensures the `documents` bucket exists.
- `upload_file(name, data, content_type)` → `"s3://documents/<name>"`.
- `download_file(name)` → `bytes`.

Returns `s3://` URIs rather than presigned URLs so the DB holds a stable object
identifier independent of expiry.

### `RQClient` (`app/infra/queue.py`)

- `enqueue_job(queue_name, payload)` → job ID string.
- `payload` must be `{"func": "<dotted.path>", "kwargs": {...}}`. The RQ worker
  process resolves the function by import — the adapter stays ignorant of which
  worker functions exist.

### `SFTPClient` (`app/infra/sftp.py`)

Primitives: `connect`, `close`, `list_dir`, `size_of`, `read_partial`,
`read_file`, `delete_file`, `move_file`. The higher-level
`list_and_download_new_files` iterator is built from these.

### `init_redis_cache` (`app/infra/cache.py`)

Bootstraps `fastapi-cache2` against Redis DB 1 (`decode_responses=False` — the
backend stores pickled bytes). Pings Redis before returning; an unreachable Redis
fails the startup event and causes `sys.exit(1)`.

Cache key prefix: `dc-cache:`. DB 0 is used by RQ (queue); DB 1 by the API
cache. Same Redis container, different logical databases.

### `VaultClient` (`app/infra/vault.py`)

`get_secret(path)` — KV v2, raises `RuntimeError` on any failure. Workers call
this at boot to fetch MinIO and SFTP credentials. The API calls it during
lifespan to fetch the JWT signing key.

---

## Worker 1 — SFTP ingestion (`app/workers/sftp_ingest.py`)

Polls `/upload` on the SFTP container every 5 seconds. For each file, runs a
four-stage triage before touching MinIO or Redis:

| Stage | Check | Action |
|---|---|---|
| 1 | Path sanitisation | Use `os.path.basename`; log and continue with safe name |
| 2 | Zero-byte file | Delete; log `INFO` |
| 3 | Wrong extension (`!= .tif/.tiff`) or size > 50 MiB | Delete; log `WARNING` |
| 4 | TIFF magic bytes (`II*\x00` or `MM\x00*`) | Wrong magic → **quarantine** to `/quarantine/`; log `CRITICAL` |

Happy path:

```
bytes  = sftp.read_file(/upload/<name>)
uri    = blob.upload_file(<name>, bytes)          → "s3://documents/<name>"
batch  = BatchService.create_pending_batch(name, uri)
ticket = {"batch_id": batch.id, "minio_file_path": uri}
job_id = queue.enqueue_job("classification_queue", {
           "func": "app.workers.inference.run",
           "kwargs": {"payload": json.dumps(ticket)},
         })
sftp.delete_file(/upload/<name>)                  → at-most-once delivery
```

The remote file is deleted only after enqueue succeeds. If MinIO or Redis is
unreachable the file stays on `/upload` and retries on the next poll.

Per-file structured logs carry `request_id` and `filename` as structlog
contextvars so all log lines from a single file are correlated.

---

## Worker 2 — inference (`app/workers/inference.py`)

RQ consumer on `classification_queue`. For each job:

```
payload      = json.parse(rq_payload["kwargs"]["payload"])
vault_creds  = fetch_vault_secrets()             → minio access/secret keys
blob         = MinioBlobClient(vault_creds)
image_bytes  = blob.download_file(payload["minio_file_path"])

label, confidence, overlay_bytes = run_classification(image_bytes)
# ↑ injectable seam (classify= kwarg) for testing without torch

overlay_key  = "overlays/<batch_id>/<filename>.png"
blob.upload_file(overlay_key, overlay_bytes, "image/png")

asyncio.run(_persist(engine, redis, PredictionCreate(...)))
# ↑ NullPool engine per call — asyncpg connections are loop-bound
```

`_persist` runs inside `AsyncSession(engine, expire_on_commit=False)`:
1. `PredictionRepository.create(prediction_in)` — inserts and commits.
2. `BatchRepository.update_status(batch_id, BatchStatus.done)` — flushes.
3. `session.commit()` — persists the status flip.
4. `_invalidate_batch_caches()` — Redis SCAN `dc-cache:*` + DEL (best-effort).

`run_classification` bundles classify + overlay into one tuple
`(label, confidence, overlay_bytes)`. The injectable `classify=` kwarg lets
integration tests stub the full ML path without loading torch or the 111 MB
weights.

Secrets: MinIO credentials from Vault at boot (`secret/minio`). Redis URL from
env (no credential). JWT secret not needed — the worker writes directly to the
DB, it does not call the API.

---

## Ali Asfahani: RVL-CDIP Classifier

Ali Asfahani owns the visual document classifier path: training the model in Colab,
shipping the classifier artifacts, and defining the runtime inference contract
used by the worker. The classifier is intentionally separate from the API,
database, queue, and blob-storage layers.

The model classifies RVL-CDIP document images by visual layout only. It does not
run OCR and does not use document text as input. The sixteen output classes are:

- letter
- form
- email
- handwritten
- advertisement
- scientific_report
- scientific_publication
- specification
- file_folder
- news_article
- budget
- invoice
- presentation
- questionnaire
- resume
- memo

## Training and Artifacts

Training happens in Google Colab, not in the local docker-compose stack. The
notebook used for the current artifact is
`Ali_rvl_cdip_convnext_colab_pro_v2_CLEAN_RUN_ALL.ipynb`.

The notebook performs a balanced 100k-image RVL-CDIP run:

- 80,000 training images, 5,000 per class
- 10,000 validation images, 625 per class
- 10,000 test images, 625 per class

The current run is explicitly not the full RVL-CDIP train/validation/test run.
The model card records this with `run_mode = balanced_100k` and `full_run = false`.

The notebook produces the repo-ready classifier files:

- `app/classifier/models/classifier.pt`
- `app/classifier/models/model_card.json`
- `app/classifier/eval/golden_expected.json`
- `app/classifier/eval/golden_images/`

`classifier.pt` stores the trained ConvNeXt Tiny state dict plus the metadata
needed to rebuild preprocessing at runtime: class names, image size, backbone
name, weights enum, freeze policy, ImageNet normalization mean, and ImageNet
normalization standard deviation.

`model_card.json` records the SHA-256 hash of `classifier.pt`, the dataset
source, the no-OCR constraint, run sizes, model architecture, training
hyperparameters, test top-1/top-5 accuracy, per-class accuracy, worst class, and
the Colab environment fingerprint.

Current classifier metrics from the balanced 100k run:

- test top-1: `0.7261`
- test top-5: `0.9388`
- worst class: `scientific_report`
- worst-class accuracy: `0.4576`

The classifier weights are stored with Git LFS because the artifact is about
111 MB.

### Per-class accuracy

| Class | Accuracy |
|---|---|
| file_folder | 0.9248 |
| email | 0.9200 |
| scientific_publication | 0.8528 |
| handwritten | 0.8752 |
| advertisement | 0.8272 |
| news_article | 0.7776 |
| specification | 0.7904 |
| resume | 0.7888 |
| presentation | 0.6928 |
| letter | 0.6976 |
| invoice | 0.6704 |
| budget | 0.6688 |
| memo | 0.5648 |
| form | 0.5744 |
| questionnaire | 0.5344 |
| scientific_report | 0.4576 |

## Model Training Flow

The Colab notebook follows this flow:

1. Mount Google Drive and check the GPU and Colab disk.
2. Verify the RVL-CDIP split files and archive/subset archive.
3. Restore or extract the local TIFF subset under `/content`.
4. Read the official RVL-CDIP split files.
5. Select balanced train, validation, and test rows per class.
6. Filter unreadable test TIFFs before final evaluation.
7. Build PyTorch datasets and dataloaders.
8. Convert grayscale TIFFs to RGB, resize to `224×224`, and normalize using the
   ConvNeXt ImageNet preprocessing constants.
9. Train a ConvNeXt Tiny classifier head with the pretrained backbone frozen.
10. Partially unfreeze the final ConvNeXt feature stage and fine-tune with a
    smaller learning rate.
11. Evaluate top-1, top-5, and per-class accuracy.
12. Save `classifier.pt`, compute its SHA-256, and write `model_card.json`.
13. Select and copy the 50-image golden set.
14. Replay the golden set on CPU to verify deterministic expected outputs.
15. Package the repo-ready artifacts into a zip for local extraction and commit.

Training hyperparameters:

| Hyperparameter | Value |
|---|---|
| Seed | 42 |
| Batch size | 64 |
| Linear probe epochs | 2 |
| Partial unfreeze epochs | 2 |
| Linear probe LR | 0.0003 |
| Fine-tune LR | 0.00001 |
| Best validation top-1 | 0.726 |

Training environment: Tesla T4 GPU, CUDA 12.8, torch 2.10.0, Python 3.12.

## Golden Set

The golden set is a 50-image subset selected from the test rows used by the
current run. The selection spans all sixteen RVL-CDIP classes and deliberately
mixes low-confidence, medium-confidence, and high-confidence examples.

The expected output file, `app/classifier/eval/golden_expected.json`, stores the
model's CPU prediction for every golden image:

- source filename
- true label and true label id
- expected top-1 label and label id
- top-1 confidence
- top-5 labels, ids, and confidences

The replay invariant is:

- predicted label must match exactly
- top-1 confidence must match within `1e-6`

This protects the project from accidental changes to preprocessing, class order,
model loading, or the classifier artifact.

## Runtime Inference Boundary

The classifier runtime code belongs under `app/classifier/`. It has no FastAPI,
SQLAlchemy, Redis, RQ, MinIO, or cache imports. Its responsibility is limited to:

- locating the model artifacts
- validating that the model and model card exist
- checking the `classifier.pt` SHA-256 against `model_card.json`
- rebuilding ConvNeXt Tiny with the correct 16-class head
- applying the exact same preprocessing used during training
- returning top-k predictions for an image path, bytes payload, or PIL Image

The API never runs inference directly — it only enqueues work. The inference
worker consumes a job, loads the image from blob storage, calls the classifier
runtime, writes an overlay PNG, and persists the prediction result through the
service/repository path.

The `RVLCDIPClassifier` class exposes three call forms:

```python
classifier.predict_path(path)         # from file
classifier.predict_bytes(content)     # from raw bytes (workers use this)
classifier.predict_image(pil_image)   # from PIL Image
```

`get_default_classifier()` is an `@lru_cache(maxsize=1)` singleton — the model
loads once per process.

This boundary keeps the classifier reusable in three places without modification:

- worker inference jobs
- golden-set replay tests (`app/classifier/eval/golden.py`)
- local smoke scripts

---

## Dataset and License Notices

### RVL-CDIP dataset

This project uses the RVL-CDIP document image dataset for classifier training,
evaluation, and golden-set replay.

- Source: `https://adamharley.com/rvl-cdip/`
- Use in this project: visual layout classification only; OCR not used.

The dataset is intended for academic and research use. The following repo files
are derived from RVL-CDIP images and should be treated as academic/research
materials:

- `app/classifier/models/classifier.pt`
- `app/classifier/models/model_card.json`
- `app/classifier/eval/golden_expected.json`
- `app/classifier/eval/golden_images/`

Do not use these artifacts for commercial purposes without confirming the dataset
terms from the original source.

### Pretrained model weights

The classifier backbone is `torchvision.models.convnext_tiny` with
`ConvNeXt_Tiny_Weights.DEFAULT`. The runtime model is a fine-tuned derivative
artifact created for this academic project. See the PyTorch and Torchvision
project licenses for upstream library and pretrained-weight terms.

- PyTorch: `https://github.com/pytorch/pytorch`
- Torchvision: `https://github.com/pytorch/vision`

### Project code

Unless otherwise noted in source comments, the application code in this
repository was written for the SE Factory Week 6 project. Third-party libraries
retain their own licenses.
