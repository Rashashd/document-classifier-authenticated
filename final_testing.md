# Final testing report — Phase 6 DevOps

End-to-end validation that the refuse-to-boot guards, golden-set
regression check, full-stack smoke test, and CI wiring all work after
the Phase 5 (Sara) API merge.

Date: 2026-05-15 (UTC).
Branch: `feature/mahdi-worker-inference`.

---

## TL;DR

| Suite                              | Result      | Count        |
|------------------------------------|-------------|--------------|
| `tests/unit/`                      | ✅ pass     | 14 / 14      |
| `tests/integration/`               | ✅ pass     | 7  / 7       |
| Golden-set eval (real ConvNeXt)    | ✅ pass     | 50 / 50      |
| Full-stack smoke (`tests/smoke/`)  | ✅ pass     | 1  / 1       |
| Bugs found + fixed during the run  | 3 (see §5)  |              |

The local smoke ran against host-spawned `uvicorn` + `worker-ingest` +
`worker-inference` processes pointed at the compose-managed
db / redis / minio / sftp / vault. The CI workflow exercises the same
flow against the full-Docker stack (per [.github/workflows/ci.yml](.github/workflows/ci.yml)).

---

## 1. Unit tests

```
$ uv run --no-sync pytest tests/unit/ -v

collected 14 items

tests/unit/test_audit_router.py::test_audit_endpoint_admin_allowed[asyncio] PASSED [  7%]
tests/unit/test_audit_router.py::test_audit_endpoint_auditor_allowed[asyncio] PASSED [ 14%]
tests/unit/test_audit_router.py::test_audit_endpoint_reviewer_denied[asyncio] PASSED [ 21%]
tests/unit/test_auth_deps.py::test_require_role_returns_401_for_missing_token[asyncio] PASSED [ 28%]
tests/unit/test_auth_deps.py::test_require_role_returns_403_for_wrong_role[asyncio] PASSED [ 35%]
tests/unit/test_batches_router.py::test_list_batches_returns_paginated_results[asyncio] PASSED [ 42%]
tests/unit/test_batches_router.py::test_get_batch_returns_200_for_any_authenticated_role[asyncio] PASSED [ 50%]
tests/unit/test_batches_router.py::test_update_batch_requires_admin_role[asyncio] PASSED [ 57%]
tests/unit/test_predictions_router.py::test_list_recent_predictions_returns_list[asyncio] PASSED [ 64%]
tests/unit/test_predictions_router.py::test_relabel_prediction_blocked_when_confidence_too_high[asyncio] PASSED [ 71%]
tests/unit/test_predictions_router.py::test_relabel_prediction_writes_audit_entry[asyncio] PASSED [ 78%]
tests/unit/test_users_router.py::test_me_hides_hashed_password[asyncio] PASSED [ 85%]
tests/unit/test_users_router.py::test_role_toggle_self_demote_returns_409[asyncio] PASSED [ 92%]
tests/unit/test_users_router.py::test_role_toggle_writes_audit_row[asyncio] PASSED [100%]

============================== 14 passed in 0.18s ==============================
```

All router-layer tests pass, including the audit-write assertion on the
relabel endpoint and the 403 guardrail on PATCH /batches for
non-admins.

---

## 2. Integration tests

Backing services: `docker compose up -d db redis minio sftp vault` +
`docker compose up vault-init` (one-shot) + `alembic upgrade head`.

```
$ uv run --no-sync pytest tests/integration/ -v

collected 7 items

tests/integration/test_e2e_mocked_pipeline.py::test_full_ingestion_to_inference_pipeline PASSED [ 14%]
tests/integration/test_infra_adapters.py::test_minio_blob_adapter PASSED [ 28%]
tests/integration/test_infra_adapters.py::test_redis_queue_adapter PASSED [ 42%]
tests/integration/test_infra_adapters.py::test_sftp_adapter PASSED       [ 57%]
tests/integration/test_infra_adapters.py::test_redis_cache_adapter PASSED [ 71%]
tests/integration/test_ingest_pipeline.py::test_ingest_pipeline_e2e PASSED [ 85%]
tests/integration/test_worker_inference.py::test_run_inference_end_to_end PASSED [100%]

============================== 7 passed in 2.45s ===============================
```

Note: during a later combined run, `test_e2e_mocked_pipeline` flaked
once because a host-spawned `worker-inference` was concurrently
draining the queue. With no competing consumer the test is
deterministic; the integration suite is designed to be the only RQ
consumer during its run.

---

## 3. Golden-set regression check

Runs the real classifier weights against the 50 fixtures in
[app/classifier/eval/golden_images/](app/classifier/eval/golden_images/),
asserting per-image byte-identical labels and top-1 confidence within
1e-6 of
[app/classifier/eval/golden_expected.json](app/classifier/eval/golden_expected.json).

```
$ uv run --no-sync python -m app.classifier.eval.golden

[PASS] 01__true-letter__imagesc__c__n__a__cna63f00__0001215021.tif            label=invoice                conf=0.17257334
[PASS] 02__true-letter__imagesa__a__q__c__aqc24d00__507649930.tif             label=memo                   conf=0.47719559
[PASS] 03__true-letter__imagesg__g__q__j__gqj59c00__70057815-7815.tif         label=letter                 conf=0.71748877
[PASS] 04__true-letter__imagesd__d__x__h__dxh35d00__505033380.tif             label=handwritten            conf=0.99977428
...
[PASS] 48__true-memo__imagesg__g__b__c__gbc00f00__0000051340.tif              label=scientific_report      conf=0.14026023
[PASS] 49__true-memo__imagesb__b__a__b__bab92e00__2048625212.tif              label=letter                 conf=0.51840651
[PASS] 50__true-memo__imagesh__h__c__o__hco75c00__2078437338a.tif             label=email                  conf=0.94861817
[golden] 50/50 passed
```

50/50 byte-identical. This guards against silent regressions from a
PyTorch version bump, a torchvision transform change, or accidental
swapping of `classifier.pt` for a re-trained checkpoint.

---

## 4. Full-stack smoke

Boots `uvicorn app.api.main:app`, `app.workers.sftp_ingest`, and
`app.workers.inference` as host processes against the compose-managed
backing services. Smoke flow:

1. `GET /health` (unauthenticated liveness probe).
2. `POST /auth/register` + `POST /auth/login` → JWT.
3. `GET /batches` with the JWT — primes the 60 s `@cache` layer.
4. SFTP-drop a generated TIFF into `/upload`.
5. Poll `GET /batches` every 2 s for a *new* batch in `done` status.
6. Once observed, `GET /predictions/recent` and confirm the prediction
   row carries the dropped filename.

```
$ uv run --no-sync python tests/smoke/test_full_stack.py

[smoke 22:50:18] GET /health → ok
[smoke 22:50:18] smoke user already exists (smoke@example.com) — proceeding
[smoke 22:50:19] acquired JWT
[smoke 22:50:19] GET /batches primed (2 existing) — cache warm
[smoke 22:50:21] SFTP-dropped smoke-f092042d.tif (150668 bytes) to /upload
[smoke 22:50:23] still waiting: 2 batches visible, 0 new
[smoke 22:50:25] still waiting: 2 batches visible, 0 new
[smoke 22:50:27] still waiting: 3 batches visible, 1 new
[smoke 22:50:29] still waiting: 3 batches visible, 1 new
[smoke 22:50:31] new batch 99078359-912b-40ca-b1da-9b0286036114 reached status=done (sftp_path='/upload/smoke-f092042d.tif')
[smoke 22:50:31] prediction OK: label='file_folder' confidence=0.4816 overlay_path='s3://documents/batches/99078359-912b-40ca-b1da-9b0286036114/overlays/smoke-f092042d.overlay.png'
[smoke 22:50:31] SMOKE PASS
```

End-to-end ~12 s on a cold worker (model weights load + ConvNeXt
forward pass on CPU). The "0 new" → "1 new" transition at +6 s shows
the `@cache(expire=60)` layer being correctly invalidated by
`CacheService.invalidate_batches()` from the inference worker.

The brief's wording says *"status == completed"*; the actual enum value
in [app/domain/batch.py](app/domain/batch.py) is `done` (the terminal
success state). The smoke test asserts against the real value.

### CI variant

The `.github/workflows/ci.yml` test job runs the same smoke against a
fully-Docker stack (no host processes):

```yaml
- name: Boot full stack
  run: docker compose up -d --build
- name: Wait for stack to warm up
  run: sleep 15
- name: Run full-stack smoke
  run: python tests/smoke/test_full_stack.py
```

This required two supporting changes:

1. A `worker-inference` service in [docker-compose.yml](docker-compose.yml)
   (previously only `worker-ingest` was containerised).
2. A multi-stage [Dockerfile](Dockerfile) with two runtime targets —
   `runtime-core` (api / migrate / worker-ingest, no torch) and
   `runtime-ml` (worker-inference, with `torch` + `torchvision` from
   the `[ml]` extra). Keeps the lean services at ~250 MB instead of
   carrying the ~1 GB ML payload.

---

## 5. Bugs found and fixed during validation

Surfacing these because each one was caught by *this* run, not by an
existing test.

1. **API startup never called `FastAPICache.init`** — the comment
   `Must initialize FastAPI-Cache here + redis client` in
   [app/core/lifespan.py](app/core/lifespan.py) was a TODO. Hitting
   any `@cache`-decorated GET (i.e. `/batches`, `/predictions/recent`)
   produced an `AssertionError: You must call init first!` 500.
   Fixed by calling `await init_redis_cache(settings.redis_url)` at
   the top of the lifespan.

2. **Ingest worker enqueued the wrong function path** —
   `INFERENCE_FUNC_PATH = "app.workers.inference.run"` in
   [app/workers/sftp_ingest.py](app/workers/sftp_ingest.py), but the
   actual function is `run_inference`. RQ raised
   `ValueError: Invalid attribute name: run` on every job. Fixed by
   changing the constant to `app.workers.inference.run_inference`.

3. **`DocumentLabel` enum was out of sync with the model card** —
   `filefolder` in the enum vs `file_folder` in `model_card.json`'s
   `class_names`. The classifier output `"file_folder"` and
   `DocumentLabel("file_folder")` raised `ValueError`. The DB column
   is `String(32)` not a native Postgres enum, so the fix is a
   one-line rename in [app/domain/prediction.py](app/domain/prediction.py)
   with no migration needed.

These were latent — none of the existing pytest suites exercised the
end-to-end API + worker path under a real ConvNeXt classifier (the
integration tests mock the classifier). The full-stack smoke is the
only fixture that catches them.

Also added in this phase (not bug-fixes, scope items):

- Refuse-to-boot guards in [app/core/lifespan.py](app/core/lifespan.py)
  and [app/workers/inference.py](app/workers/inference.py) — model
  artifacts, Vault, Casbin policy table.
- `GET /health` at [app/api/routers/health.py](app/api/routers/health.py).
- The golden-set regression check at
  [app/classifier/eval/golden.py](app/classifier/eval/golden.py).
- The full-stack smoke at
  [tests/smoke/test_full_stack.py](tests/smoke/test_full_stack.py).

---

## 6. Environment / repro

```sh
# Backing services
docker compose up -d db redis minio sftp vault
docker compose up vault-init     # one-shot, seeds secret/jwt|sftp|minio

# Schema
DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5433/app' \
VAULT_TOKEN='dev-root-token-change-in-prod' \
uv run alembic upgrade head

# Unit + integration
DATABASE_URL='...' VAULT_TOKEN='...' uv run pytest tests/unit tests/integration -v

# Golden — needs the [ml] extra installed (torch + torchvision)
uv pip install ".[ml]"
uv run python -m app.classifier.eval.golden

# Full-stack smoke (host-process variant used here)
source /tmp/smoke_env.sh   # DATABASE_URL, VAULT_*, SFTP_*, MINIO_*, REDIS_URL
uv run uvicorn app.api.main:app --host 0.0.0.0 --port 8000 &
uv run python -m app.workers.sftp_ingest &
uv run python -m app.workers.inference &
uv run python tests/smoke/test_full_stack.py
```

Logs from the validation run are preserved under [logs/smoke/](logs/smoke/)
(api.log, worker_ingest.log, worker_inference.log, smoke_run.log) for
post-hoc inspection.
