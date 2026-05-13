# vault-secrets-seeding

**Branch:** `feature/mahdi-worker-inference`
**Author:** Person C
**Date:** 2026-05-14

---

## The gap that was blocking the stack

Before this PR, `dc_vault_init` only seeded one secret (`secret/jwt`).
The ingestion worker reads two others at boot —

```python
sftp_creds  = vault.get_secret("sftp")     # → username, password
minio_creds = vault.get_secret("minio")    # → access_key, secret_key
```

— and refuses to start when either path is missing. Result: worker
crashed in a restart loop with:

```json
{"event":"vault.boot.fetch_failed","sftp_path":"sftp","minio_path":"minio",
 "error":"Vault secret read failed for path 'sftp': None, on get
 http://vault:8200/v1/secret/data/sftp", "level":"critical"}
```

This is the **refuse-to-start** behaviour we designed in for security
(no Vault → no boot, ever). It was working as intended; the seeding
just hadn't caught up.

## What changed

[docker-compose.yml](../docker-compose.yml) — `vault-init` now seeds
all three secrets in one one-shot run:

```yaml
entrypoint: >
  sh -c "
  vault kv put -address=http://vault:8200 secret/jwt   secret=dev-signing-key-change-in-prod &&
  vault kv put -address=http://vault:8200 secret/sftp  username=$$SFTP_USER       password=$$SFTP_PASSWORD &&
  vault kv put -address=http://vault:8200 secret/minio access_key=$$MINIO_ROOT_USER secret_key=$$MINIO_ROOT_PASSWORD
  "
environment:
  VAULT_TOKEN:         ${VAULT_TOKEN}
  SFTP_USER:           ${SFTP_USER}
  SFTP_PASSWORD:       ${SFTP_PASSWORD}
  MINIO_ROOT_USER:     ${MINIO_ROOT_USER}
  MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
```

The `$$` is intentional — single-`$` would be expanded by *compose*
at parse time; double-`$$` defers expansion to the shell **inside**
the container, which is where the env vars live.

## Single source of truth

`.env` on the host is the only place these values live in plaintext.
`vault-init` mirrors them into Vault at startup. The app NEVER reads
`SFTP_PASSWORD` / `MINIO_ROOT_PASSWORD` from env — it goes through
Vault. The brief's grep test (`grep -ri 'password' app/` returns
nothing outside Vault-reading code) is preserved.

## What an analyst will see when the worker boots

After this fix, fresh `docker compose up -d`:

```json
{"event":"ingest.boot", ...}
{"event":"Connected (version 2.0, client OpenSSH_8.4p1)", ...}
{"event":"Authentication (password) successful!", ...}
{"event":"[chan 0] Opened sftp connection (server version 3)", ...}
{"event":"sftp: connected to sftp:22 as 'scanner'", ...}
```

No more `vault.boot.fetch_failed`.

## What this is NOT (and the prod path)

This is the **dev / CI bootstrap**. It hardcodes the convention that
compose's env vars and Vault's secret values are the same plaintext
strings. That's fine for a laptop and CI; it's wrong for production:

1. **Auth mechanism.** Worker still authenticates to Vault with a
   root token. Production should use AppRole, Kubernetes auth, or
   one of Vault's other auth methods, with role-bound, short-lived
   tokens — not a root token.
2. **Where secret values originate.** In prod the SFTP/MinIO secrets
   are written into Vault by ops (manually, via Terraform, or via a
   sealed-secrets pipeline). They never live on a host as plaintext.
3. **Vault storage.** Dev mode runs `vault server -dev` — in-memory,
   wipes on container restart, single unsealed node. Prod runs real
   storage (Consul / integrated raft / file) with sealing.

The Vault adapter (`app/infra/vault.py`) is **production-shaped
already**. The only thing that changes for prod is which auth
mechanism builds the `hvac.Client` token (and where `vault-init`
goes — it's replaced by the real provisioning process).

## Verification (from this PR's commit)

```sh
docker compose down -v
docker compose up -d
docker compose ps                                # all healthy
docker compose exec vault vault kv list \
    -address=http://127.0.0.1:8200 secret/
# Keys:  jwt, minio, sftp
docker compose logs worker-ingest | grep ingest.boot
# {"event":"ingest.boot", ...}   (no crash, no restart loop)
```

## Follow-ups before this hits prod

1. Replace root-token auth with AppRole. The worker's existing
   `VaultClient(addr, token)` constructor signature works for both —
   the token just becomes role-bound and shorter-lived.
2. Move `vault-init` out of compose. It belongs in the deployment
   pipeline, not the dev stack.
3. Add a `vault_secret_rotated` hook: when ops rotates an SFTP
   password in Vault, the worker should re-fetch on next poll
   (currently it caches at boot). Out of scope today; raise as a
   ticket when production timeline approaches.
