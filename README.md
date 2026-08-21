# insurance-text2sql

![CI](https://github.com/Staceyobj/insurance-text2sql/actions/workflows/ci.yml/badge.svg)

Natural-language → PostgreSQL question answering over insurance data (text2SQL). Given one business question in Chinese (or English), the system generates read-only SQL, executes it, and answers in Chinese. Built on the **LangChain** stack: `langchain-openai` for the model layer (structured output against the Zhipu GLM endpoint) with **LangGraph** for agent orchestration.

**Stack** — Python 3.12 (uv) · LangChain / LangGraph (Zhipu GLM via OpenAI-compatible endpoint) · sqlglot (AST-level SQL validation) · FastAPI/uvicorn · psycopg · PostgreSQL 16 (Docker Compose) · pytest · ruff · GitHub Actions

> Demo project: all data is deterministic synthetic insurance data (seed=42) with no real records. Every table and column carries a bilingual (CN/EN) COMMENT, and the schema block in the prompts is generated on the fly from those COMMENTs.

> Design authority: [SPEC.md](SPEC.md) is the single source of truth — the `§` references throughout this README point there.

> Optional extra: a complete **Azure deployment variant** (container image,
> Bicep IaC, private PostgreSQL, CI deploy) lives on the `feat/azure-deploy`
> branch — see [Optional: Azure deployment](#optional-azure-deployment-variant-branch) below.

## Quick start

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/), and make (macOS/Linux). Evaluation and the service need a Zhipu API key.

```bash
cp .env.example .env      # then fill in ZHIPUAI_API_KEY (not needed for test)
make up                   # start PostgreSQL 16 (waits for the healthcheck)
make seed                 # schema + roles + deterministic data (seed=42, byte-identical reruns)
make test                 # 92 offline tests — no network, no API key
make run                  # CLI REPL
```

One-shot questions and options:

```bash
uv run t2s "2024年生效的保单有多少张？"              # "How many policies became effective in 2024?"
uv run t2s "2024年各产品类别的理赔总额是多少？" --show-sql   # show the final normalized SQL
uv run t2s "去年理赔了多少？" --json                       # full state as JSON (rows/trace included)
```

- Every question stands alone — no multi-turn memory. Semantically incomplete questions get a single clarifying question (e.g. "last year" → asks for a concrete year).
- Write operations are always refused, with a reason.

## HTTP service

```bash
uv run uvicorn text2sql.api:app --port 8000
curl -s localhost:8000/healthz
curl -s -X POST localhost:8000/v1/query -H 'Content-Type: application/json' \
     -d '{"question": "2024年生效的保单有多少张？", "debug": false}'
```

Response fields: `action` (sql / clarify / refuse), `answer`, `sql` (the normalized SQL that actually ran), `rows`, `truncated`, `error` (honest failure after exhausted retries surfaces here; HTTP stays 200), `trace` (returned only when `debug=true`). Money values inside `rows` are lossless strings (e.g. `"1159128031.00"`).

## Architecture

```mermaid
flowchart TD
  Q[question] --> R[router]
  R -- clarify/refuse --> A[final reply] --> Z[END]
  R -- sql --> G[generator] --> V[validator] -->|pass| X[executor] --> N[answerer] --> Z
  V -- fail · retries<2 --> G
  X -- error · retries<2 --> G
  V -- retries≥2 --> H[honest failure: states why, never fabricates] --> Z
  X -- retries≥2 --> H
```

- **Five-node agent pipeline built on the LangChain stack** (`router → generator → validator → executor → answerer`): `langchain-openai` provides the model layer — `ChatOpenAI` against the Zhipu endpoint, `with_structured_output(method="function_calling")` for the router/generator outputs — and LangGraph orchestrates the conditional routing and the **shared retry budget** (hard cap 2): validation failures, execution errors, and structured-output parse failures all consume the same counter; once exhausted the flow ends in an honest failure.
- **Two independent read-only layers** (never weaken either):
  1. AST-level whitelist validation (sqlglot, rules R1–R8): single SELECT only, six-table whitelist, system objects banned, function blacklist, `SELECT INTO`/locking clauses banned, LIMIT governance (clamped to ROW_LIMIT+1; truncation reported honestly), and only the normalized re-rendered SQL is ever executed.
  2. The runtime connection holds only the read-only role `t2s_readonly` (per-table GRANT SELECT, `default_transaction_read_only=on`, `statement_timeout=5s`).
- **Transport vs. semantic errors are isolated**: 429/connection errors are digested by client-side exponential backoff (`max_retries=5`) and re-raised past the nodes — they never consume the semantic retry budget.

### Data layer (six tables)

| Table | Rows | Notes |
|---|---|---|
| products | 12 | insurance products (5 category enums) |
| agents | 40 | agents (12 branch cities) |
| customers | 500 | customers (gender / city / risk level) |
| policies | 2,000 | policies (4 statuses; effective per year 2023–2025: 763 / 661 / 576) |
| claims | 600 | claims (4 statuses; amounts ≤ sum assured) |
| payments | 8,000 | installments (4 per policy; ~4% overdue) |

## Evaluation

```bash
make eval                # 42 golden cases against the real LLM (serial by default; --jobs to parallelize)
```

Gates (SPEC §6.3): **refuse 6/6 (hard gate)**, sql ≥ 27/30, clarify ≥ 5/6, total ≥ 38/42. A failed gate exits non-zero; the report lands in `evals/report.md` (gitignored; CI uploads it as an artifact).

Latest local result (glm-4.7): **42/42** (refuse 6/6, sql 30/30, clarify 6/6).
Red line: golden-set expectations are never modified to make an evaluation pass — fixes land in prompts and implementation only.

## Configuration (SPEC §8)

| Variable | Default | Notes |
|---|---|---|
| `ZHIPUAI_API_KEY` | — (required) | Zhipu API key (not needed for `make test`) |
| `LLM_BASE_URL` | `https://open.bigmodel.cn/api/paas/v4` | OpenAI-compatible endpoint (**/v4, not /v1**) |
| `LLM_MODEL` | `glm-4.7` | pinned for eval/CI; glm-4.7-flash allowed for local debugging |
| `LLM_THINKING_ENABLED` | `false` | thinking-mode switch |
| `DATABASE_URL` | `postgresql://t2s_readonly:...@localhost:5432/insurance` | the only connection the app may hold |
| `ADMIN_DATABASE_URL` | `postgresql://postgres:...@localhost:5432/insurance` | **seed flow only** |
| `ROW_LIMIT` / `MAX_RETRIES` | 200 / 2 | row cap / retry hard cap |
| `LOG_LEVEL` | INFO | |

## CI

- **test** (every push/PR, zero secrets): postgres:16 service container → uv sync → ruff → seed → pytest.
- **eval** (PRs to main + manual dispatch): depends on test; uses the `ZHIPUAI_API_KEY` secret; model pinned to glm-4.7; runs the 42 cases serially; uploads the report artifact; the runner's exit code is the gate; fork PRs skip automatically.

## Known limitations (SPEC §2.2/§13)

Read-only queries only — writes are always refused. Clarify is single-round with no conversation memory. No business-metric semantic layer (literal SQL translation is canonical). PostgreSQL dialect only. Model output is not verbatim-stable; correctness is judged by result-set equivalence.

## Fresh-clone walkthrough timing

Measured on a same-machine clone into `/tmp` (M5 walkthrough, including an API smoke check). Everything except `make eval` finishes in about a minute; the total is dominated by LLM response latency, which varies considerably by day — the SPEC §12 ten-minute target is met on typical-latency days and exceeded when the provider is slow.

| Step | Measured |
|---|---|
| `make up` | ~3 s |
| `make seed` | ~13 s |
| `make test` (92 tests) | ~17 s |
| `make eval` (42 cases, `--jobs 2`) | ~14 min this run (42/42 PASS); ~7–8 min on a faster-API day |
| `uv run t2s` (one question) | ~15 s |
| API smoke (`uvicorn` + two curls) | ~13 s |
| **Full chain** | **~14.6 min measured; ≈8 min when API latency is low** |

## Optional: Azure deployment (variant branch)

Cloud deployment is deliberately **optional and branch-scoped**: `main` stays
the pure product. The complete variant — container image, two-phase Bicep
IaC, internal-only Container Apps with a private PostgreSQL 16, a Manual
seed Job, an OIDC deploy job, and the accompanying spec amendments — lives
on the `feat/azure-deploy` branch (start from
[`DEPLOYMENT.md`](https://github.com/Staceyobj/insurance-text2sql/blob/feat/azure-deploy/DEPLOYMENT.md));
`make infra-up` there takes ~30 minutes on a first run (reruns are faster).
