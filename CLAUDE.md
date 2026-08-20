# CLAUDE.md

Instructions for Claude Code working in this repository.

**SPEC.md is the single source of truth.** If anything in this file, the code, or your
assumptions conflicts with SPEC.md, SPEC.md wins. Spec changes go through a PR — never
silently diverge from it.

## Project

Natural language → PostgreSQL question-answering service (text-to-SQL) over six
insurance tables (deterministic synthetic data, no real records). LangGraph pipeline:
`router → generator → validator → executor → answerer`, with a shared retry budget
and an honest-failure fallback (SPEC §3).

## Commands

| Command | Purpose |
|---|---|
| `make up` / `make down` | Start / stop PostgreSQL 16 (docker compose) |
| `make seed` | Create schema + roles + deterministic data, seed=42 (uses admin connection) |
| `make psql` | psql shell into the database |
| `make test` | `uv run pytest` — must pass fully offline, no API key |
| `make eval` | Golden-set evaluation against the real LLM (needs `ZHIPUAI_API_KEY`) |
| `make run` | CLI REPL |
| `make lint` | `ruff check` |

Use `uv` for all Python operations (`uv sync`, `uv run ...`) — never install
packages with pip directly. Python 3.12.

## Stack

Python 3.12 + uv · LangGraph · langchain-openai · sqlglot (postgres dialect) ·
FastAPI · psycopg · PostgreSQL 16 · pydantic-settings · pytest · ruff

## Hard rules

1. **Two independent security layers, never weaken either** (SPEC §5.4 + §4.3):
   the AST validator (default-deny allowlist, rules R1–R9) AND the runtime
   read-only role `t2s_readonly` with a 5s statement timeout. The application
   must never hold the admin connection string; `ADMIN_DATABASE_URL` is for the
   seed flow only.
2. **Golden-set cases use absolute dates only** — no relative time such as
   "the last three months" (SPEC §6.1). **Never modify golden-set expectations
   to make an eval pass.** If evaluation fails, fix prompts or implementation.
   Any legitimate golden-set revision must be justified explicitly in its
   commit message.
3. **Prompts live only in `prompts/*.md`.** Zero inline prompt strings in code.
4. **Deterministic seeding** (SPEC §4.2): all randomness from `random.Random(42)`,
   fixed generation order. Never use `uuid4()`, `now()`, or `date.today()` in
   `db/seed.py`. Business time window is 2023-01-01 … 2025-12-31.
5. **`make test` runs offline** — no network, no API keys. Graph tests inject a
   FakeLLM; only the eval suite (§6) talks to the real model.
6. **Schema context is generated from DB COMMENTs** via `schema_context.py` —
   never hand-write a duplicate schema description. Every table and column in
   `db/01_schema.sql` carries a bilingual COMMENT; enum columns list all values.
7. **Structured-output JSON parse failures are treated as validation failures**:
   write the error into `error_feedback` and consume the shared `retries`
   counter (hard cap `MAX_RETRIES=2`). After the cap: honest failure — say the
   query could not be completed and why; never fabricate results.
8. **LLM configuration** (SPEC §5.3, §8): `ChatOpenAI` against the ZhipuAI
   OpenAI-compatible endpoint, `base_url=https://open.bigmodel.cn/api/paas/v4`
   (note `/v4`, **not** `/v1` — beware of proxies that force-append `/v1`).
   Model `glm-4.7` for eval and CI; `glm-4.7-flash` is allowed for local
   debugging only. `temperature=0`; **do not pass a seed parameter** —
   determinism is handled by result-set comparison in evaluation, not by the
   model. Structured output uses `method="function_calling"`. Thinking mode is
   off by default (`LLM_THINKING_ENABLED=false`, sent via `extra_body`).

## Workflow

- Follow the milestones in SPEC §11. **Work on exactly one stage at a
  time**; commit when that stage's DoD is met. Do not start the next stage
  unless explicitly asked.
- Run `make test` after completing each module; only move on to the next item
  when everything is green.
- TDD for `validator.py`: write `tests/test_validator.py` first. Every rule
  R1–R9 in SPEC §5.4 needs at least one positive and one negative test case.
- All configuration through pydantic-settings / environment variables (SPEC §8).
  Never commit `.env`; update `.env.example` whenever you add a variable.
- The directory layout is fixed in SPEC §9 — do not invent new top-level
  directories or move files across the defined boundaries.
- CI (SPEC §6.4): the test job needs no secrets; the eval job uses the
  `ZHIPUAI_API_KEY` secret and the `glm-4.7` model, and is skipped on fork PRs.

## Code conventions

- Type hints everywhere. `QueryState` is a TypedDict; `RouteResult` is a
  Pydantic model (SPEC §5.1–5.2).
- `validator.py` is a pure function: no I/O, no globals, deterministic; it
  returns the sqlglot-rendered normalized SQL on success, and the executor runs
  only that normalized version.
- Error text fed back to the generator via `error_feedback` must be concise and
  actionable (parser error, violated rule ID, or DB error message).
- `ruff check` must be clean before every commit. Keep functions small; no
  speculative abstractions beyond what SPEC requires.

## When unsure

Check SPEC.md first. If it is still ambiguous, ask instead of guessing. Do not
add features beyond SPEC §2.1 — the exclusions in §2.2 are deliberate decisions,
not gaps.