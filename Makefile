.PHONY: up down seed psql test eval run lint api frontend-dev frontend-test frontend-build

# Start PostgreSQL 16; --wait blocks until the healthcheck passes.
up:
	docker compose up -d --wait

# Stop PostgreSQL (keeps the pgdata volume).
down:
	docker compose down

# Schema + roles + deterministic seed data (seed=42, uses ADMIN_DATABASE_URL).
seed: up
	uv run python db/seed.py

# psql shell into the database, inside the container (no local psql needed).
psql:
	docker compose exec postgres psql -U postgres -d insurance

# Offline unit tests: no network, no API key.
test:
	uv run pytest

# Golden-set evaluation against the real LLM (needs ZHIPUAI_API_KEY).
eval:
	uv run python evals/runner.py

# CLI REPL.
run:
	uv run t2s

lint:
	uv run ruff check .

# HTTP API dev server. /healthz needs nothing; /v1/query needs DB + API key.
api:
	uv run uvicorn text2sql.api:app --reload --port 8000

# Vite dev server (:5173) — proxies /v1 + /healthz to :8000, so zero CORS.
frontend-dev:
	cd frontend && ([ -d node_modules ] || npm install) && npm run dev

# Lint + unit tests for the frontend (offline, no DB, no browser).
frontend-test:
	cd frontend && npm run lint && npm run test

# Production build → frontend/dist (served by `uvicorn` once it exists).
frontend-build:
	cd frontend && npm run build
