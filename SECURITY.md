# SECURITY.md

## Secrets discipline

No application secret lives in environment variables or source code.
The `.env` file holds only the Vault root token and port bindings — nothing the `app/` package reads directly.

**Rule:** `grep -ri 'password' app/` must return zero hits outside Vault-reading code and the SFTP/MinIO ingest worker (which reads compose-injected env vars, not auth credentials).

Current grep output:

```
app/infra/sftp.py           — parameter name and instance variable, no hardcoded value
app/services/auth_service.py — 'reset_password_token_secret' is a fastapi-users field name, not a credential
app/workers/sftp_ingest.py  — SFTP_PASSWORD and MINIO_SECRET_KEY read from os.environ (compose-injected);
                               fallback 'password123' is dev-only and never touches the auth system
```

No hardcoded secrets. `app/` never reads JWT material from env.

---

## Vault

| Item | Value |
|---|---|
| Server | `http://vault:8200` (compose service, dev mode) |
| Auth | Root token from `.env` → `VAULT_TOKEN` |
| JWT secret path | `secret/data/jwt` (KV v2) |
| Secret key inside payload | `secret` |

Dev mode runs in-memory with no unsealing required. The root token is the only credential; it is injected by compose and never committed.

### Vault paths

| Path | Content | Consumer |
|---|---|---|
| `secret/data/jwt` | `{ "secret": "<signing key>" }` | `lifespan.py` at startup |

---

## JWT lifecycle

1. **Startup** — `lifespan.py` instantiates `VaultClient` and calls `vault.get_secret("jwt")`. The signing key is stored on `app.state.jwt_secret`. If Vault is unreachable or the path is missing, the process calls `sys.exit(1)` — the container never starts in a degraded state.

2. **Login** — `POST /auth/login` triggers fastapi-users `UserManager`, which verifies the bcrypt hash. On success, `JWTStrategy` (algorithm: `HS256`, lifetime: 60 min) mints a token whose `sub` claim is the user's UUID.

3. **Per request** — `get_current_user` in `app/api/deps.py` extracts the Bearer token, calls `JWTStrategy.read_token()` to verify signature and expiry, and returns the `User` object. Casbin then enforces role policy.

4. **Expiry** — tokens are stateless; there is no revocation list. A role change takes effect on the user's next login (next token). Logout simply discards the client-side token.

---

## Role table

| Role | Reviewer routes | Audit log | Admin routes |
|---|---|---|---|
| `reviewer` | Yes | No | No |
| `auditor` | No | Yes | No |
| `admin` | Yes (inherited) | Yes (inherited) | Yes |

Casbin role inheritance (`g` rules in `app/casbin/policy.csv`):
- `admin` inherits `reviewer` and `auditor`
- `auditor` does **not** inherit `reviewer` — read-only by design

Self-demotion is blocked at the route level: `POST /users/admin/{uid}/role` returns `409` if `uid == current_user.id`.

---

## Audit log fields

Table: `audit_entries`

| Column | Type | Description |
|---|---|---|
| `id` | UUID | Row identifier |
| `actor_id` | UUID → `users.id` | User who performed the action; `SET NULL` on user deletion |
| `action` | string (128) | Short verb: `role_toggle`, `relabel`, `batch_status_change` |
| `target` | string (512) | Resource path: `/users/{uid}/role`, `/predictions/{pid}`, `/batches/{bid}` |
| `request_id` | string (128), nullable | `X-Request-ID` from structured logger |
| `timestamp` | timestamptz | Set by DB server default (`now()`) |

---

## Kill-Vault walkthrough

Demonstrates the refuse-to-restart contract:

```sh
# 1. Stack is running normally
docker compose up -d

# 2. Kill Vault
docker compose stop vault

# 3. Force-restart the API container
docker compose restart api

# 4. Observe — api exits immediately
docker compose logs api --tail 20
# Expected: structlog CRITICAL event "refuse_to_boot" with reason "vault_unreachable_or_jwt_missing"
# Container enters a restart loop (restart: unless-stopped) rather than serving traffic

# 5. Restore
docker compose start vault
docker compose restart api
# API boots normally once Vault is reachable again
```

A container in a crash-loop is observable via `docker compose ps` and alertable via healthchecks. A container that silently degraded (e.g. fell back to a weak default secret) would not be.
