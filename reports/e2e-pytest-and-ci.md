# e2e-pytest-and-ci

**Branch:** `feature/mahdi-worker-ingestion`
**Author:** Person C
**Date:** 2026-05-13

---

## Scope

**Ships:**

- [tests/integration/test_ingest_pipeline.py](../tests/integration/test_ingest_pipeline.py)
  — proper pytest e2e test replacing the standalone runner. Uses
  fixtures for `sftp_client` / `blob_client` / `queue_client` /
  `db_engine`, a `fresh_state` fixture that truncates batches + clears
  `/upload` and `/quarantine` before AND after, and a single
  assertion-driven test that drops 4 files and verifies the four
  triage outcomes.
- [scripts/generate_test_drops.py](../scripts/generate_test_drops.py) —
  refactored. The file drops are now factored into
  `get_test_drops() -> dict[str, bytes]` and
  `drop_files(sftp, remote_dir)`. `main()` opens its own connection
  for ad-hoc manual use. The pytest test imports `drop_files`.
- [app/infra/sftp.py](../app/infra/sftp.py) — adds
  `SFTPClient.write_file(remote_path, data)` (symmetric with
  `read_file`/`read_partial`/`delete_file`/`move_file`).
- [pyproject.toml](../pyproject.toml) — `pythonpath = ["."]` under
  `[tool.pytest.ini_options]` so tests can import from `scripts/`.
- [.github/workflows/ci.yml](../.github/workflows/ci.yml) — `test` job
  now spins up `db` alongside `sftp`/`minio`/`redis`, runs
  `alembic upgrade head`, then `pytest tests/`.

**Removed:**

- `scripts/run_pipeline_integration.py` — its assertion logic moved
  into pytest. The standalone runner-with-JSON-output convention is
  retired (per Task 5 — pytest output is sufficient).

**Deliberately NOT shipped:**

- New `logs/postgres-integration/*.json` entries. Existing historical
  artifact left in place; the JSON-per-run convention is no longer
  produced by any script.
- Vault service in compose. Still Person D's territory; CI continues
  to skip starting `worker-ingest` (which requires Vault) and exercises
  the worker logic via `process_one` directly.

---

## Key decisions

### One pytest test, not four

The integration test runs `process_one` for all four files in one
test method rather than four separate parametrized tests. The
rationale: the four triage paths interact through shared SFTP state
(an empty `/upload` + a populated `/quarantine` is an end-state
assertion). Splitting into four tests would either need teardown to
re-drop fixtures (slow, brittle) or assume ordering (fragile).
Combined-state test keeps the assertions sharp.

### Cleanup fixture runs both before AND after

`fresh_state` truncates batches + clears `/upload` + `/quarantine`
twice: once at fixture setup, once at teardown. The "before" half
makes the test robust to whatever state previous tests left behind
(`test_infra_adapters.py::test_sftp_adapter` lists `/upload` but
doesn't mutate it, but the principle stands). The "after" half is
courtesy for whatever runs next, and for the human running pytest
locally who wants a clean stack afterwards.

### Engine fixture uses `NullPool`

Same reasoning as the worker: `process_one` opens its own
`asyncio.run` per file, so asyncpg connections cannot survive across
event loops. The fixture matches.

### `pythonpath = ["."]` instead of restructuring `scripts/`

Tests need to import `drop_files` from `scripts/generate_test_drops.py`.
The minimally-invasive way is the pythonpath setting; alternatives —
making `scripts/` a real package, adding sys.path hacks in a conftest,
or moving the helper to `app/` — were all worse for one of three
reasons (more files / runtime sys.path manipulation / putting test
data into the production package).

### Pillow added to CI test-tooling install

Pillow is in the `ml` optional-extra, not the main deps. The drop
fixtures need it (to build `valid_document.tiff`). Rather than
install the full `ml` extra (which would drag in torch and torchvision,
~3 GB and minutes of CI time), I install Pillow explicitly alongside
pytest.

### `VAULT_TOKEN=ci-noop` during the alembic step

`alembic/env.py` calls `get_settings()` to read `database_url`, and
`Settings` requires `vault_token` as a non-default field. Alembic
itself never touches Vault — the value is irrelevant. Passing a
placeholder keeps Settings happy without requiring a real Vault on
the CI runner.

---

## Cross-team touchpoints

| Touchpoint | Affects | Action |
|---|---|---|
| `pytest tests/` now requires Postgres on `localhost:5432` | Anyone running the suite locally | Bring up `db` via compose before `pytest`. README should be updated. |
| `scripts/generate_test_drops.py` API | Anyone calling it programmatically | `drop_files(sftp, remote_dir)` is the new entry point; the old script-only flow still works via `main()`. |
| CI test runtime | Reviewers watching CI duration | Adds ~3 s for the new e2e test plus ~10 s for Postgres boot + migration. Total ~30 s end-to-end for the test job. |

---

## Verification

```
$ docker compose up -d db redis minio sftp
$ DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/app \
  VAULT_TOKEN=local-test \
  uv run alembic upgrade head
$ DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/app \
  .venv/bin/python -m pytest tests/integration/ -v

tests/integration/test_infra_adapters.py::test_minio_blob_adapter PASSED
tests/integration/test_infra_adapters.py::test_redis_queue_adapter PASSED
tests/integration/test_infra_adapters.py::test_sftp_adapter         PASSED
tests/integration/test_infra_adapters.py::test_redis_cache_adapter  PASSED
tests/integration/test_ingest_pipeline.py::test_ingest_pipeline_e2e PASSED

============================== 5 passed in 4.47s ===============================
```

Both test files run side-by-side cleanly. The cleanup fixture
keeps the e2e test reproducible across runs (verified by running
twice in a row — second run also green).

---

## Follow-ups

1. **README**: add a short "Running tests locally" section once Person
   B's pyproject + this PR land — should reference `docker compose up
   -d db sftp minio redis` + `alembic upgrade head` + `pytest tests/`.
2. **Pytest marker for integration tests**: a `@pytest.mark.integration`
   marker on `tests/integration/*` would let
   `pytest -m 'not integration'` run cleanly when no stack is up.
   Useful for developer-machine workflows but not on the critical
   path.
3. **Vault in compose**: once Person D ships the `vault` service, the
   CI `test` job can also start `worker-ingest` and exercise the
   *deployed* worker against the live stack (vs the current pattern of
   importing `process_one` and driving it from inside the test). That
   would close the last gap between this test and a real smoke test.

> **See also:** [vault-secrets-and-ci.md § Known limitations](vault-secrets-and-ci.md#known-limitations) — CI does not exercise the Vault → worker credential fetch path. The tests inject adapter credentials from env vars, so a shape mismatch in seeded Vault secrets or a broken worker→Vault network path would pass CI silently.
