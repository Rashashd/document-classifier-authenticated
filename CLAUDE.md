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

## 10. Error-handling conventions

These rules apply to **every layer**, but they are most load-bearing in
`app/infra/` (which talks to external systems) and in `app/services/`
(which decides what to do with failures).

### 10.1 Catch narrow, not broad

`except Exception:` is almost never right. It hides bugs that should
crash loudly (e.g. typos, attribute errors) by treating them like
transient infrastructure failures.

```python
# Bad — swallows AttributeError, ImportError, KeyboardInterrupt-class
# logic errors, and AuthenticationException, all the same way.
try:
    transport.connect(username=u, password=p)
except Exception as exc:
    raise RuntimeError(f"failed: {exc}") from exc

# Good — narrow on the documented failure modes.
try:
    transport.connect(username=u, password=p)
except (paramiko.SSHException, OSError) as exc:
    raise SFTPConnectError(host=h, port=p) from exc
```

The only acceptable use of `except Exception` is at the very edge of
the system (a polling-tick boundary, a single RQ job execution) where
you must keep the long-lived loop alive. In those places, write
`# noqa: BLE001` and a comment explaining the boundary.

### 10.2 Log with `exc_info`, not without

A bare `logger.error("X failed")` drops the traceback. The next person
debugging at 2am has no idea where the error came from. Always log with
the traceback when you catch:

```python
# Bad
logger.error("minio: upload connection failed")
raise RuntimeError(...) from exc

# Good — logger.exception() implies exc_info=True at ERROR level
logger.exception("minio: upload connection failed (bucket=%r key=%r)", b, k)
raise BlobUnavailableError(...) from exc
```

Use `logger.exception(...)` inside `except` blocks. Reserve plain
`logger.error(...)` for cases where you have no exception object on
hand (rare).

### 10.3 Preserve the chain — always use `raise ... from exc`

Without `from exc`, Python loses the causal chain. Python 3 still
prints both via `__context__`, but it labels the original as "During
handling of the above exception, another exception occurred" — which
implies a bug. With `from exc` we get "The above exception was the
direct cause of the following exception", which is what we mean.

### 10.4 Typed exceptions across layer boundaries

Each layer translates external failures into its own vocabulary. This
keeps higher layers from importing low-level SDK exceptions and keeps
the architecture's directionality (§2) intact.

| Layer | Catches | Raises |
|---|---|---|
| `app/infra/` | SDK exceptions (`S3Error`, `paramiko.SSHException`, `redis.exceptions.ConnectionError`, `urllib3.exceptions.MaxRetryError`, `OSError`) | Infra-typed exceptions, e.g. `BlobUnavailableError`, `BlobNotFoundError`, `QueueUnavailableError`, `SFTPConnectError`, `SFTPReadError` |
| `app/services/` | Infra-typed exceptions + repository exceptions | Domain-typed exceptions, e.g. `BatchNotFoundError`, `RoleToggleForbiddenError`, `ClassifierUnavailableError` |
| `app/api/` | Domain-typed exceptions | `fastapi.HTTPException` with appropriate status codes |
| `app/repositories/` | SQLAlchemy exceptions | Repository exceptions; **never** HTTPException |

Define infra-typed exceptions in `app/infra/exceptions.py` (one module,
keep them tiny — usually just `class FooError(Exception): pass`). A
single shared module avoids each adapter inventing its own hierarchy.

Wrapping every failure as `RuntimeError` is an anti-pattern in this
codebase: it forces every caller to either ignore the failure type or
parse log strings, neither of which composes with a retry/backoff
policy.

### 10.5 Decide where retries live, then put them there

Retries are policy, not transport — so they do not belong in
`app/infra/`. The adapter surfaces failures faithfully; **someone
above** decides whether to retry, dead-letter, or surface to the user.

- **Network-level transient failures during inference** → RQ's job
  retry config (`retry=Retry(max=3, interval=[5, 30, 120])`). The
  worker entrypoint catches its own typed errors and re-raises to let
  RQ handle the backoff.
- **API-side transient failures** (e.g. cache backend hiccup) → return
  the uncached path (degrade gracefully). The service layer decides
  this, not the router.
- **Permanent failures** (auth, schema mismatch, missing resource) →
  never retry. Surface to the user with a stable error code.

If you find yourself sleeping-and-retrying inside an infra adapter,
move it out.

### 10.6 Refuse-to-start over self-heal

For startup probes (`MinioBlobClient.startup()`, `init_redis_cache()`,
classifier weight verification), failure must propagate out and crash
the container. A container in a crash-loop is observable; one that
silently degraded is not.

Specifically:
- `api` and `worker` must refuse to boot if classifier weights are
  missing, SHA-256 mismatches the model card, or model-card top-1 is
  below the README threshold.
- `api` must refuse to boot if Vault is unreachable or the Casbin
  policy table is empty.
- `api` and `worker` should refuse to boot if their backing Redis is
  unreachable at startup (we cache-ping and queue-ping during the
  startup event).

### 10.7 Idempotency over exactly-once

We chose at-most-once delivery in `SFTPClient.list_and_download_new_files`
(delete-after-read). The cross-component contract is that the
inference worker MUST be idempotent on `(batch_id, filename)`. If you
add a new pipeline stage, ask yourself: "if this runs twice, what
breaks?" and either make it idempotent or document why retries are
forbidden.

### 10.8 Don't catch what you can't handle

If you can't make a decision based on the exception, don't catch it.
Re-raising with a wrapped message just for the sake of "handling" is
worse than letting the original propagate — it costs lines, hides the
type, and adds nothing the caller can act on.

### 10.9 Quick checklist when reviewing an `except`

When you see a `try/except` in a PR review, ask:

1. Is the caught type **narrow** (specific exception classes, not
   `Exception`)?
2. Does the log call use `logger.exception(...)` (or `exc_info=True`)?
3. Is the re-raise chained with `from exc`?
4. Does the new exception type live in the right layer (infra raises
   infra-typed, services raise domain-typed, etc.)?
5. Is there a comment explaining **why** this failure is caught here
   and not propagated?

If any answer is "no" without a documented reason, request changes.

---

## 11. Code Hygiene & Commenting Standards

Comments in this repo are **high signal, low noise**. The codebase is
small and the architecture doc lives outside the source files, so
prose belongs in `ARCH.md`, `DECISIONS.md`, and `reports/*.md` — not
buried in every adapter. Specifically:

1. **Minimise inline comments.** Default to none. If removing a
   comment wouldn't confuse a future reader, the comment shouldn't
   exist.
2. **One module-level docstring per file.** Short — one short
   paragraph stating the file's purpose. No "Why a class, not a
   function?" essays.
3. **Short, concise docstrings on classes and public functions.**
   Describe what the thing does and its non-obvious contract.
   Skip parameter-by-parameter restatements that the type hints
   already cover.
4. **Inline comments only for genuinely non-obvious logic** —
   SDK quirks, hidden constraints, cross-team invariants, subtle
   ordering requirements. Examples that pass this bar:
   - `atmoz/sftp chroots scanner to /home/scanner, so /upload is
     what we see` (path-quirk surprise)
   - `decode_responses=False — RedisBackend stores pickled bytes`
     (would-look-tidy-to-flip flag)
   - `delete before yielding — downstream must dedupe on filename`
     (cross-team contract)
5. **No tutorial / teaching comments.** Do not explain what `try /
   except` does, what `BytesIO` is, or what Python idioms mean.
   Assume the reader knows Python.
6. **Don't restate the code.** `# increment counter` next to
   `counter += 1` is noise. Either the name is unclear (fix the name)
   or the comment is redundant (delete it).
7. **Don't cite the current PR / task / issue inside the code.**
   "added for the Y flow", "handles the case from issue #123" — that
   belongs in the PR description and the report under `reports/`.

The PR-review checklist in §10.9 applies here too: if a comment
doesn't add information that the code can't, ask the author to delete
it.

---

## 12. When in doubt

Re-read the architecture table in §2. Most bugs in this kind of layered
codebase are layer violations dressed as functional bugs.
