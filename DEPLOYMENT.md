# DEPLOYMENT — insurance-text2sql on Azure (global)

> This document specifies **deployment only**. It is subordinate to SPEC.md:
> on any conflict, SPEC.md prevails. Revisions go through PRs, same as SPEC.md.
> All decisions below were settled on 2026-08-21; changing any of them is a
> revision to this file, not a code comment.

## 1. Architecture

```
client (inside VNet) --HTTPS--> Container Apps (FastAPI / uvicorn; lazy graph)
   ingress = internal-only  (/v1/query is unauthenticated by SPEC §7.2)
       |-- VNet --> Azure Database for PostgreSQL 16 Flexible Server
       |              (private access, no public endpoint)
       |              `-- one-shot Container Apps Job runs db/seed.py
       |                  (ADMIN_DATABASE_URL exists only on this Job)
       |-- outbound HTTPS --> Zhipu endpoint (LLM_BASE_URL)
       `-- Key Vault (managed identity: ZHIPUAI_API_KEY, DATABASE_URL)
GitHub Actions: build --> ACR --> az containerapp update (OIDC, deploy job)
```

| Component | Choice | Notes |
|---|---|---|
| Compute | Container Apps, **workload profiles environment, Consumption profile only** | Consumption-only (V1) environments run in a Microsoft-managed network and **cannot join a custom VNet** → excluded. Never add a dedicated profile (~$145/mo baseline). |
| Ingress | **internal-only** | The service has no auth (SPEC §7.2); public ingress would let anyone holding the URL spend the Zhipu quota. All smoke checks run from inside the VNet (`az containerapp exec`). |
| Database | PostgreSQL Flexible Server 16, private access (VNet), B1ms, East Asia | Same DDL/roles as local (`db/*.sql`); both connection strings carry `sslmode=require`. |
| Secrets | Key Vault + managed identity | App reads `ZHIPUAI_API_KEY` / `DATABASE_URL` via Key Vault references; `ADMIN_DATABASE_URL` exists only as an ephemeral secret on the seed Job. |
| Registry / CI | ACR; GitHub Actions deploy job with OIDC | Deploy depends on the test job only (§6). |
| Scaling | min replicas **0**, max **2–3** | The max cap exists because of Zhipu rate limits (429s observed under concurrency). Cold start is accepted: first query = cold start + lazy graph build + first LLM call, ~5–10 s. |
| Egress | IPv4 only | VNet-integrated Container Apps have no IPv6 egress; the Zhipu endpoint lists AAAA first, so `build_llm` reorders `getaddrinfo` to try AF_INET first (SPEC §5.3). Verified in M3: without it the first `/v1/query` hangs past the client timeout. |

## 2. Security mapping (the two layers, SPEC §5.4 + §4.3)

- Layer 1, AST validator: unchanged by deployment.
- Layer 2, runtime role: `t2s_readonly` with role-level `default_transaction_read_only = on` and `statement_timeout = '5s'` (`db/02_roles.sql`) applies verbatim on Flexible Server.
- The application container holds only `DATABASE_URL` (read-only role). The admin connection string never enters the app environment.
- PG16 caveat to verify at first seed (M3): `CREATE ROLE` works for the server admin (a non-superuser); the suspect link is `ALTER ROLE ... SET` / `GRANT`. If it fails, the fix is a small seed-flow change, recorded here.

## 3. Image

- Root `Dockerfile`: `python:3.12-slim` multi-stage + uv; install with **`uv sync --frozen --no-dev`** (editable layout — `llm.py` resolves `prompts/` relative to the source tree via `parents[2]`). **`uv pip install .` (site-packages) is forbidden** — it breaks prompt loading.
- The image contains `src/`, `prompts/`, and `db/` (inert in the app; the seed Job reuses the same image with its command overridden to `python db/seed.py`, so there is exactly one image to build and audit).
- Entrypoint: `uvicorn text2sql.api:app`; liveness probe `GET /healthz` (static, no key required).
- The image must already exist in ACR before the app/Job resources are deployed — revision provisioning validates the image pull at PUT time. `infra/` is therefore two phases: `base.bicep` (everything else) → push image → `workloads.bicep` (app + seed Job).
- The ACR image is always built `--platform linux/amd64` (Container Apps' Consumption profile is amd64-only; dev machines may be arm64).

## 4. Environment variables (SPEC §8 → Azure)

| Variable | Azure source |
|---|---|
| `ZHIPUAI_API_KEY` | Key Vault secret → Container App secret |
| `DATABASE_URL` | Key Vault secret (read-only role, `sslmode=require`) |
| `ADMIN_DATABASE_URL` | **seed Job only** — ephemeral secret, never on the app |
| `LLM_*`, `ROW_LIMIT`, `MAX_RETRIES`, `LOG_LEVEL` | plain Container App env vars |

All three Key Vault secrets (`zhipuai-api-key`, `database-url`, `admin-database-url`) are provisioned by `infra/base.bicep` itself from the two secure deploy parameters (`pgAdminPassword`, `zhipuaiApiKey`) — there is no manual `az keyvault secret set` runbook step. `make infra-up` reads `ZHIPUAI_API_KEY` from `.env` and generates the PG admin password randomly at deploy time (never printed, never stored in a file; recoverable from the Key Vault secret `admin-database-url` if ever needed). For the strictest posture, delete `admin-database-url` from Key Vault after the first successful seed (M3 runbook) — the seed Job is its only consumer.

Operational notes:

- Re-running `make infra-up` rotates the PG admin password (fresh random per run; the server password and the `admin-database-url` secret update together). Self-consistent — nothing caches the old value.
- `admin-database-url` is an ARM resource: a manual deletion is undone by the next `infra-up` (with the rotated password). Treat "delete after first seed" as a break-glass posture, not a persistent state.
- First activation can race RBAC propagation (the identity's Key Vault/ACR roles may not be effective when the very first revision activates). The two-phase `infra-up` (base → build+push → workloads) usually gives propagation enough time; the trailing `az containerapp update` re-pull line (below) doubles as the recovery move if a revision still fails with a Key Vault reference error.
- `:dev` is a moving tag: re-running `infra-up` over an existing deployment re-PUTs identical workloads (no-op) and would keep the old digest. The trailing `az containerapp update --image` forces a fresh revision that re-pulls the tag.

## 5. Seed Job

- Runs `db/seed.py` against Flexible Server: **destructive by design** (`DROP SCHEMA ... CASCADE` rebuild, SPEC §4.2). Bicep pins the Job trigger type to **Manual** so nothing can auto-rerun it.
- Success check: the six row counts equal the table in SPEC §4.2 (seed.py exits non-zero on mismatch, so Job `Succeeded` == counts verified). M3 proved the whole `02_roles.sql` chain (CREATE ROLE / GRANT / ALTER ROLE … SET) works under the non-superuser server admin — the PG16 caveat closed with no seed changes.

**Smoke procedure (M3 runbook)**: the ingress is internal-only, so smoke runs inside the app container — `az containerapp update --min-replicas 1` (the trailing `infra-up` update resets it to 0), wait for a Ready replica, then `az containerapp exec … --command "python3 db/smoke_queries.py"` (five checks: write rejection, 5 s statement timeout, sql / clarify / refuse paths), then `--min-replicas 0`. Note: `--command` is argv-form — no pipes, no quotes; and the exec websocket can drop mid-run on a cold graph: rerun on the warm replica.

## 6. CI (extends SPEC §6.4)

- eval trigger changes from "PRs to main + manual" to **pushes to main + manual** (SPEC §6.4 revised in the same PR that changes `ci.yml`). Rationale: long-lived branches must not burn real-model quota on every push. Accepted trade-off: the eval gate moves to after merge — an eval failure lands on main and requires manual follow-up.
- deploy job (new, pushes to main only): `needs: test` (**not** eval — an eval failure must not block deploying a test-verified image); OIDC federated credentials, no client secret; build/push to ACR → `az containerapp update` → `/healthz` smoke from inside the VNet.

## 7. Milestones (independent of SPEC §11's product milestones)

| Phase | Scope | Definition of Done |
|---|---|---|
| **M0 Docs** | DEPLOYMENT.md + SPEC touch-ups (§6.4 trigger, §9 tree) | Review pass; existing CI green |
| **M1 Image** | `Dockerfile`, `.dockerignore` | Local `docker run`: `/healthz` ok; `/v1/query` end-to-end against local PG + real key |
| **M2 Infra** | `infra/` Bicep: rg, ACR, Key Vault, VNet, Flexible Server, ACA env (workload profiles, Consumption only), app, seed Job | One-command deploy; env shows Consumption-only; server has no public endpoint; ingress internal; Job trigger Manual; `/healthz` via VNet |
| **M3 Seed + verify** | Run seed Job; verify the PG16 caveat; smoke `/v1/query` | Row counts match SPEC §4.2; writes rejected as `t2s_readonly`; 5 s timeout active; sql / clarify / honest-failure smoke pass |
| **M4 CI deploy** | eval trigger change + deploy job (OIDC) | Push to main deploys automatically; PR flow is test-only |

Makefile targets (names fixed here, implemented in M1/M2): `docker-build`, `docker-smoke`, `infra-up`, `infra-seed`, `infra-stop` / `infra-start` (PG stop/start — a stopped server charges storage+backup only; full teardown is `az group delete -g rg-t2s-eastasia`, redeploy via `make infra-up`).

## 8. Cost ballpark (East Asia)

Flexible Server B1ms + storage + backup ≈ $35–45/mo; ACR Basic ≈ $5/mo; Consumption profile with min replicas 0 is request-based; total ≈ **$40–50/mo**.
