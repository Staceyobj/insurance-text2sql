.PHONY: up down seed psql test eval run lint

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
