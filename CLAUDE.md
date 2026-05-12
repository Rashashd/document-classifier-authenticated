# CLAUDE.md — Working Notes for AI Pair-Programming

This file orients an AI assistant (Claude Code) to the repo. It is **not**
the primary architecture doc — that is `ARCH.md`. Read this first.

---

## 1. Project at a glance

**Document Classifier as an Authenticated Service** — Week 6 AIE bootcamp
project, group of 4. A docker-compose stack that:

1. Receives TIFF scans over SFTP (atmoz/sftp).
2. Uploads them to MinIO.
3. Enqueues an inference job via Redis Queue (RQ).
4. Runs a ConvNeXt classifier (16-class RVL-CDIP taxonomy).
5. Writes predictions + annotated PNG overlays back to MinIO.
6. Exposes results through a FastAPI surface gated by Casbin RBAC
   (admin / reviewer / auditor) with JWT auth via fastapi-users.

Deadline: **Thursday midnight**, presentation Friday. Tag at submission:
`v0.1.0-week6`.

The model is trained on Colab. **Only** the weights, the model card, and
the 50-image golden set ship in the repo. The local stack never trains.

---

## 2. Layered architecture (the grade)

> **The architecture is the grade.** A working classifier in a tangled
> codebase scores below a slightly-worse classifier in a clean one.

Strict layering, no shortcuts:

| Layer | Path | May import from | Must NOT |
|---|---|---|---|
| API | `app/api/` | services, domain | touch SQLAlchemy, cache, infra directly |
| Services | `app/services/` | repositories, domain, infra | raise HTTP errors |
| Repositories | `app/repositories/` | `app/db/models.py`, domain | raise HTTP errors, invalidate caches |
| Domain | `app/domain/` | (nothing internal) | depend on SQLAlchemy |
| Infra | `app/infra/` | external SDKs only | contain business logic, touch DB |
| DB models | `app/db/models.py` | — | be imported anywhere except repositories |

Cache invalidation lives in the **service layer** only. Routers and
repositories do not invalidate.

Friday spot-check: examiners will ask each of us to add a new endpoint
or CLI command **live**, walking through router → service → repo → DB.

---

## 3. Where things live

```
app/
├── api/            # FastAPI routers — HTTP only
│   ├── main.py
│   └── routers/auth.py
├── classifier/     # ML artefacts (Person A)
│   ├── models/classifier.pt        (git LFS)
│   ├── models/model_card.json      (SHA-256, metrics)
│   └── eval/golden_images/         (50 TIFFs + golden_expected.json)
├── core/           # settings, config, structured logging
├── db/             # SQLAlchemy ORM + Alembic migrations
├── domain/         # pydantic models — already populated
│   ├── audit.py    batch.py  jobs.py  prediction.py  user.py
├── infra/          # external-system adapters (Person C — this PR)
│   ├── blob.py     MinioBlobClient
│   ├── queue.py    RQClient
│   └── sftp.py     SFTPClient
├── repositories/   # SQL only
├── services/       # business logic, txn boundaries, cache invalidation
│   └── audit_service.py
└── workers/        # RQ entrypoints (sftp_ingest, inference)
```

Frontend lives in `frontend/` (out of Python scope).

---

## 4. Compose stack

| Service | Image | Owner |
|---|---|---|
| `api`         | (our Dockerfile) | Person B |
| `worker`      | (our Dockerfile) | Person D |
| `sftp-ingest` | (our Dockerfile) | Person D |
| `migrate`     | (our Dockerfile, alembic) | Person B |
| `db`          | `postgres:16` | Person B |
| `redis`       | `redis:7` ✅ | Person C |
| `minio`       | `minio/minio` ✅ | Person C |
| `sftp`        | `atmoz/sftp` ✅ | Person C |
| `vault`       | `hashicorp/vault` dev | TBD |

✅ = already wired in `docker-compose.yml` by Person C.

Host port map (dev only): 2222 → sftp:22, 9000 → minio:9000,
9001 → minio:9001 (console), 6379 → redis:6379. All bound to 127.0.0.1.

---

## 5. Cross-cutting rules to never forget

- **Secrets:** `grep -ri 'password' app/` must return zero hits outside
  Vault-reading code. The `.env` file holds only the Vault root token
  and ports. Compose-level credentials are NOT read by `app/`.
- **Refuse-to-start contract:** `api` and `worker` will not boot if
  classifier weights are missing, SHA-256 mismatches the model card, or
  the model card's reported test top-1 is below the README threshold.
  `api` also refuses to boot if Vault is unreachable or the Casbin
  policy table is empty.
- **Latency budgets (in README, demoed Friday):**
  - cached read p95 < 50 ms
  - uncached read p95 < 200 ms
  - inference p95 < 1.0 s (CPU, ConvNeXt Tiny/Small)
  - SFTP → `GET /batches/{bid}` p95 < 10 s (single doc)
- **No OCR.** We classify visual layout, not text.
- **Queue choice:** RQ, not Celery (explicit brief constraint).

---

## 6. Team layout (Trello mirrors this)

| Person | Owns |
|---|---|
| A | Classifier training on Colab; model card; golden set; CI golden-replay test |
| B | API surface; services + repositories; Postgres + Alembic; cache layer |
| C (me) | Infra adapters (blob/queue/sftp); compose infra services; SFTP ingestion plumbing |
| D | Inference worker; sftp-ingest worker loop; audit log writes; structured logging |

Each person owns one component end-to-end. No passengers.

---

## 7. Git workflow

We use feature branches off `master`. Naming convention:

```
feature/<initial>-<short-slug>
e.g. feature/c-infra-adapters
```

Workflow for any non-trivial change:

```sh
git pull origin master                       # always start clean
git checkout -b feature/<initial>-<slug>     # branch
# ... commit small, descriptive units ...
git push -u origin feature/<initial>-<slug>  # push for PR review
# open PR on GitHub; at least one teammate reviews and approves
# merge into master via squash-merge so master stays linear
```

Commit-message style (loosely Conventional Commits):

- `feat: …` — new feature
- `fix: …` — bug fix
- `refactor: …` — non-functional change
- `docs: …` — README / CLAUDE.md / report-only change
- `chore: …` — tooling, deps, infra

One scope-creep rule: a feature branch fixes its feature **and nothing
else**. Drive-by formatting changes are a separate PR.

---

## 8. PR reports — required for every feature branch

For **every PR / feature branch**, write a Markdown report in
`reports/<feature-slug>.md` (matching the branch slug). The report
captures *decisions and trade-offs that the diff alone won't show*.

**Template** (keep each section short, ~3-6 lines):

```markdown
# <feature slug>

**Branch:** feature/<initial>-<slug>
**Author:** <name>
**Date:** YYYY-MM-DD

## Scope
Bullet list of what this PR ships and what it deliberately does not ship.

## Key decisions
- Decision → reasoning → alternative considered.

## Cross-team touchpoints
Anything that depends on / will be depended on by another teammate.

## Open questions / follow-ups
What you punted on and why; what the next PR should pick up.

## How to verify locally
The exact commands you ran to satisfy yourself this works.
```

Reports are reviewed as part of the PR. They are not generated from the
diff — write them yourself.

---

## 9. Quick commands

```sh
# Bring up Person C's infra services only:
docker compose up -d redis minio sftp

# Inspect MinIO via the console:
open http://127.0.0.1:9001     # admin / password123

# Drop a test TIFF on the SFTP share:
sftp -P 2222 scanner@127.0.0.1  # password123
> put scan.tif upload/

# Install Python deps (once pyproject.toml is filled by Person A):
uv sync
```

---

## 10. When in doubt

Re-read the architecture table in §2. Most bugs in this kind of layered
codebase are layer violations dressed as functional bugs.
