# Collaboration Guide

How the team coordinates, owns work, and ships code.

---

## Team ownership

Each person owns one slice end-to-end. No shared ownership on core paths — if something in your slice breaks, it is yours to fix.

| Person | Name | Owns |
|--------|------|------|
| A | Ali Asfahani | Classifier training (Colab); model card; golden set; `app/classifier/`; CI golden-replay test |
| B | Sarah Shawraba | API surface (`app/api/`); services & repositories; PostgreSQL schema + Alembic; Redis cache layer |
| C | Mahdi El-Zein | Infra adapters (`app/infra/`); compose infra services (Redis, MinIO, SFTP); SFTP ingestion worker |
| D | Racha Chamseddine | Auth/RBAC (fastapi-users + Casbin); audit log; inference worker; structured logging; React frontend |

---

## Layer rules

Strict layering — the architecture is the grade. Violations are caught in code review.

| Layer | Path | May import from | Must NOT |
|-------|------|-----------------|----------|
| API | `app/api/` | services, domain | touch SQLAlchemy, cache, or infra directly |
| Services | `app/services/` | repositories, domain, infra | raise HTTP errors |
| Repositories | `app/repositories/` | `app/db/models.py`, domain | raise HTTP errors; invalidate caches |
| Domain | `app/domain/` | (nothing internal) | depend on SQLAlchemy |
| Infra | `app/infra/` | external SDKs only | contain business logic; touch DB |
| DB models | `app/db/models.py` | — | be imported anywhere except repositories |

Cache invalidation lives in the **service layer** only.

---

## Git workflow

We use feature branches off `master`. `master` is always in a deployable state.

```sh
git pull origin master                          # always start clean
git checkout -b feature/<initial>-<slug>        # branch
# ... commit small, descriptive units ...
git push -u origin feature/<initial>-<slug>     # push for PR review
# open PR on GitHub → at least one teammate reviews and approves
# merge into master via squash-merge (keeps master linear)
```

**Branch naming:**

```
feature/<initial>-<short-slug>

Examples:
  feature/a-golden-set
  feature/b-batch-endpoints
  feature/c-infra-adapters
  feature/d-auth-rbac
```

---

## Commit message style

Loosely Conventional Commits:

| Prefix | Use for |
|--------|---------|
| `feat:` | New feature |
| `fix:` | Bug fix |
| `refactor:` | Non-functional change |
| `docs:` | README / docs / report only |
| `chore:` | Tooling, deps, infra |
| `test:` | Tests only |

One scope-creep rule: a feature branch fixes its feature **and nothing else**. Drive-by formatting or unrelated fixes go in a separate PR.

---

## PR process

1. Open a PR against `master` from your feature branch.
2. At least one teammate reviews and approves before merge.
3. Squash-merge (not regular merge) to keep `master` linear.
4. Delete the branch after merge.

**Every PR must include a report** — see below.

---

## PR reports

For every feature branch, write `reports/<feature-slug>.md` (slug matches the branch name). The report captures decisions and trade-offs that the diff alone does not show.

Template:

```markdown
# <feature-slug>

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
What was punted and why; what the next PR should pick up.

## How to verify locally
The exact commands you ran to satisfy yourself this works.
```

Reports are reviewed as part of the PR. They are not generated from the diff.

---

## Cross-team contracts

These are the interfaces between slices. Breaking one requires coordinating with the affected teammate before merging.

### Classifier → Inference worker (Person A ↔ Person D)

- Worker calls `app.classifier.inference` — it must accept an image path, bytes, or PIL Image and return top-k predictions.
- Artifact location: `app/classifier/models/classifier.pt` + `model_card.json`.
- SHA-256 in `model_card.json` must match the committed `.pt` file.
- Class names in `model_card.json` must match `DocumentLabel` enum in `app/domain/prediction.py`.

### Infra adapters → Workers (Person C ↔ Person D)

- `RQClient` in `app/infra/queue.py` is the only way workers enqueue jobs.
- `MinioBlobClient` in `app/infra/blob.py` is the only way workers read/write blobs.
- Worker entrypoints must not import boto3 / minio SDK directly.

### Services → API (Person B ↔ Person D)

- Services raise domain-typed exceptions (`BatchNotFoundError`, `RoleToggleForbiddenError`, etc.) — never `HTTPException`.
- Routers catch domain exceptions and map them to HTTP status codes.
- Cache invalidation is the service layer's responsibility — routers do not call `CacheService` directly.

---

## Code review checklist

When reviewing a PR, check:

**Error handling**
- [ ] `except` clauses are narrow (not bare `Exception`) unless at a polling-tick boundary with `# noqa: BLE001`
- [ ] `logger.exception(...)` used inside `except` blocks (not `logger.error`)
- [ ] Re-raises use `raise ... from exc` to preserve the chain
- [ ] New exception types are in the correct layer (`app/infra/exceptions.py` for infra, domain module for services)

**Layering**
- [ ] No layer imports from a layer below it (routers do not touch SQLAlchemy; repos do not raise `HTTPException`)
- [ ] Cache invalidation only in services

**Comments**
- [ ] No tutorial comments explaining Python idioms
- [ ] No comments restating what the code does (`# increment counter`)
- [ ] No PR/issue references inside source code (belongs in the report)
- [ ] Inline comments only for genuinely non-obvious logic (SDK quirks, cross-team invariants)

---

## Submission

Tag at submission: `v0.1.0-week6`

```sh
git tag v0.1.0-week6
git push origin v0.1.0-week6
```
