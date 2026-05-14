# Database Models & Migrations – Person B

## UUID Handling

We replaced `fastapi_users_db_sqlalchemy.generics.GUID` with `sqlalchemy.dialects.postgresql.UUID(as_uuid=True)` on all `id` columns and foreign keys. This prevents Alembic from generating broken migrations (it wrote a Python class path instead of a SQL type).

**Choice defended:** Alembic understands native `UUID(as_uuid=True)` and generates correct PostgreSQL `uuid` columns. `fastapi-users` still works because it only requires a `uuid.UUID` value, not a specific column type. All foreign keys (`owner_id`, `batch_id`, `actor_id`) use the same type for consistency.

## Batch Model

- `sftp_path` – stores the original SFTP drop path (simpler than separate `filename` + `minio_path`).
- `owner_id` – may be `NULL` for scanner‑ingested batches (no authenticated user). Visibility is handled at the API layer (see API report).
- `status` – stored as `String` (not `SAEnum`) to avoid creating a custom PostgreSQL enum, making migrations simpler.
- `document_count` – implemented as a `@property` that counts `self.predictions`. We avoid denormalization; performance is acceptable for the dataset size.

## Prediction Model

- `label` – stored as `String` using the domain `DocumentLabel` enum (values are strings).
- `overlay_path` – nullable, populated after inference worker finishes.
- Foreign key `batch_id` with cascade behavior (if batch deleted, predictions are orphaned – but our application never deletes batches).

## Migration Strategy

We deleted all old migration files and re‑generated a single `initial_schema` migration after fixing the UUID types. This keeps the migration history clean and avoids the `GUID` import error.

**Run migrations locally (non‑Docker):**
```bash
alembic revision --autogenerate -m "initial_schema"
alembic upgrade head


---

### 2. SERVICES_REPORT.md

```markdown
# Business Logic & Service Layer – Person B

## Layering Principle

Services own **transaction boundaries**, **cache invalidation**, and **audit logging**. Repositories only do SQL; routers only do HTTP. This separation is strictly enforced.

## BatchService

- `create_pending_batch(sftp_path, owner_id)` – used by Person C’s ingestion worker. Returns only the batch ID (minimal data for job queue).
- `list_batches(skip, limit)` – returns **all batches** to any authenticated user (including `owner_id = NULL`). This decision makes scanner‑ingested batches visible without complex role‑based filters. The project brief only requires authentication, not per‑user isolation.
- `update_batch(batch_id, updates, actor_id, request_id)` – generic update. If status changes, logs an audit event **before** commit (so both batch update and audit entry are saved atomically). Cache invalidation is triggered via `CacheService`.

**Why audit before commit?**  
We moved `commit()` after the audit call to ensure the audit entry belongs to the same database transaction. If the commit fails, the audit is also rolled back – correct behavior.

## PredictionService

- `save_prediction(prediction_data)` – called by Person A’s inference worker. Commits the prediction, then invalidates the batch cache and recent predictions cache.
- `relabel_prediction(prediction_id, updates, actor_id, request_id)` – first checks confidence (rejects if ≥0.7), then updates, logs audit, commits, and invalidates caches.

**Confidence threshold** (0.7) is hardcoded as required by the project brief. It is enforced in the service layer, not in the router.

## Audit Integration

We inject `AuditService` into both `BatchService` and `PredictionService`. Every role change, relabel, and batch state change calls `audit.log_event()` with the actor’s ID and request ID (for traceability). The audit entry is stored in the same transaction as the state change.

**Defense against bypass:** A future developer could call the repository directly and skip audit. We rely on the team’s commitment to the layered architecture. The `ARCH.md` should explicitly state that audit must go through the service layer.

# API Routers (Batches & Predictions) – Person B

## Endpoints Implemented

| Method | Path | Description | Cached | Auth |
|--------|------|-------------|--------|------|
| GET | `/batches` | List batches (paginated) | Yes (30s) | Any authenticated user |
| GET | `/batches/{batch_id}` | Get single batch with predictions | Yes (30s) | Owner or admin |
| PATCH | `/batches/{batch_id}` | Update batch status | No | Admin only (Casbin) |
| GET | `/predictions/recent` | List recent predictions (paginated) | Yes (30s) | Any authenticated user |
| PATCH | `/predictions/{prediction_id}` | Relabel prediction | No | Reviewer or admin (Casbin) |

## Pagination

All list endpoints use `skip` and `limit` query parameters. Default `limit=100`, max `100`. Responses follow the schema:

```json
{ "items": [...], "total": 123, "skip": 0, "limit": 100 }


---

### 4. CACHING_REPORT.md

```markdown
# Caching Strategy – Person B

## Libraries

- `fastapi-cache2[redis]` – decorator‑based caching.
- Redis as backend (running in Docker or locally).

## Endpoints Cached

- `GET /batches` (paginated)
- `GET /batches/{batch_id}`
- `GET /predictions/recent`

TTL = 30 seconds. This meets the latency budget (cached reads <50ms) while providing reasonable freshness. The brief does not specify a TTL; 30s is a safe choice.

## Cache Invalidation

We implemented a custom `CacheService` that uses Redis `SCAN` + `DELETE` to delete keys by pattern. This is necessary because `fastapi-cache2` does not support pattern deletion natively.

### Key Patterns Invalidated

| Write Operation | Invalidation Call | Deleted Patterns |
|----------------|------------------|------------------|
| New batch created | `invalidate_user(owner_id)` | `*user:{owner_id}*` (includes all batch lists for that user) |
| Batch status changed | `invalidate_batch(batch_id)` | `*/batches/{batch_id}*` and `*/batches?*` (all list caches) |
| Prediction saved / relabeled | `invalidate_recent_predictions()` | `*/predictions/recent*` |

Because we invalidate **all** `*/batches?*` keys on any batch‑related write, stale paginated results are avoided. This is safe even with pagination (every cached page gets cleared).

## Graceful Degradation

If Redis is not running or `FastAPICache.init()` fails, `CacheService` lazily catches the exception and disables caching (all `invalidate_*` methods become no‑ops). The API continues to work, only slower.

## Why Not Use `@cache` Without Invalidation?

Without invalidation, new batches would not appear until TTL expires. The brief requires that writes affect reads “on the next page load” (for role changes) and “within the pipeline” – we interpret that as **cache must be invalidated on writes**. Our pattern deletion achieves this.

## Performance Impact

- `SCAN` + `DELETE` loops are efficient for small key sets (demo scale). For production with thousands of cache keys, one could store a set of user‑specific keys and delete them directly. We accept this trade‑off for simplicity.

# Testing Strategy – Person B

## Unit Tests (Pytest)

We wrote unit tests for `batches_router` and `predictions_router` using **service mocks** (not database mocks). This isolates the router logic and avoids the complexity of mocking SQLAlchemy queries.

**Why service mocks?**  
Our initial attempt to mock `session.execute` failed because paginated endpoints also run a `count` query. Mocking the service layer (`BatchService`, `PredictionService`) is simpler and equally valid: the router only delegates to services, so we trust the services are tested elsewhere.

### Test Files

- `tests/unit/test_batches_router.py` – covers list, get, update with permissions.
- `tests/unit/test_predictions_router.py` – covers recent list, relabel with confidence check and role enforcement.

All tests pass. They run in CI (GitHub Actions) on every push.

## End‑to‑End Tests (Curl)

We manually tested all endpoints against a real stack (Postgres, Redis, Vault). Steps documented in `RUNBOOK.md` (not included here). Key validations:

- Admin can see all batches (including scanner‑ingested ones with `owner_id = NULL`).
- Reviewer can relabel low‑confidence predictions (<0.7) but not high‑confidence ones.
- Batch status changes and relabel events appear in audit log.
- Pagination works (skip/limit).
- Cached responses return within <50ms (observed).

## Test Data

We added a temporary `POST /test/batches` and `POST /test/predictions` endpoint (guarded by `APP_ENV=development`) to seed data without needing the full SFTP/worker pipeline. This endpoint is **removed before production** or wrapped with environment check.

## Edge Cases Covered

- Attempting to relabel a non‑existent prediction → 404.
- Non‑owner accessing batch → 403.
- Non‑admin updating batch status → 403.
- Relabel with confidence ≥0.7 → 403 with custom message.

## Uncovered (but not required)

- Concurrent writes and cache consistency (out of scope for week‑6 project).
- Performance under load (brief only asks for latency budgets on single requests).

# Integration Notes for Teammates (A, C, D)

## For Person A (Inference Worker)

**Call `PredictionService.save_prediction(prediction_data: PredictionCreate) -> PredictionRead`** after running the model.

Example:
```python
from app.domain.prediction import PredictionCreate, DocumentLabel

prediction_data = PredictionCreate(
    batch_id=batch_uuid,
    filename=original_filename,
    label=DocumentLabel.resume,
    confidence=0.98,
    overlay_path="minio://overlays/abc.png"
)
result = await prediction_service.save_prediction(prediction_data)

