# env-and-compose

**Branch:** `feature/mahdi-worker-inference`
**Author:** Person C
**Date:** 2026-05-14

---

## The convention

| Where | What lives there |
|---|---|
| `.env` (host, gitignored) | All `${VAR}` values referenced by `docker-compose.yml`. **Plus** `DATABASE_URL` for running app code on the host (alembic, pytest, ad-hoc scripts). |
| `.env.example` (tracked) | Same shape as `.env`, with placeholder values; new devs `cp .env.example .env` and they're up. |
| Container-side env (compose `environment:` blocks) | Per-service config the *container* reads. The api / migrate / worker-ingest blocks compute `DATABASE_URL` from `${POSTGRES_*}` internally, override `VAULT_ADDR` to `http://vault:8200`, etc. |
| Vault (`secret/jwt`, `secret/sftp`, `secret/minio`) | The actual sensitive values consumed by application code. Seeded by `vault-init` from the same `.env` values. |

**One source of truth is `.env`.** Everything else is derived from it.

## Complete variable list

Required for `docker compose up`:

```
POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
MINIO_ROOT_USER, MINIO_ROOT_PASSWORD
SFTP_USER, SFTP_PASSWORD
VAULT_TOKEN
```

Required for running app code on the host (alembic / pytest):

```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/app
VAULT_TOKEN=<same as above>
```

Optional (defaults work everywhere):

```
VAULT_ADDR             # default http://vault:8200 (compose) / set host-side if outside compose
REDIS_URL              # default redis://redis:6379/0
VAULT_JWT_SECRET_PATH  # default secret/data/jwt
VAULT_SFTP_PATH        # default sftp
VAULT_MINIO_PATH       # default minio
JWT_ALGORITHM          # default HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES  # default 60
```

All of the above are in [.env.example](../.env.example) with comments
explaining when each is needed.

## Host port map (important sharp edge)

Compose maps Postgres **5433 → 5432** to avoid clashing with a
developer-local Postgres on 5432. The other services keep their
natural ports.

| Service | Host port | In-network host:port |
|---|---|---|
| `db` (Postgres) | 5433 | `db:5432` |
| `redis` | 6379 | `redis:6379` |
| `minio` (API) | 9000 | `minio:9000` |
| `minio` (Console) | 9001 | `minio:9001` |
| `sftp` | 2222 | `sftp:22` |
| `vault` | 8200 | `vault:8200` |
| `api` | 8000 | `api:8000` |

If you're running app code on the host, use `localhost:<host-port>`.
If you're running it inside compose, use `<service-name>:<internal-port>`.

## Local conflicts to watch for

Several developers on this team also run other docker-compose projects
locally that bind the same host ports. Conflicts I've personally hit:

| Port | Held by | Resolution |
|---|---|---|
| 6379 | `drift-triage-agent-redis-1` | `docker stop drift-triage-agent-redis-1` |
| 9000/9001 | `wrc_minio` | `docker stop wrc_minio` |
| 5432 | `drift-triage-agent-postgres-1` | (irrelevant — we now use 5433) |
| 8000 | `drift-triage-agent-agent-1` | `docker stop drift-triage-agent-agent-1` |

If `docker compose up` errors with `address already in use`, the
above is almost certainly why.

## Smoke-check the stack is working

```sh
cp .env.example .env                    # one-time, edit values as needed
docker compose up -d                    # brings up all 7 services
docker compose ps                       # all should report "healthy"
docker compose logs worker-ingest | head -10
# expected: ingest.boot → SSH auth successful → sftp connected
docker compose exec vault vault kv list \
    -address=http://127.0.0.1:8200 secret/
# expected keys: jwt, minio, sftp
```

If `worker-ingest` is in a restart loop or `api` is not "running",
check that:

1. `.env` exists at repo root (not under `app/` or `document-classifier-authenticated/` parent — it must be alongside `docker-compose.yml`).
2. Vault's three secrets are seeded — see [vault-secrets-seeding.md](vault-secrets-seeding.md).
3. No other container holds the ports above.

## What lives in compose vs Vault

| Layer | Reads from |
|---|---|
| Container engines (postgres image, minio image, sftp image) need bootstrap credentials *to start the daemon*. | compose env (`${POSTGRES_USER}` etc.) → image's own init script |
| Application code (`app/`) needs credentials *to talk to those daemons as a client*. | **Vault**, via `app/infra/vault.py` |

The two are kept in sync by `vault-init` at compose-startup. That
sync only goes one direction: `.env` → Vault.

## Follow-ups

1. **Add a `.env` validation step** to the README (or a `make check`
   target) so new devs know what's missing before `docker compose up`
   produces a cryptic error.
2. **CI .env duplication.** [.github/workflows/ci.yml](../.github/workflows/ci.yml)
   currently writes its own `.env` inline with hardcoded test values.
   That's fine because CI is its own concern, but the values drift if
   someone updates `.env.example` and forgets the CI step. A more
   robust approach would be to commit a `.env.ci` and have both CI
   and the repo's `.env.example` derive from a shared template; for
   now, manual sync is sufficient.
3. **Per-developer overrides.** Anyone wanting different values
   should add them to `.env` directly (it's gitignored) rather than
   editing `.env.example`. Mention this in the README onboarding.
