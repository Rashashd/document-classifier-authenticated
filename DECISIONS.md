# Decision Log

## Ali Asfahani: Classifier Decisions

### Use ConvNeXt Tiny From Torchvision

Decision: use `torchvision.models.convnext_tiny` with
`ConvNeXt_Tiny_Weights.DEFAULT`.

Reasoning: the project brief requires pretrained backbones from
`torchvision.models` and allows ConvNeXt Tiny or Small. ConvNeXt Tiny is much
lighter than ConvNeXt Small, produces an artifact around 111 MB, and is more
realistic for the required CPU inference budget. It also fits better in Git LFS
and in the local worker container.

Consequence: accuracy may be lower than a larger backbone, but the runtime cost
and artifact size are easier for the full compose stack to handle.

### Classify Layout, Not Text

Decision: do not use OCR.

Reasoning: RVL-CDIP is a document layout classification task and the assignment
explicitly says to classify visual layout, not document text. All images are
loaded as TIFFs, converted to RGB, resized, normalized, and passed directly to
ConvNeXt.

Consequence: model predictions are based on document structure, spacing, and
visual patterns. Text content is ignored.

### Train In Colab And Ship Only Artifacts

Decision: training, evaluation, and golden-set selection happen in Colab. The
repo receives only the generated artifacts.

Reasoning: the full RVL-CDIP archive is large and the local docker-compose stack
must not train or see the full dataset. Colab provides GPU access and temporary
disk, while Google Drive stores the dataset files and generated outputs.

Consequence: the local app only needs to load `classifier.pt`, validate it, and
run inference. The repo stays small enough except for the model artifact, which
is stored through Git LFS.

### Use A Balanced 100k Run For The Current Artifact

Decision: the current classifier artifact is from a balanced 100k-image run, not
the full RVL-CDIP evaluation.

Reasoning: the full archive and full test-set evaluation are expensive in Colab
time and disk. A balanced 100k run still exercises the full training pipeline,
covers every class evenly, and gives meaningful metrics for integration work.

Current run sizes:

- training: 80,000 images
- validation: 10,000 images
- test: 10,000 images

Current metrics:

- test top-1: `0.7261`
- test top-5: `0.9388`
- worst class: `scientific_report`
- worst-class accuracy: `0.4576`

Consequence: the model card is honest and marks `run_mode = balanced_100k` and
`full_run = false`. For a strict final interpretation of the assignment, the
same notebook flow should be rerun with the full official test split.

### Train With Linear Probe Then Partial Unfreeze

Decision: train the replacement classifier head first, then unfreeze only the
final ConvNeXt feature stage and the classifier head.

Reasoning: a linear probe gives a stable baseline while preserving pretrained
ImageNet features. Partial unfreezing adapts the highest-level visual features
to document layouts without the cost and overfitting risk of full fine-tuning.

Consequence: the model card records the freeze policy as
`linear_probe_then_partial_unfreeze_final_stage`.

### Save Runtime Metadata Inside The Classifier Artifact

Decision: save more than just the PyTorch state dict in `classifier.pt`.

Reasoning: runtime inference must rebuild preprocessing and class order exactly.
The artifact therefore includes class names, image size, backbone, weights enum,
freeze policy, normalization mean, and normalization standard deviation.

Consequence: `app/classifier/inference.py` can load the model without guessing
class order or preprocessing constants.

### Validate Artifacts With SHA-256

Decision: compute the SHA-256 of `classifier.pt` and store it in
`model_card.json`.

Reasoning: the assignment requires the API and worker to refuse startup if the
classifier weights are missing or do not match the model card. A SHA-256 check
also catches accidental model swaps and corrupted downloads.

Consequence: startup checks can compare the real hash of `classifier.pt` against
the committed model card before serving or processing jobs.

### Store The Model With Git LFS

Decision: track `app/classifier/models/classifier.pt` with Git LFS.

Reasoning: the model artifact is about 111 MB. GitHub rejects normal Git blobs
over 100 MB, and large binaries should not live in normal Git history.

Consequence: `.gitattributes` marks `classifier.pt` as an LFS object. The model
push requires Git LFS support, but the repository history remains manageable.

### Golden Set Uses CPU Predictions

Decision: write golden expected labels and confidences from a CPU replay model.

Reasoning: CI and local replay are likely to run on CPU. Recomputing expected
values on CPU reduces drift between Colab GPU evaluation and service-side replay.

Consequence: `golden_expected.json` represents the exact CPU inference contract.
The replay check should fail if the predicted label changes or the top-1
confidence differs by more than `1e-6`.

### Keep Inference Code Outside The API Layer

Decision: model loading and prediction live in the classifier module, while the
API only queues jobs.

Reasoning: the project architecture says routers must not run inference or touch
external systems directly. Keeping the model code in `app/classifier/` lets the
worker, golden replay, and local scripts reuse the same inference path.

Consequence: the worker consumes an inference job, reads image bytes from blob
storage, calls the classifier runtime, writes an overlay PNG, and records the
prediction result. The API remains an HTTP and permission boundary, not a model
execution process.

---

## Sarah Shawraba: API and Services Decisions

### Cache reads with fastapi-cache2, 60-second TTL

Decision: use `fastapi-cache2` backed by Redis (DB 1) on all read routes (`GET /batches`,
`GET /batches/{id}`, `GET /predictions/recent`), with a 60-second TTL.

Reasoning: the latency brief requires cached reads at p95 < 50 ms and uncached at p95 < 200 ms.
Redis is already in the stack. A 60-second TTL is long enough to absorb burst reads between
inference jobs and short enough that a fresh classification appears within one TTL window.

Alternative: per-request DB query on every read. Ruled out — once the inference worker lands,
prediction rows arrive at burst rates and the API would saturate Postgres connection slots.

Consequence: the inference worker must invalidate the affected keys after writing a prediction
row. Cache invalidation is `CacheService`'s job; routers and repositories never call it directly.

### `CacheService` owns all cache invalidation

Decision: all cache-key invalidation is centralised in `app/services/cache_service.py`
(`invalidate_user`, `invalidate_batch`, `invalidate_recent_predictions`). Routers and
repositories do not call Redis directly.

Reasoning: scattered invalidation makes it impossible to reason about which cache entries are
fresh. A single service-layer owner makes auditing straightforward — if a key is ever stale, the
bug is in one place.

Alternative: invalidate inside the repository after each write. Rejected — repositories must not
know about the cache layer.

### `document_count` as ORM `@property`, not a DB column

Decision: `Batch.document_count` is a Python `@property` on the ORM model that counts associated
prediction rows, rather than a stored integer column.

Reasoning: a stored counter requires incrementing inside the same transaction as the prediction
insert, or the count goes stale. An ORM property always reflects the current relationship length
without extra bookkeeping.

Alternative: a `document_count` column maintained by the worker. Rejected — keeping a redundant
integer consistent with the true row count is a class of bug we do not want.

Consequence: `BatchUpdate` schema has no `document_count` field.

### `expire_on_commit=False` on async sessions

Decision: SQLAlchemy async sessions are created with `expire_on_commit=False`.

Reasoning: by default, SQLAlchemy expires all ORM attributes after a commit. In async code,
accessing a post-commit attribute triggers a lazy-load — a DB round-trip inside a running
coroutine — which async SQLAlchemy forbids. `expire_on_commit=False` keeps in-memory attribute
values alive after the commit.

Alternative: explicitly `refresh()` every object after commit. Rejected — verbose and
error-prone; easy to forget on any new code path.

### Relabel confidence check in the service layer

Decision: the confidence guard ("reviewer may only relabel predictions where top-1 < 0.7") is
enforced in `PredictionService.relabel_prediction`, not in the router.

Reasoning: business rules belong in the service layer. A guard in the router is easily duplicated
or bypassed if a second route calls the same service method.

Alternative: check in the router before calling the service. Rejected — the router should not
contain business logic.

### `owner_id` nullable to support scanner-ingested batches

Decision: `Batch.owner_id` is nullable in both the DB model and `BatchRead` schema.

Reasoning: the SFTP ingestion worker creates batches without a logged-in user. A non-null
`owner_id` would prevent the worker from writing the batch row entirely, or would require a fake
system-user record.

Consequence: `GET /batches` filtered by `owner_id` silently drops scanner-ingested batches for
reviewer and auditor roles. Admins need a role-branched query path in `BatchService.list_batches`
— known issue, scheduled fix.

---

## Mahdi El-Zein: Infra and Worker Decisions

### `MinioBlobClient` returns an `s3://` URI, not a presigned URL

Decision: `MinioBlobClient.upload_file` returns an `s3://bucket/key` URI.

Reasoning: presigned URLs embed auth tokens and a TTL — the adapter should not hard-code an
expiry that silently becomes a security parameter. The DB stores a stable object identifier;
HTTP-downloadable links are generated on demand by a separate method. A bare object key forces
callers to remember the bucket name; `s3://` is self-describing.

Alternative: return just the object key. Rejected because callers would still need to reconstruct
the bucket context.

### `RQClient.enqueue_job` uses a `{"func": ..., "kwargs": ...}` envelope

Decision: the enqueue payload is `{"func": "<dotted.import.path>", "kwargs": {...}}`. The RQ
worker process resolves and calls the function via standard Python import.

Reasoning: the task brief required the signature `enqueue_job(queue_name, payload) -> str` — the
adapter must not receive a function reference at its call site. Embedding the dotted path in the
payload keeps the adapter ignorant of which worker functions exist.

Alternative: map `queue_name → func_path` in settings. Rejected — a config change to add a new
job type adds unnecessary coupling between runtime config and code.

### SFTP at-most-once delivery: delete after enqueue

Decision: the ingestion worker deletes the remote file only after MinIO upload and RQ enqueue
both succeed. If either fails, the file stays on `/upload` for the next poll.

Reasoning: losing a customer's scan is worse than processing it twice. Downstream inference must
be idempotent on `(batch_id, filename)`.

Alternative: delete before reading. Rejected — an upload failure would silently destroy the scan.

### Queue and cache on the same Redis, separated by DB number

Decision: RQ uses Redis DB 0; `fastapi-cache2` uses Redis DB 1. The DB suffix is in the
connection string chosen by the caller, not hard-coded in the adapters.

Reasoning: one container, one volume, one healthcheck — and a `FLUSHDB` on the cache DB does not
take the job queue down with it.

Alternative: two Redis containers. Rejected — doubles operational complexity for the stack.

### Four-stage TIFF triage in the ingestion worker

Decision: every file from `/upload` passes four ordered checks: (1) path sanitisation against
traversal, (2) empty-file deletion, (3) wrong extension or oversized file deletion, (4) TIFF
magic-byte check — files that claim to be TIFFs but fail the magic check are quarantined, not
deleted.

Reasoning: a file with the right extension but wrong magic bytes is the signature of a disguised
executable or polyglot, not an honest mistake. Quarantining preserves evidence for an analyst.
Triage 3 collapses wrong-extension and oversized into one path because both produce the same
response (delete + warning log) and the structured `reason` field in the log already
distinguishes them.

Alternative: trust the extension and upload anything. Rejected — the pipeline would accept
arbitrary file types if a vendor misconfigures their drop folder.

### `run_classification` as an injectable seam

Decision: the inference worker bundles classify + overlay into a single
`run_classification(image_bytes) -> tuple[str, float, bytes]` that is injected via a `classify=`
kwarg. Tests pass a pure-Python stub.

Reasoning: the full classifier requires PyTorch and the ConvNeXt weights, which are not available
on CI. A single injectable seam exercises the full DB + blob + cache path without the ML stack.
Bundling classify and overlay (rather than two separate seams) keeps the mock symmetric with
production — overlay depends on the classifier output and they are naturally coupled.

Alternative: run the real classifier in every integration test. Rejected — adds ~5 s warmup per
run and makes CI depend on git LFS access.

### Overlay rendering stays in the worker, not in `app/classifier/`

Decision: `create_overlay_png` lives in `app/workers/inference.py`, not in
`app/classifier/inference.py`.

Reasoning: the classifier module is reserved for PyTorch math. Overlay rendering uses
`PIL.ImageDraw` and `ImageFont`, which are presentation concerns unrelated to model inference.

Alternative: move overlay to `app/classifier/`. Rejected — the classifier module would gain a
PIL dependency and become a mixed concern.

### `NullPool` + `asyncio.run` per inference job

Decision: the inference worker creates a fresh SQLAlchemy engine with `poolclass=NullPool` and
calls `asyncio.run(_persist(...))` for each job.

Reasoning: RQ workers are synchronous; SQLAlchemy 2.0 async requires an asyncio event loop.
`asyncio.run` spawns a fresh loop per call. asyncpg connections are bound to the loop that
created them — sharing a connection pool across `asyncio.run` calls checks out connections from
a dead loop. `NullPool` prevents this at a cost of ~30 ms of Postgres handshake per inference,
well within the 1-second inference budget.

Alternative: one shared persistent event loop in the worker process. Rejected — RQ's signal
handling and job lifecycle are designed for synchronous workers; hijacking the event loop
conflicts with that.

### `Settings(extra="ignore")` instead of `extra="forbid"`

Decision: the pydantic-settings `Settings` class uses `extra="ignore"` to drop unknown
environment variables silently.

Reasoning: the shared `.env` file carries compose-level variables (`POSTGRES_USER`,
`SFTP_PASSWORD`, etc.) that `Settings` does not declare as fields. With `extra="forbid"`,
alembic, pytest, and ad-hoc scripts that load `Settings` crash with a pydantic validation error.
`extra="ignore"` is the pydantic-settings convention for this situation. Security posture is
unchanged — each containerised service receives only the env vars in its compose `environment:`
block.

Alternative: maintain separate `.env` files per service. Rejected — too much duplication for a
team of four on a one-week deadline.

### `$$` in compose `vault-init` entrypoint

Decision: the `vault-init` entrypoint uses `$$VAR` instead of `$VAR` when referencing env vars
inside the `sh -c` string.

Reasoning: Docker Compose expands `$VAR` at parse time, before the container runs, producing
empty strings. `$$VAR` escapes the compose parser and passes a literal `$VAR` to the shell
inside the container, where the actual values live.

Consequence: this is a Compose YAML quirk that affects every multi-line entrypoint referencing
env vars; the only fix is `$$`.

---

## Racha Chamseddine: Auth, RBAC, and Audit Decisions

### fastapi-users over hand-rolled auth

Decision: JWT authentication is handled by the `fastapi-users` library, not a custom
implementation.

Reasoning: `fastapi-users` provides password hashing, token revocation hooks, and user CRUD for
free. The only custom code is `get_jwt_strategy`, which pulls the signing key from Vault instead
of env.

Alternative: bare PyJWT with a custom login endpoint. Rejected — would require re-implementing
secure password hashing, token validation, and the OpenAPI form that the API docs exercise.

### JWT secret loaded from Vault, never from env

Decision: the JWT signing key is fetched from `secret/jwt` in Vault at startup and stored in
`app.state.jwt_secret`. It is never written to disk, logs, or environment variables.

Reasoning: an env var appears in `docker inspect` output, CI logs, and any tool that dumps the
container environment. A Vault-sourced secret is accessible only to code that holds the Vault
token.

Alternative: `JWT_SECRET` as an environment variable in `.env`. Rejected — fails the brief's
requirement that signing keys not appear in env.

Consequence: if Vault is unreachable at startup the API calls `sys.exit(1)` and refuses to boot.

### Casbin file-based enforcer, not per-request DB adapter

Decision: the Casbin enforcer reads `policy.csv` at startup. Policy is also seeded into the
`casbin_rule` DB table, and the lifespan checks that the table is non-empty before allowing the
app to start.

Reasoning: a file-based enforcer adds no DB round-trip per `enforce()` call, keeping role-check
latency negligible. The DB table provides durability and a deploy guard — the app refuses to
start if the migration ran but the seed did not.

Alternative: pure DB adapter that queries `casbin_rule` on every `enforce()` call. Rejected —
adds a DB round-trip to every authenticated request.

### Casbin policy seeded via Alembic data migration

Decision: an Alembic revision uses `op.bulk_insert` to seed the Casbin role and permission
rules.

Reasoning: seeding through Alembic is automatic, repeatable, and version-controlled — every
fresh stack runs `alembic upgrade head` once and the policy is present. The alternative requires
either embedding DB credentials in the compose file or a separate seed script that is easy to
skip.

Alternative: a one-shot compose service running `psql` INSERT statements. Rejected — embeds DB
credentials in `docker-compose.yml`, the secret-management problem we explicitly wanted to avoid.

### `auditor` does not inherit `reviewer`

Decision: the Casbin inheritance graph has `admin → reviewer` and `admin → auditor`, but no
`auditor → reviewer` edge.

Reasoning: auditors are read-only observers of the audit trail; they must not be able to relabel
documents. The principle of least privilege requires the two roles to remain separate.

Alternative: `auditor` inherits `reviewer`. Rejected — a read-only compliance role must not have
document-write capabilities.

### Self-demotion blocked with 409, not 403

Decision: if an admin attempts to change their own role via `POST /users/admin/{uid}/role`, the
endpoint returns 409 Conflict.

Reasoning: 409 is more accurate than 403 — the admin has the permission to change roles; the
request is semantically conflicted with current resource state (demoting the last admin leaves no
admin in the system). 403 would imply a permission-check failure, which is misleading.

Consequence: without this guard, a single admin could lock the team out of admin-only routes
permanently, requiring a manual DB fix.

### `sys.exit(1)` in lifespan on any startup failure

Decision: any failure in the startup sequence (DB engine, Vault fetch, Casbin enforcer, policy
table check) calls `sys.exit(1)` immediately.

Reasoning: a container in a crash-loop is visible in `docker ps` and logs the reason on exit. A
container that starts in a degraded state (silent 500s because Vault was temporarily unreachable)
is invisible until a user files a bug. Docker Compose `restart: unless-stopped` handles the loop.

Alternative: log the failure and continue in degraded mode. Rejected — an API that starts without
a valid JWT secret or Casbin policy is a security hole.

### Vault dev mode via entrypoint override on Docker Desktop for Windows

Decision: the `vault` compose service overrides the image entrypoint to run `vault server -dev`
directly, with `VAULT_DISABLE_MLOCK=true`.

Reasoning: `hashicorp/vault`'s default `docker-entrypoint.sh` calls `setcap cap_ipc_lock+ep` on
the vault binary. Docker Desktop for Windows does not support `setcap` inside containers, causing
the container to crash before the dev server starts. Overriding the entrypoint skips that step.
`VAULT_DISABLE_MLOCK=true` prevents the vault binary itself from calling `mlock`, also
unsupported in that environment.

Consequence: dev-only workaround. Production Vault runs on a Linux host where `setcap` works and
the default entrypoint is correct.

### Audit log written in the same transaction as the triggering change

Decision: `AuditService.log_event` is called before `session.commit()`, inside the same session
as the change it records.

Reasoning: if the audit write and the business write are in separate transactions, a failure
between the two leaves the DB in an inconsistent state — a role change happened but was never
audited, or an audit row exists for a change that was rolled back. A single transaction makes
both writes atomic.

Alternative: write the audit row in a fire-and-forget background task. Rejected — audit rows are
a compliance artifact; best-effort delivery is not acceptable.
