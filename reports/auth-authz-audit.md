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

5. **stdlib `logging` in infra adapters** — `blob.py`, `cache.py`, `sftp.py`, `queue.py` all use `import logging` instead of `structlog`. Logs from those files won't appear as structured JSON. Switch to `structlog.get_logger(__name__)`.
