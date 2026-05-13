# auth / authz / audit slice

**Branch:** `rasha/auth-part`
**Author:** Person D (Rasha)
**Dates:** 2026-05-12 – 2026-05-13
**Status:** ✅ All 8 unit tests passing, app boots end-to-end.

---

## What was built

### Authentication — fastapi-users + Vault JWT

| File | What it does |
|---|---|
| [app/domain/user.py](../app/domain/user.py) | `UserRead`, `UserCreate`, `UserUpdate` schemas. Inherits fastapi-users base classes. Adds `role` (enum: `admin`, `reviewer`, `auditor`) and `created_at`. |
| [app/services/auth_service.py](../app/services/auth_service.py) | fastapi-users wiring: `UserManager`, `get_jwt_strategy`, `auth_backend`, `fastapi_users` instance. JWT secret is pulled from Vault at startup — never from env. |
| [app/api/routers/auth.py](../app/api/routers/auth.py) | Mounts fastapi-users register + login/logout routes. |
| [app/infra/vault.py](../app/infra/vault.py) | `VaultClient.get_secret(path)` — KV v2, raises `RuntimeError` on any failure. |
| [app/core/lifespan.py](../app/core/lifespan.py) | Startup sequence: DB engine → Vault JWT secret → Casbin enforcer. Calls `sys.exit(1)` if any step fails so the container never silently starts broken. |

---

### Authorization — Casbin RBAC

| File | What it does |
|---|---|
| [app/casbin/model.conf](../app/casbin/model.conf) | RBAC model: `sub` = user role, `obj` = required role, role inheritance via `g`. |
| [app/casbin/policy.csv](../app/casbin/policy.csv) | Seed rules. See table below. |
| [app/api/deps.py](../app/api/deps.py) | `get_current_user` (fastapi-users bearer check) and `require_role(*roles)` (Casbin enforce). |

**Role permissions:**

| Role | Can do |
|---|---|
| `reviewer` | Edit and relabel documents (reviewer routes) |
| `auditor` | Read audit log only — **cannot** access reviewer routes |
| `admin` | Everything: inherits both reviewer and auditor |

`require_role("admin", "auditor")` on the audit endpoint means: allow if the user's role satisfies *either* — reviewer is explicitly excluded.

---

### User management routes

**File:** [app/api/routers/users.py](../app/api/routers/users.py)

| Route | Auth | Notes |
|---|---|---|
| `GET /users/me` | Any active user | Returns `UserRead` — `hashed_password` never exposed |
| `GET /users` | admin only | Lists all users |
| `POST /users/admin/{uid}/role?role=<role>` | admin only | Toggles a user's role. Returns 409 if the admin targets themselves (self-demotion blocked). Writes an audit row on every successful change. |

---

### Audit routes and persistence

| File | What it does |
|---|---|
| [app/repositories/audit_repo.py](../app/repositories/audit_repo.py) | `insert`, `list_all`, `list_by_actor` — all async. |
| [app/services/audit_service.py](../app/services/audit_service.py) | `AuditService(session).log_event(actor_id, action, target, request_id)` — call this from anywhere to write an audit row. |
| [app/api/routers/audit.py](../app/api/routers/audit.py) | `GET /audit` — admin and auditor only, returns last 200 entries newest-first. |

**Coupling note:** `log_event()` is designed to be called from `batch_service` and `prediction_service` on every relabel and batch state change. Pass `actor_id` from the JWT subject, `action` as a short verb (e.g. `"relabel"`, `"batch_status_change"`), and `target` as the resource path.

---

### DB models

**File:** [app/db/models.py](../app/db/models.py)

Three tables owned by this slice:

| Table | Key columns |
|---|---|
| `users` | `id` (UUID), `email`, `hashed_password`, `role`, `created_at` |
| `audit_entries` | `id`, `actor_id` → `users.id`, `action`, `target`, `request_id`, `timestamp` |
| `casbin_rule` | `ptype`, `v0`–`v5` — shape required by the SQLAlchemy Casbin adapter |

---

### Unit tests

| File | Tests |
|---|---|
| [tests/unit/test_auth_deps.py](../tests/unit/test_auth_deps.py) | Missing token → 401; wrong role → 403 |
| [tests/unit/test_users_router.py](../tests/unit/test_users_router.py) | `/me` hides `hashed_password`; self-demotion → 409; role toggle writes audit row (flush called ≥ 2×) |
| [tests/unit/test_audit_router.py](../tests/unit/test_audit_router.py) | Admin allowed; auditor allowed; reviewer denied → 403 |

All tests use mocked sessions and a mocked Casbin enforcer — no DB required to run them.

```sh
python -m pytest tests/unit/ -v
# 8 passed in 0.24s
```

---

## What teammates need to know

1. **Calling `log_event`** — inject `AuditService` with the route's session and call:
   ```python
   await AuditService(session).log_event(
       actor_id=current_user.id,
       action="relabel",
       target=f"/predictions/{prediction_id}",
   )
   ```
   Do this *before* `session.commit()` so the audit row is in the same transaction.

2. **Protecting a route** — use `require_role` from `app.api.deps`:
   ```python
   from app.api.deps import require_role

   @router.post("/something")
   async def do_something(
       current_user: Annotated[User, Depends(require_role("admin", "reviewer"))],
   ):
       ...
   ```

3. **Getting the session** — always use `Depends(get_async_session)` from `app.db.session`. The engine lives on `app.state` and is created once at startup.

4. **`batch_service.py` uses a sync `Session`** — this will block the FastAPI event loop if called from an async route. It needs to be migrated to `AsyncSession` before the demo.

5. **stdlib `logging` in infra adapters** — fixed. `blob.py`, `cache.py`, `sftp.py`, `queue.py` all switched to `structlog.get_logger(__name__)` during this session.

---

## Key decisions

**fastapi-users over hand-rolled auth** — Gives password hashing, token revocation hooks, and user CRUD for free. The only custom code is `get_jwt_strategy`, which pulls the secret from Vault instead of env. Alternative (PyJWT manually) would have required duplicating what fastapi-users already does correctly.

**JWT secret from Vault, not `.env`** — The secret is loaded at startup into `app.state.jwt_secret` and never written to disk or logs. If Vault is unreachable the app refuses to start (`sys.exit(1)`). Alternative (env var) would have meant the secret appears in `docker inspect` output and CI logs.

**Casbin file-based enforcer, DB-backed check** — The enforcer reads `policy.csv` (fast, no DB round-trip per request). The lifespan also checks `casbin_rule` table is non-empty at boot — this guards against a deploy where the migration ran but the seed didn't. Alternative (DB adapter only) would have added a DB query on every `enforce()` call.

**Casbin policy seeded via Alembic data migration** — `b1c2d3e4f5a6_seed_casbin_policy.py` runs `op.bulk_insert` so seeding is automatic, repeatable, and version-controlled. Alternative (compose entrypoint with inline SQL) was rejected because it would embed DB credentials in `docker-compose.yml`.

**`auditor` does not inherit `reviewer`** — Auditors are read-only observers of the audit trail; they must not be able to relabel documents. Casbin has `g, admin, reviewer` and `g, admin, auditor` but no `g, auditor, reviewer`. Alternative (auditor inherits reviewer) would have violated the principle of least privilege.

**Self-demotion blocked with 409** — An admin who demotes themselves leaves no admin in the system. The guard is in `set_user_role` before the DB write; the 409 is a client error (bad request semantics) not a 403. Alternative (allow and document) was ruled out because it produces an unrecoverable state without a manual DB fix.

**`sys.exit(1)` in lifespan on any startup failure** — A container in a crash-loop is visible in `docker ps` and alerts. A container that started silently broken (Vault unreachable, empty policy) would serve 500s until someone noticed. Docker Compose `restart: unless-stopped` handles the loop.

**Vault dev mode via entrypoint override** — `hashicorp/vault` image's `docker-entrypoint.sh` calls `setcap` which fails on Docker Desktop for Windows. Overriding the entrypoint to `vault server -dev ...` skips that script. `VAULT_DISABLE_MLOCK=true` stops the vault binary itself from trying mlock. This is dev-only; production Vault runs on a dedicated host.

---

## Open questions / follow-ups

- `cache_service.invalidate_user(uid)` not yet called in `set_user_role` — waiting on Person B to ship `cache_service.py`. One line to add at `users.py:52`.
- `batch_service.py` uses sync `Session` — needs migration to `AsyncSession` before demo (Person B's task).
- Frontend is now Rasha's responsibility — not yet started.
