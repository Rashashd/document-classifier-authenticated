# sara-api-merge

**Branch:** `feature/mahdi-worker-inference`
**Author:** Person C
**Date:** 2026-05-14

---

## What landed

Merge of `origin/feature/sara-api` into the local feature branch. Sara
brings the API surface (routers + cache service); this merge stitches
it to the worker code.

**New from Sara:**

- `app/api/routers/batches.py` — `GET /batches`, `GET /batches/{id}`.
- `app/api/routers/predictions.py` — `PATCH /predictions/{id}/relabel`.
- `app/api/routers/test_data.py` — seed helper.
- `app/services/cache_service.py` — `CacheService` with
  `invalidate_user / invalidate_batch / invalidate_recent_predictions`.
- Richer `BatchService` (read + update methods) and `BatchRepository`.
- `PredictionService` with full CRUD + reviewer relabel.

---

## Conflict resolutions

Eight files in conflict. Resolutions per the user-approved plan:

| File | Resolution |
|---|---|
| `.gitignore` | Union; kept all entries from both. |
| `app/db/session.py` | Sara's version (`expire_on_commit=False` is correct). |
| `app/db/models.py` | Sara's (single-line email column). `owner_id nullable=True` already in place. |
| `app/domain/batch.py` | Sara's `BatchUpdate` (removed `document_count` since it's now an ORM `@property`). |
| `app/repositories/batch_repo.py` | Mine for the docstring + clean imports. Dropped Sara's duplicate `create_batch` at file end — it referenced `self.session` but the class uses `self._session` (would have raised AttributeError). |
| `app/services/batch_service.py` | Sara's constructor (optional `cache_service` / `audit_service`). `create_pending_batch` takes `owner_id: uuid.UUID \| None` (worker passes `None`); cache invalidation skipped when owner is None. |
| `app/services/prediction_service.py` | **Union.** Sara's full CRUD + relabel kept verbatim. Added `save_prediction_and_complete_batch` for the worker — uses Sara's `CacheService` (no inline Redis SCAN). |
| `pyproject.toml` | Dropped the lonely blank-line conflict. |

---

## Worker rewiring

[app/workers/inference.py](../app/workers/inference.py) updated:

- Drops `cache_redis: AsyncRedis` kwarg from `run_inference`.
- Adds `redis_url: str | None` kwarg; default reads `REDIS_URL` env.
- `_persist` now constructs `CacheService` + `AuditService`
  and passes them to `PredictionService(session, cache_service, audit_service)`.
- Calls `FastAPICache.reset()` then `init_redis_cache(redis_url)` at
  the top of every `asyncio.run`. `reset()` is required because
  `FastAPICache.init()` short-circuits on the second call, which
  would leak a closed-loop AsyncRedis client across files.
- `finally` block explicitly closes the backend's Redis client before
  the asyncio loop ends, to prevent GC-after-loop-close errors in
  pytest fixtures.

Net change: ~15 LOC.

## Test updates

Two tests previously passed a `cache_redis` fixture:

- `tests/integration/test_worker_inference.py` — fixture removed,
  test parameters updated, `redis_url=REDIS_URL` passed to
  `run_inference`.
- `tests/integration/test_e2e_mocked_pipeline.py` — same.

No assertion changes; behaviour identical.

## Files dropped

- `repl_db.py` / `test_db.py` did NOT come through the merge — they
  were already deleted from master before Sara's branch picked up.
  No action needed.

---

## Verification

```sh
docker compose up -d db redis minio sftp vault
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/app \
  VAULT_TOKEN=dev-root-token-change-in-prod \
  uv run alembic upgrade head
DATABASE_URL=... uv run pytest tests/ -v
# → 15 passed in 16.43s
```

All 15 tests pass: 7 integration (incl. the full pipeline e2e) +
8 unit (Rasha's auth-deps / users / audit router tests).

---

## Follow-ups

1. **`app/api/routers/test_data.py`** is mounted in `app/api/main.py`.
   That's a seed helper; should not ship in production. Flagged for
   either guarding behind `settings.app_env == "dev"` or moving to
   `scripts/`.
2. **Worker cache-invalidation pattern** is correct but heavy — each
   prediction re-inits FastAPICache. Acceptable at our throughput;
   revisit if the inference worker ever scales out.
3. **PredictionService.save_prediction** vs. `save_prediction_and_complete_batch`
   are siblings now. The worker uses the latter; `save_prediction`
   exists for hypothetical future code paths that persist without
   flipping batch status. If nothing calls `save_prediction` in 30
   days, delete it.
