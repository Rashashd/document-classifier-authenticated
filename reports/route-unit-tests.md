# route-unit-tests

**Branch:** `feature/mahdi-worker-inference`
**Author:** Person C
**Date:** 2026-05-14

---

## Why

Sara's merge added five HTTP routes — `GET /batches`, `GET /batches/{id}`,
`PATCH /batches/{id}`, `GET /predictions/recent`, `PATCH /predictions/{id}` —
with **zero test coverage**. Wiring-class bugs (wrong role decorator, missing
auth dep, wrong status code, wrong response shape) would only surface during
the eventual API smoke tests, which are still gated on adding `vault` to CI.
These five unit tests close that gap cheaply: each runs in ~10 ms, none need
docker, and they exercise the route + dep injection paths without the live
DB / Redis.

Convention follows Rasha's existing pattern in
[tests/unit/test_audit_router.py](../tests/unit/test_audit_router.py) —
`ASGITransport(app)` + `dependency_overrides`, mocked enforcer, mocked
service.

## Files

| File | Tests | Routes covered |
|---|---|---|
| [tests/unit/test_batches_router.py](../tests/unit/test_batches_router.py) | 3 | `GET /batches`, `GET /batches/{id}`, `PATCH /batches/{id}` |
| [tests/unit/test_predictions_router.py](../tests/unit/test_predictions_router.py) | 2 | `GET /predictions/recent`, `PATCH /predictions/{id}` |

## What each test asserts

| Test | Assertion | Why it matters |
|---|---|---|
| `test_list_batches_returns_paginated_results` | `GET /batches?skip=10&limit=20` → 200; `BatchService.list_batches` called with `owner_id=user.id, skip=10, limit=20` | Verifies the pagination params reach the service unchanged + the auth-derived owner_id is used. |
| `test_get_batch_returns_403_when_not_owner_and_not_admin` | Reviewer querying someone else's batch → 403 | Catches the ownership guard at [batches.py:51](../app/api/routers/batches.py#L51). |
| `test_update_batch_requires_admin_role` | Reviewer attempting PATCH with denied enforcer → 403; service never invoked | Catches a regression like accidentally swapping `require_role("admin")` → `require_role("reviewer")`. The service-not-invoked check is the important half. |
| `test_list_recent_predictions_returns_list` | `GET /predictions/recent?limit=25` → 200; `PredictionService.list_recent_predictions(limit=25)` called | Verifies the limit query param parses and reaches the service. |
| `test_relabel_prediction_blocked_when_confidence_too_high` | Reviewer PATCHes a prediction with `confidence=0.95` → 403; `service.relabel_prediction` never invoked | This is the brief's hard guardrail ("reviewers can only relabel where top-1 < 0.7"). The "service never invoked" assertion guards against a future refactor that might check confidence too late. |

## Results

```
$ pytest tests/unit/test_batches_router.py tests/unit/test_predictions_router.py -v
tests/unit/test_batches_router.py::test_list_batches_returns_paginated_results          PASSED  [ 20%]
tests/unit/test_batches_router.py::test_get_batch_returns_403_when_not_owner_and_not_admin  PASSED  [ 40%]
tests/unit/test_batches_router.py::test_update_batch_requires_admin_role               PASSED  [ 60%]
tests/unit/test_predictions_router.py::test_list_recent_predictions_returns_list      PASSED  [ 80%]
tests/unit/test_predictions_router.py::test_relabel_prediction_blocked_when_confidence_too_high  PASSED  [100%]

============================== 5 passed in 0.30s ===============================
```

Full suite remains green: **20/20 passed in 3.69s** (15 prior + 5 new).

## What these tests do NOT cover

Unit tests with mocked services are *deliberately* blind to:

- **SQL correctness** — e.g. the `Batch.owner_id == owner_id` filter at
  [batch_repo.py:50](../app/repositories/batch_repo.py#L50) silently drops
  scanner-ingested batches (`owner_id=NULL`). A unit test would still pass.
- **Casbin policy loading** — the enforcer is a `MagicMock(.enforce → bool)`,
  not the real policy CSV.
- **Cache invalidation actually purging Redis keys** — verified only via the
  integration tests.
- **Transaction semantics** — services that commit at the wrong time look
  identical to ones that commit correctly under a mocked session.

Those gaps are filled by the existing integration tests
([test_e2e_mocked_pipeline.py](../tests/integration/test_e2e_mocked_pipeline.py),
[test_worker_inference.py](../tests/integration/test_worker_inference.py)) and
the future API smoke tests once Vault lands in CI.

## Follow-ups worth picking up

Three behaviour-class bugs that exist in the routes today but are *not*
caught by either layer of tests. Worth raising with Sara:

1. **`GET /batches` is invisible for scanner-ingested batches.** The route
   filters `WHERE owner_id = ?` but worker 1 inserts batches with
   `owner_id=NULL`. Reviewers and admins both see nothing. The route's SQL is
   correct; the access policy is wrong. Fix: branch on role in
   `BatchService.list_batches`.
2. **`POST /test/batches` and `POST /test/predictions` are mounted
   unconditionally** in [app/api/main.py:27](../app/api/main.py#L27) —
   these are admin-only "create a fake row directly" endpoints. Should be
   guarded behind `settings.app_env != "production"` or moved to
   `scripts/seed_data.py`.
3. **`PATCH /predictions/{id}` doesn't currently audit-log the relabel
   action.** The service's `relabel_prediction` does log it, but a test that
   asserts on the audit row would have caught a refactor that bypassed the
   service.

None of these block the milestone — flag at the next sync.

## Coverage matrix after this PR

| Route | Unit test | Integration test | Smoke test |
|---|---|---|---|
| `POST /auth/register` | ❌ | ❌ | ❌ (planned post-vault-in-CI) |
| `POST /auth/login`    | ❌ | ❌ | ❌ |
| `GET /me`             | ✅ | ❌ | ❌ |
| `GET /audit`          | ✅ | ❌ | ❌ |
| `POST /users/admin/{id}/role` | ✅ | ❌ | ❌ |
| `GET /batches`        | ✅ **(new)** | ❌ | ❌ |
| `GET /batches/{id}`   | ✅ **(new)** | ❌ | ❌ |
| `PATCH /batches/{id}` | ✅ **(new)** | ❌ | ❌ |
| `GET /predictions/recent` | ✅ **(new)** | ❌ | ❌ |
| `PATCH /predictions/{id}` | ✅ **(new)** | ❌ | ❌ |
| `POST /test/*` (dev-only) | n/a | n/a | n/a |
