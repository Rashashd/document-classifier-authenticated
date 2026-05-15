# vault-secrets-and-ci

**Branch:** `feature/mahdi-worker-ingestion`
**Author:** Person C
**Date:** 2026-05-13

---

## Scope

**Ships:**

- [app/workers/sftp_ingest.py](../app/workers/sftp_ingest.py) — Vault
  bootstrap before the polling loop; SFTP and MinIO credentials are
  no longer read from env vars.
- [.github/workflows/ci.yml](../.github/workflows/ci.yml) — two-job
  pipeline (lint + test) triggered on every push and on PRs into
  `master`/`main`.
- This report.

**Deliberately does NOT ship:**

- Vault service in `docker-compose.yml`, and the `VAULT_ADDR` /
  `VAULT_TOKEN` env wiring for the `worker-ingest` service. Out of
  scope for the task; the worker container will refuse to boot in
  the current compose stack until that's added — flagged below as
  the immediate follow-up.
- Smoke / E2E tests in CI, and the ML golden-set replay test. Per
  the task brief, those land in a later PR once Person A's ML models
  are ready.

---

## Why the worker bootstraps Vault itself

The worker is a standalone Python process — it does not share the
FastAPI app's `lifespan`. `app/core/lifespan.py` cannot be reused; it
only runs when `uvicorn` serves the API. The pattern in this PR
mirrors `lifespan`'s but in plain `sys.exit(1)` form so an
orchestrator (compose, Kubernetes) observes the boot failure cleanly.

Boot order in [app/workers/sftp_ingest.py:main()](../app/workers/sftp_ingest.py):

1. `configure_logging()` — JSON renderer + stdlib bridge.
2. `log.info("ingest.boot", ...)` — boot event for observability.
3. **`fetch_vault_secrets()`** — new step.
4. `build_sftp(creds)`, `build_blob(creds)`, `build_queue()`.
5. `sftp.connect()`, `blob.startup()`.
6. `while True: ...`.

The Vault step happens **before** any external connect, so a missing
secret or unreachable Vault aborts the boot without leaving a half-
connected client lying around.

---

## Secret layout in Vault

`fetch_vault_secrets()` reads two KV v2 entries, each containing a
credential pair:

| Path (mount-relative) | Keys (project convention)                |
|---|---|
| `sftp`  | `{"username": ..., "password": ...}`     |
| `minio` | `{"access_key": ..., "secret_key": ...}` |

Paths can be overridden per-environment via `VAULT_SFTP_PATH` and
`VAULT_MINIO_PATH`. `VAULT_ADDR` / `VAULT_TOKEN` come from env (these
are not secrets — they are the **bootstrap credentials for Vault
itself**). Missing `VAULT_TOKEN` triggers the same critical-and-exit
path as a fetch failure.

Logging discipline: the worker NEVER logs the resolved credentials.
The Vault address is logged on failure (host:port is useful for
debugging, the token is not).

---

## What got removed from the worker

```diff
- password=os.environ.get("SFTP_PASSWORD",  "password123")
- secret_key=os.environ.get("MINIO_SECRET_KEY", "password123")
+ password=creds["password"]
+ secret_key=creds["secret_key"]
```

After the change:

```
$ grep -rni 'password123' app/
(none)
```

All remaining `password` hits inside `app/` are identifier names
(parameter / attribute / dict-key) passing credentials through from
the Vault-reading caller, which the brief explicitly allows.

---

## CI pipeline

Two jobs run in parallel on `ubuntu-latest` / Python 3.11:

### `lint`

```
checkout → setup-python(3.11) → install uv
  → uv pip install --system .          # application deps only
  → uv pip install --system ruff       # ruff isn't in main deps
  → ruff check app/
```

### `test`

```
checkout → setup-python(3.11) → install uv
  → uv pip install --system .
  → uv pip install --system pytest pytest-asyncio
  → docker compose up -d redis minio sftp
  → wait until services report healthy (or timeout after 60s)
  → pytest tests/
  → docker compose down -v             # cleanup (always)
```

Notes:

- The test job spins up `redis`, `minio`, `sftp` because
  `tests/integration/test_infra_adapters.py` exercises real
  containers. The `worker-ingest` container is deliberately NOT
  started — it now requires Vault, which CI does not provision.
- `pytest tests/` discovers `tests/unit` and `tests/integration` per
  `[tool.pytest.ini_options].testpaths` in pyproject.
- `tests/smoke/` and ML golden-set tests are still empty placeholder
  directories — they'll be wired in a follow-up CI revision when
  Person A's ML pieces land.

---

## Verification

- `pytest tests/integration/test_infra_adapters.py -v` against the
  live local stack: **4 passed in 5.52s** (no regression from the
  Vault refactor; tests don't depend on Vault).
- `grep -rni 'password123' app/` — empty result.
- Worker module imports cleanly, `fetch_vault_secrets` exists,
  `build_sftp`/`build_blob` take a `creds: dict` parameter.

---

## Cross-team touchpoints

| Touchpoint | Affects | Action |
|---|---|---|
| Vault KV layout (`secret/data/sftp`, `secret/data/minio`) | Person D | Seed these on Vault dev-mode startup (in compose), shape `{"username","password"}` and `{"access_key","secret_key"}`. |
| `docker-compose.yml` — `worker-ingest` env | Anyone bringing up the stack locally | Remove `SFTP_PASSWORD` / `MINIO_SECRET_KEY` env entries; add `VAULT_ADDR`, `VAULT_TOKEN`, and `depends_on: vault`. |
| `docker-compose.yml` — `vault` service | Person D / anyone bringing up the stack | Add `hashicorp/vault` in dev mode with the seed step that writes the two KV entries above. |
| CI service set | Whoever wires the smoke job | Will need a `vault` service container too, with seeded secrets, before the worker can be tested end-to-end in CI. |

---

## Immediate follow-up before this PR is merged

The worker container in the current compose stack will refuse to boot
(missing `VAULT_TOKEN`). Before this branch hits master, somebody
needs to update `docker-compose.yml` to:

1. Add a `vault` service (hashicorp/vault dev mode) with a one-shot
   seed step that writes both KV entries.
2. Update `worker-ingest` env to pass `VAULT_TOKEN` and remove the
   now-stale `SFTP_PASSWORD` / `MINIO_SECRET_KEY` entries.
3. Add `vault: { condition: service_healthy }` to `worker-ingest`'s
   `depends_on`.

That compose change is intentionally out of scope of this commit — it
belongs in the same PR that ships the dev-mode Vault container, which
is Person D's territory.

---

## Known limitations

**CI does not exercise the Vault → worker credential flow.** Integration
tests build adapter clients (`MinioBlobClient`, `SFTPClient`) directly
from job-level env vars (`MINIO_ROOT_USER`, `SFTP_PASSWORD`, etc.)
rather than going through the worker's `fetch_vault_secrets()` →
`build_*(creds)` path. Consequently, the following breakages would
pass CI but fail on `docker compose up`:

- Shape change in `secret/sftp` or `secret/minio` keys (e.g.
  `username` → `user`).
- Wrong KV mount path in `vault-init`.
- Misconfigured `VAULT_ADDR` on the worker service.
- Dropped `VAULT_TOKEN` env var on the worker service.

These are caught only by manual `docker compose up` smoke tests today.

**Closing the gap** requires (a) adding `vault` + `vault-init` to CI's
`docker compose up` line, and (b) a small integration test asserting
the seeded secret shapes match what `fetch_vault_secrets()` expects:

```python
# tests/integration/test_vault_seed_contract.py
def test_vault_seeded_secrets_match_worker_expectations():
    vault = VaultClient(addr=os.environ["VAULT_ADDR"],
                        token=os.environ["VAULT_TOKEN"])
    assert {"username", "password"}    <= vault.get_secret("sftp").keys()
    assert {"access_key", "secret_key"} <= vault.get_secret("minio").keys()
```

Tracked in the API-E2E roadmap; not blocking the current milestone.
