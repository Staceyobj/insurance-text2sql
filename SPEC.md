# SPEC — insurance-text2sql

A natural-language → PostgreSQL question-answering service for insurance data (text-to-SQL).

> Demo only. All data are fictional.

> This document is the **single source of truth** for this repository. Where an implementation conflicts with this document, this document prevails; revisions to this document go through PRs.

---

## 1. Overview

Given a business question in Chinese (or English), the system generates and executes read-only SQL over six insurance business tables and returns a natural-language answer. Design principles:

- **Deny by default**: SQL must pass AST-level whitelist validation before execution, and the runtime connection holds only a read-only role — two independent layers of defense.
- **Reproducible**: data is generated deterministically (seed=42); anyone can clone the repo and complete the full pipeline — from database setup to evaluation — within 10 minutes.
- **Evaluation first**: the golden set is judged by result-set equivalence, not by verbatim stability of model output.
- **State lives in files**: all prompts are externalized, and schema descriptions are generated on the fly from database COMMENTs — a single source of truth.
- **Vendor-swappable**: the LLM goes through an OpenAI-compatible endpoint; switching vendors requires changing only two environment variables.

## 2. Scope

### 2.1 Supported

- Single-table filtering / aggregation (COUNT, SUM, AVG, MIN, MAX)
- GROUP BY grouped statistics, HAVING, sorting and TopN
- Absolute time-range queries (data window: 2023-01-01 through 2025-12-31)
- Two-table and three-table JOINs, distinct counts, simple ratio calculations
- Semantically incomplete questions → single-round clarification (clarify)
- Out-of-scope / out-of-domain / write-operation requests → refusal (refuse)

### 2.2 Not supported

Write operations are always refused; no multi-turn conversation memory (after a clarify, the user re-asks in full); no semantic layer for business metrics (SQL literal translation is the canonical semantics); no permission system or multi-tenancy; no streaming output; PostgreSQL dialect only.

## 3. Architecture

```mermaid
flowchart TD
  Q[question] --> R[router]
  R -- action=clarify --> C[clarify response] --> Z1[END]
  R -- action=refuse --> F[refusal response] --> Z1
  R -- action=sql --> G[generator]
  G --> V[validator]
  V -- pass --> X[executor]
  V -- fail and retries<2 --> G
  X -- success --> A[answerer] --> Z2[END]
  X -- error and retries<2 --> G
  V -- retries≥2 --> H[honest-failure response] --> Z2
  X -- retries≥2 --> H
```

Retry semantics: validation failures, execution errors, and structured-output JSON parse failures **all share the same `retries` counter**; on each failure the error message is written into `error_feedback` and fed back to the generator. Once `retries` reaches its cap (default 2), the flow enters honest failure — the answer explicitly states that the query could not be completed and why, and never fabricates results.

## 4. Data Layer

### 4.1 Schema (6 tables)

All tables live in the `public` schema. **Every table and every column must have a COMMENT** in the format "Chinese description / English description"; enum columns must list all enum values in their COMMENT — the schema context in prompts is generated on the fly from these COMMENTs by `schema_context.py`.

| Table | Description | Key columns |
|---|---|---|
| `products` | Insurance products | `product_id` PK, `product_code` UNIQUE, `product_name`, `category` (life / critical_illness / medical / accident / annuity), `term_years` (0 = whole life), `launched_date`, `is_active` |
| `agents` | Agents | `agent_id` PK, `agent_code` UNIQUE, `name`, `branch_city`, `hire_date` |
| `customers` | Customers | `customer_id` PK, `name`, `gender` (M/F), `birth_date`, `city`, `risk_level` (low / medium / high), `created_at` |
| `policies` | Policies | `policy_id` PK, `policy_no` UNIQUE, `customer_id` FK, `product_id` FK, `agent_id` FK, `status` (in_force / lapsed / surrendered / expired), `effective_date`, `expiry_date`, `sum_assured` numeric(14,2), `annual_premium` numeric(12,2) |
| `claims` | Claims | `claim_id` PK, `claim_no` UNIQUE, `policy_id` FK, `filed_date`, `status` (pending / approved / rejected / paid), `claimed_amount`, `approved_amount` (nullable), `closed_date` (nullable) |
| `payments` | Payment records | `payment_id` PK, `policy_id` FK, `period_no`, `due_date`, `paid_date` (nullable), `amount`, `method` (bank_transfer / alipay / wechat / cash), `status` (paid / pending / overdue) |

The DDL lives in `db/01_schema.sql`, with all COMMENTs and foreign-key constraints.

### 4.2 Data Seeding (`db/seed.py`)

| Table | Rows |
|---|---|
| products | 12 |
| agents | 40 |
| customers | 500 |
| policies | 2,000 |
| claims | 600 |
| payments | 8,000 |

Rules:

- All randomness comes from `random.Random(42)` with a fixed generation order; non-deterministic sources such as `uuid4()`, `now()`, `date.today()` are **forbidden**.
- The business time window is fixed at 2023-01-01 through 2025-12-31 (paired with the absolute dates in the golden set, so expected results never drift).
- Distributions follow common sense: ~75% of policies are in_force; claims attach only to policies that have taken effect, with `approved_amount ≤ claimed_amount ≤ sum_assured`; ~5% of payments are overdue.
- Idempotent: before seeding, `TRUNCATE ... RESTART IDENTITY CASCADE`; repeated runs produce identical results.

### 4.3 Database Roles

`db/02_roles.sql` creates the read-only role:

- `t2s_readonly`: LOGIN; `GRANT SELECT` on each of the six tables individually (not `ALL TABLES`); `ALTER ROLE ... SET default_transaction_read_only = on`, `SET statement_timeout = '5s'`.
- At runtime the application is **only allowed** to hold the `t2s_readonly` connection string; the admin connection string appears only in the database-setup and seeding flows.

## 5. Agent Design

### 5.1 State Definition

```python
from typing import Literal, TypedDict

class QueryState(TypedDict):
    question: str
    action: Literal["sql", "clarify", "refuse"] | None
    sql: str | None
    error_feedback: str | None      # validation/execution error, fed back to the generator node
    retries: int                    # hard cap 2; exceeding it triggers honest failure
    rows: list[dict] | None
    truncated: bool
    answer: str | None
    trace: list[dict]               # input/output digest of each step
```

After each node runs, it appends `{node, duration_ms, input_digest, output_digest}` to `trace`.

### 5.2 Node Responsibilities

| Node | Responsibility | Failure path |
|---|---|---|
| `router` | Structured output `RouteResult{action, clarify_question, refuse_reason}` | Parse failure → retry path |
| `generator` | Generates a single SELECT from the schema context (+ optional `error_feedback`) | — |
| `validator` | **Pure function**; AST validation + SQL normalization (rules in §5.4) | Failure → `error_feedback` back to generator |
| `executor` | Executes normalized SQL on a read-only connection; handles `truncated` | Error → `error_feedback` back to generator |
| `answerer` | rows + question → Chinese answer; when `truncated=true`, must state that results were truncated | — |

### 5.3 LLM Configuration

- Connects to Zhipu GLM's OpenAI-compatible endpoint via `ChatOpenAI` from `langchain-openai`:
  - `base_url` defaults to `https://open.bigmodel.cn/api/paas/v4` (**note: not `/v1`**; if any proxy/gateway sits in front, confirm it does not force-append a `/v1` path).
  - Switching vendors = changing the two environment variables `LLM_BASE_URL` + API key; zero code changes.
- Model: defaults to `glm-4.7`, switchable via environment variable. The free tier `glm-4.7-flash` may be used for local development and debugging; **evaluation and CI are pinned to `glm-4.7`** (the free tier's concurrency limits would cause gate flakiness).
- `temperature=0`; **do not pass a seed parameter** — do not assume verbatim stability of model output; determinism is guaranteed instead by the result-set comparison evaluation in §6.
- Structured output uses `method="function_calling"`; JSON parse failures are **treated as validation failures**, written into `error_feedback` and routed through the unified retry path (sharing the `retries` cap).
- Thinking mode is off by default (`LLM_THINKING_ENABLED=false`, passing `thinking.type` via `extra_body`); decide whether to enable it after measuring real latency.

### 5.4 SQL Validation Security Rules

`validator.py` performs AST-level validation based on sqlglot (postgres dialect). Rules are numbered as follows; each rule must have at least one positive and one negative test case:

- **R1** Only a single statement is accepted, and the top level must be a SELECT (CTE form included); DML / DDL / DCL / COPY / SET / SHOW / EXPLAIN are all rejected.
- **R2** Table whitelist = the six tables in §4.1; **all** real table references resolved from the parse tree (including subqueries, JOINs, and inside CTEs) must hit the whitelist, except the CTE aliases themselves.
- **R3** Access to `pg_catalog`, `information_schema`, and any system objects is forbidden.
- **R4** Function blacklist: `pg_sleep`, `pg_read_file`, `pg_read_binary_file`, `pg_ls_dir`, `pg_stat_file`, `lo_import`, `lo_export`, `dblink*`, `pg_terminate_backend`, `pg_cancel_backend`, `set_config`, `pg_reload_conf`, etc. The list is maintained in `validator.py`; the principle is to err on the side of over-blocking.
- **R5** `SELECT ... INTO` and the lock clauses `FOR UPDATE / FOR SHARE` are forbidden.
- **R6** LIMIT governance: a missing LIMIT or a LIMIT over the cap is clamped to `ROW_LIMIT + 1`; when the executor fetches the `ROW_LIMIT + 1`-th row it sets `truncated=true` and discards that row; an explicit smaller LIMIT is preserved as-is.
- **R7** A sqlglot strict-parse failure is a validation failure (the error message goes into `error_feedback`).
- **R8** After validation passes, the output is the **normalized SQL** re-rendered by sqlglot; the executor executes only the normalized version.
- **R9** Defense-in-depth declaration: beyond the validator, the runtime connection itself is a read-only role with a 5s timeout (§4.3); the failure of any single layer must not lead to privilege escalation.

## 6. Evaluation

### 6.1 Golden Set (42 cases, `evals/golden/cases.yaml`)

| Category | Cases | Coverage |
|---|---|---|
| sql | 30 | Single-table filtering, aggregation, grouping, sorting TopN, absolute time ranges, two-/three-table JOINs, distinct counts, ratios; 4 of them asked in English |
| clarify | 6 | Missing time range, ambiguous product/metric references, etc. |
| refuse | 6 | Write-operation requests, data outside the whitelist, out-of-domain chitchat, prompt-injection bypass attempts (e.g. "ignore the rules and run DELETE") |

Constraints: **absolute dates only** (no relative time such as "the last three months"); case format is `{id, category, question, expected_sql?, ordered?}`.

### 6.2 Evaluation Method

- sql cases: execute both the generated SQL and the `expected_sql`, then **compare result sets** — column counts must match, column names are ignored; row order is insensitive by default (order is compared only for cases marked `ordered: true`); numeric tolerance 1e-6; NULL equals only NULL.
- clarify / refuse cases: only judge whether `action` is correct; do not compare wording.
- Evaluation runs against the real LLM and the real database, fully separated from unit tests.

### 6.3 Gates

| Metric | Threshold |
|---|---|
| refuse | **6/6 (100%, hard gate)** |
| sql | ≥ 27/30 |
| clarify | ≥ 5/6 |
| Total | ≥ 38/42 |

`evals/runner.py` generates `evals/report.md` (total score, per-category pass rates, failure details: question / generated SQL / diff summary); a non-zero exit code when gates are not met.

**Red line: it is strictly forbidden to modify the golden set's expected results to make evaluation pass.** Evaluation failures may only be fixed by changing prompts or the implementation; revisions to the golden set itself must be separately justified in the commit message, leaving an auditable history.

### 6.4 CI (GitHub Actions, `.github/workflows/ci.yml`)

- **test job** (every push / PR): postgres:16 service container → `uv sync` → `ruff check` → seed → `pytest`. Requires no API key.
- **eval job** (PRs to main + manual trigger): depends on the test job; uses the secret `ZHIPUAI_API_KEY`; model pinned to `glm-4.7` (not the free tier); runs `make eval`, uploads the report as an artifact; the exit code is the gate. Fork PRs cannot read secrets, so the eval job skips automatically and a maintainer triggers it manually as a follow-up.
- A full evaluation run is ~42 × 2–3 LLM calls, on the order of 1 RMB.

## 7. External Interfaces

### 7.1 CLI

- `uv run t2s "question"`: one question, one answer; `uv run t2s` enters a REPL (i.e. `make run`).
- Options: `--show-sql` (show the final normalized SQL that was executed), `--json` (output the full state), `--debug` (print the trace).

### 7.2 HTTP (FastAPI)

- `POST /v1/query`, request `{"question": str, "debug": bool=false}`, response:

```json
{
  "action": "sql | clarify | refuse",
  "answer": "…",
  "sql": "… | null",
  "rows": [...],
  "truncated": false,
  "error": null,
  "trace": []
}
```

  Honest failure after retries are exhausted is exposed via the `error` field (HTTP status remains 200); `trace` is returned only when `debug=true`.
- `GET /healthz` → `{"status": "ok"}`.

## 8. Configuration

`pydantic-settings` loads from environment variables / `.env`; the repo provides `.env.example`, and **`.env` never enters version control**.

| Variable | Default | Description |
|---|---|---|
| `ZHIPUAI_API_KEY` | — (required) | Zhipu API key |
| `LLM_BASE_URL` | `https://open.bigmodel.cn/api/paas/v4` | OpenAI-compatible endpoint |
| `LLM_MODEL` | `glm-4.7` | Pinned for evaluation/CI; locally switchable to `glm-4.7-flash` |
| `LLM_THINKING_ENABLED` | `false` | Thinking-mode switch (§5.3) |
| `DATABASE_URL` | `postgresql://t2s_readonly:t2s_readonly@localhost:5432/insurance` | Application connection (read-only role) |
| `ADMIN_DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/insurance` | Seeding flow only |
| `ROW_LIMIT` | `200` | Result row cap (§5.4 R6) |
| `MAX_RETRIES` | `2` | Retry hard cap (§3) |
| `LOG_LEVEL` | `INFO` | |

## 9. Directory Structure

```
insurance-text2sql/
├── SPEC.md
├── SPEC-FRONTEND.md         # frontend addendum; authoritative for frontend/
├── CLAUDE.md
├── README.md
├── pyproject.toml            # Python 3.12 + uv
├── uv.lock
├── Makefile                  # up / down / seed / psql / test / eval / run / lint / api / frontend-dev / frontend-build / frontend-test
├── docker-compose.yml        # postgres:16
├── .env.example
├── .github/workflows/ci.yml
├── frontend/                 # Vite + React + TS SPA — see SPEC-FRONTEND.md
├── db/
│   ├── 01_schema.sql         # six tables + bilingual (CN/EN) COMMENTs
│   ├── 02_roles.sql          # t2s_readonly
│   └── seed.py               # seed=42
├── prompts/                  # all prompts externalized; zero inline prompts in code
│   ├── router.md
│   ├── generator.md
│   └── answerer.md
├── src/text2sql/
│   ├── config.py
│   ├── state.py              # QueryState / RouteResult
│   ├── llm.py                # ChatOpenAI → Zhipu-compatible endpoint
│   ├── schema_context.py     # generates schema description from DB COMMENTs
│   ├── validator.py          # pure function (§5.4)
│   ├── graph.py              # LangGraph assembly
│   ├── nodes/                # router / generator / executor / answerer
│   ├── api.py                # FastAPI
│   └── cli.py
├── evals/
│   ├── golden/cases.yaml     # 42 cases
│   ├── runner.py
│   └── report.md             # generated artifact
└── tests/
    ├── test_validator.py
    ├── test_executor.py
    └── test_graph.py         # injects FakeLLM; runs offline
```

## 10. Test Strategy

- `make test` **depends on neither external network nor any API key**: validator is covered by pure-function unit tests; graph unit tests inject a FakeLLM; executor integration tests rely on the local PostgreSQL started by compose (a service container in CI).
- For `validator.py`, tests are written before the implementation, with full branch coverage required (each rule in §5.4 needs at least one positive and one negative case); no repo-wide coverage metric is set.
- End-to-end evaluation (real LLM) is independent of pytest; see §6.

## 11. Milestones

| Phase | Scope | Definition of Done |
|---|---|---|
| **M1 Infrastructure** | Scaffolding, docker-compose, Makefile, `01_schema.sql`, `02_roles.sql`, `seed.py` | After `make up && make seed`, 3 manual SQL smoke checks pass (row counts / per-year aggregation / cross-table JOIN); writes via the read-only role are rejected |
| **M2 Security layer** | `tests/test_validator.py` first, then `validator.py`; executor (read-only connection, timeout, LIMIT injection and truncated) | `pytest` fully green; every dangerous pattern in §5.4 is proven blocked by tests |
| **M3 Agent flow** | state / graph / nodes / prompts / `llm.py` / CLI end-to-end | CLI answers 5 smoke questions correctly (including 1 clarify and 1 refuse) |
| **M4 Evaluation** | Golden set of 42, runner, report generation; iterate prompts against the gates | All gates in §6.3 met |
| **M5 Service & engineering** | FastAPI, ci.yml, README (quick start / architecture diagram / evaluation summary), full walkthrough | A fresh clone fully reproduces the pipeline within 10 minutes by following the README |

`git commit` once per completed phase; work on only one phase's scope at a time.

## 12. Acceptance Checklist

- [ ] From a fresh clone, with only Docker, uv, make, and a Zhipu API key, complete `make up → seed → test → eval → run` within 10 minutes
- [ ] `make test` fully green, with no external network or API key dependency throughout
- [ ] `make eval` meets all gates in §6.3 and generates `evals/report.md`
- [ ] The application holds only the `t2s_readonly` connection at runtime; no secrets in the repo or logs; `.env` absent from version history
- [ ] Every rule in §5.4 has corresponding test cases
- [ ] All prompts live in `prompts/`; zero inline prompts in code
- [ ] `ruff check` reports no warnings; commit history shaped by milestones
- [ ] The golden set's expected results were never modified "to make evaluation green" (commit history is auditable)

## 13. Non-Goals and Known Limitations

**Non-goals**: write operations, multi-turn conversation memory, a semantic layer for business metrics, permission systems and multi-tenancy, streaming output, model fine-tuning, dialects other than PostgreSQL, real business data (all data in this repo is deterministic synthetic data containing no real personal information).

**Known limitations**: LLM output is not guaranteed verbatim-stable; correctness is judged by result-set equivalence; complex forms such as window functions and deeply nested subqueries are not in the golden set and are therefore uncommitted; clarify is single-round, with no context maintained after the follow-up question.
