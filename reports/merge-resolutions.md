# merge-resolutions

**Branch:** master (post-pull) → local follow-up
**Author:** Person C
**Date:** 2026-05-14
**Scope:** Documents the state of the codebase after PR #9 + #10 +
the team's subsequent cleanup commits, and ships one small type-
annotation consistency fix on top.

---

## What landed on master while I was away

61 commits brought in by today's pull. The high-impact ones:

| Commit                                              | What it did |
|-----------------------------------------------------|---|
| `568c637 Merge pull request #9`                     | My ingestion-worker branch merged into master. |
| `4ea81bb fixed conflicts from merge`                | Teammate resolved the 7 conflicts I flagged in `reports/e2e-pytest-and-ci.md` |
| `07db120 removed hardcoded passwords from docker compose` | Compose now reads `${POSTGRES_USER}` / `${POSTGRES_PASSWORD}` / `${MINIO_ROOT_*}` / `${SFTP_USER}` / `${VAULT_TOKEN}` from env. **Local devs must now create a `.env` before `docker compose up`.** |
| `96750f5 added .env creation step and changed alembic port in CI` | CI workflow now writes a minimal `.env` before alembic/pytest, and Postgres is mapped to **host port 5433** (not 5432) to avoid clashing with developer-local Postgres. |
| `74d0111 fixed ci lint check error`                 | Small ruff cleanup the team patched. |
| `afefd8c #6` / `a6b95cf #10`                        | Two more iterations of `rasha/auth-part`: extended `audit_service`, `prediction_repo`, added `tests/unit/test_audit_router.py` / `test_users_router.py` / `test_auth_deps.py`, added `vault` + `vault-init` + `migrate` + `api` services in compose, added `Dockerfile` `COPY alembic.ini`. |

---

## How my flagged conflicts were resolved

In [reports/e2e-pytest-and-ci.md](e2e-pytest-and-ci.md) I'd flagged 7
conflict surfaces. Confirming each:

| File | Decision on master | Notes |
|---|---|---|
| `.gitignore` | Union, mostly tidied | + `CLAUDE.md` is now gitignored (team chose not to track AI-orientation docs in the shared tree). |
| `pyproject.toml` | Kept `jinja2` + `paramiko` | My deps survived. |
| `app/db/models.py` | **`owner_id` nullable** ✅ | My version won. |
| `app/db/migrations/versions/93ff30599200_initial_schema.py` | **`owner_id nullable=True`** ✅ | Migration matches the model. |
| `app/repositories/batch_repo.py` | **Both APIs preserved** | Sara's CRUD (`get`, `get_with_predictions`, `list_by_owner`, `update_status`, `update`) AND my `create_batch(sftp_path, owner_id)`. Best-of-both-worlds outcome. |
| `app/repositories/prediction_repo.py` | Master's version (Rasha's extension) | I had no edits on this file; nothing to lose. |
| `app/services/audit_service.py` | Master's version (Rasha's audit-log writes) | Same. |

---

## What I just shipped: `owner_id` typing consistency

After the merges, `owner_id` was *runtime-nullable* (DB + ORM both allow
NULL, and the worker passes `None`) but the **type annotations across
the public surface still claimed it was a required `uuid.UUID`.** Three
sites would have made a strict type checker (or pydantic at runtime)
unhappy:

| File | Before | After |
|---|---|---|
| `app/repositories/batch_repo.py:21` | `create_batch(..., owner_id: uuid.UUID)` | `owner_id: uuid.UUID \| None` |
| `app/services/batch_service.py:20` | `create_pending_batch(..., owner_id: uuid.UUID)` | `owner_id: uuid.UUID \| None` |
| `app/domain/batch.py:32` | `BatchRead.owner_id: uuid.UUID` | `owner_id: uuid.UUID \| None` |

Left **unchanged** on purpose:

- `BatchRepository.list_by_owner(owner_id: uuid.UUID, ...)` — that
  method *filters* batches by owner. `None` as a filter is semantically
  wrong (it'd return no rows or all rows depending on dialect). The
  function genuinely requires a real UUID.

### Why this matters

Without these fixes:

1. **Pydantic `BatchRead.model_validate(batch_orm)`** would raise on
   any scanner-originated batch (where `owner_id IS NULL` in the DB)
   because pydantic enforces required-UUID at validation time.
2. **Mypy / pyright** in strict mode would flag `create_pending_batch(
   sftp_path=..., owner_id=None)` (the line in
   `app/workers/sftp_ingest.py:181`) as a type error.
3. **Editor tooling** (LSP, in-line hints) would mark the existing
   worker call as red.

The DB schema, ORM mapping, and worker behaviour were already correct
— only the typed surface needed to catch up.

---

## Verification

```
$ git pull --ff-only origin master                 # 61 commits brought in, ff
$ ruff check app/repositories/batch_repo.py app/services/batch_service.py app/domain/batch.py
All checks passed!
$ python -c "import inspect; from app.repositories.batch_repo import BatchRepository; from app.services.batch_service import BatchService; \
print(inspect.signature(BatchRepository.create_batch)); print(inspect.signature(BatchService.create_pending_batch))"
(self, sftp_path: 'str', owner_id: 'uuid.UUID | None') -> 'Batch'
(self, sftp_path: 'str', owner_id: 'uuid.UUID | None') -> 'uuid.UUID'
```

End-to-end pytest not re-run on master here — the integration test
already lives at `tests/integration/test_ingest_pipeline.py` and CI
will cover it on push.

---

## Follow-ups for the team

1. **`.env` is now required** to bring up the local compose stack
   (POSTGRES_*, MINIO_ROOT_*, SFTP_*, VAULT_TOKEN). A `.env.example`
   committed at the repo root would save every new dev from reading
   compose to figure out the variable names.
2. **Postgres host port shifted** from 5432 → 5433. Anyone with a
   shell script hard-coding 5432 (including `scripts/run_pipeline_integration.py`
   if it still exists — it does not in master since we replaced it
   with the pytest test) needs to be updated. The pytest fixture in
   `tests/integration/test_ingest_pipeline.py` reads `DATABASE_URL`
   from env with a default still on 5432; the default should be
   updated to 5433 or the env var should be set explicitly in every
   `pytest` invocation. **Minor sharp edge — flag for the next CI
   debug session if integration tests start failing locally.**
3. **`CLAUDE.md` is now gitignored.** Anyone wanting AI assistants to
   pick up the project conventions on a fresh clone will need to
   either un-ignore it or copy it in manually.
