# Runbook

Operational reference for bringing the stack up, running tests, and recovering from common failures.

---

## Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Docker + Compose | 24+ | All services |
| Git LFS | any | Pull `classifier.pt` (~111 MB) |
| `uv` | 0.4+ | Python deps for local test runs |

---

## First-time setup

```sh
git clone <repo-url>
cd document-classifier-authenticated
git lfs pull                  # pulls classifier.pt (~111 MB)
cp .env.example .env          # fill in VAULT_TOKEN, POSTGRES_*, MINIO_*, SFTP_*
docker compose up --build     # builds images + runs migrate + vault-init
```

`vault-init` and `migrate` are one-shot services that run and exit before `api` starts. On subsequent runs, `docker compose up` is enough (no `--build` unless code changed).

---

## Daily dev workflow

```sh
# Full stack
docker compose up

# Infra services only (no api/workers — useful when running app locally)
docker compose up -d db redis minio sftp vault
docker compose up vault-init        # one-shot: seeds secret/jwt|sftp|minio

# Rebuild a single service after a code change
docker compose up --build api
docker compose up --build worker-inference
```

---

## Dropping a test document

```sh
sftp -P 2222 scanner@127.0.0.1     # password: value of SFTP_PASSWORD in .env
sftp> put my-scan.tif upload/
sftp> exit
```

The ingestion worker (`worker-ingest`) polls every few seconds, uploads the TIFF to MinIO, creates a `Batch` record in Postgres, and enqueues an inference job. The inference worker picks it up and writes a `Prediction` row. The batch appears in the frontend within ~10–15 s on a cold worker (model load + CPU forward pass).

---

## Running tests

### Unit tests (mocked, < 1 s)

```sh
uv run pytest tests/unit/ -v
```

### Integration tests (real Postgres + Redis + MinIO)

Requires infra services to be running:

```sh
docker compose up -d db redis minio sftp vault
docker compose up vault-init
DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5433/app' \
VAULT_TOKEN='dev-root-token-change-in-prod' \
uv run alembic upgrade head

uv run pytest tests/integration/ -v
```

### Golden-set regression (real ConvNeXt weights, CPU)

Requires `[ml]` extras:

```sh
uv pip install ".[ml]"
uv run python -m app.classifier.eval.golden
```

Expected output: `[golden] 50/50 passed`. Each image's label must match exactly and top-1 confidence must be within `1e-6`.

### Full-stack smoke

```sh
# Stack must be up
docker compose up -d
sleep 15   # wait for startup checks to pass

uv run python tests/smoke/test_full_stack.py
```

The smoke test registers a user, drops a TIFF via SFTP, polls `/batches` until the batch reaches `done`, and asserts the prediction row is present. Expected run time: ~12 s.

---

## Viewing logs

```sh
# All services
docker compose logs -f

# Single service
docker compose logs -f api
docker compose logs -f worker-inference
docker compose logs -f worker-ingest

# Last 50 lines
docker compose logs --tail 50 api
```

Structured logs (JSON) are written by `structlog`. Filter by event:

```sh
docker compose logs api | grep '"event"'
```

---

## Port reference

| Service | Host address | Notes |
|---------|-------------|-------|
| API | `127.0.0.1:8000` | Swagger UI at `/docs` |
| Frontend | `127.0.0.1:3000` | React app (nginx) |
| PostgreSQL | `127.0.0.1:5433` | `psql -h localhost -p 5433 -U $POSTGRES_USER` |
| Vault | `127.0.0.1:8200` | `vault status -address=http://localhost:8200` |
| MinIO S3 | `127.0.0.1:9000` | S3-compatible API |
| MinIO console | `127.0.0.1:9001` | Browser UI |
| Redis | `127.0.0.1:6379` | `redis-cli -p 6379 ping` |
| SFTP | `127.0.0.1:2222` | `sftp -P 2222 scanner@127.0.0.1` |

---

## Common failures

### API exits immediately at startup

The API enforces a refuse-to-start contract. Check which guard triggered:

```sh
docker compose logs api --tail 30
```

| Log event | Cause | Fix |
|-----------|-------|-----|
| `vault_unreachable_or_jwt_missing` | Vault is down or `secret/jwt` not seeded | `docker compose up vault-init` |
| `classifier_weights_missing` | `classifier.pt` not present | `git lfs pull` |
| `classifier_sha256_mismatch` | Weights file corrupted or swapped | `git lfs pull --force` |
| `casbin_policy_table_empty` | `migrate` has not run | `docker compose up migrate` |
| `redis_unreachable` | Redis not healthy | `docker compose up -d redis` |

### Kill-Vault walkthrough (demonstrates refuse-to-restart)

```sh
docker compose up -d          # stack running normally
docker compose stop vault     # kill Vault
docker compose restart api    # api exits immediately
docker compose logs api --tail 20
# Expected: CRITICAL "refuse_to_boot" reason="vault_unreachable_or_jwt_missing"

docker compose start vault    # restore
docker compose restart api    # api boots normally
```

### `vault-init` fails

Vault must be healthy before `vault-init` runs. If it fails:

```sh
docker compose up vault                # wait for healthy
docker compose run --rm vault-init     # rerun manually
```

### Alembic migration fails

```sh
docker compose logs migrate --tail 30
# Check for schema conflicts or unreachable DB

# Rerun manually
docker compose run --rm migrate
```

### Ingestion worker not picking up files

1. Check that files were dropped into `upload/` (not the SFTP root):
   ```sh
   sftp> ls upload/
   ```
2. Check worker logs:
   ```sh
   docker compose logs worker-ingest -f
   ```
3. Verify Redis is healthy: `redis-cli -p 6379 ping`

### Inference worker stalls / jobs not processing

```sh
docker compose logs worker-inference -f
```

Common causes:
- `classifier.pt` missing or SHA mismatch (worker also refuses to start)
- MinIO unreachable — check `docker compose logs minio`
- Redis queue full — `redis-cli -p 6379 llen rq:queue:default`

### Database connection refused

PostgreSQL binds on host port `5433` (not `5432`) to avoid conflicts. Always use port `5433` in local `DATABASE_URL`:

```
postgresql+asyncpg://user:pass@localhost:5433/app
```

---

## Resetting state

```sh
# Wipe all data volumes and start fresh
docker compose down -v
docker compose up --build
```

This destroys Postgres, MinIO, Redis, and SFTP volumes. `vault-init` and `migrate` re-run automatically.
