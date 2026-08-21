.PHONY: up down seed psql test eval run lint docker-build docker-smoke infra-up infra-seed infra-stop infra-start

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

# Build the app image (DEPLOYMENT.md §3).
docker-build:
	docker build -t insurance-text2sql:dev .

# Container smoke against the local compose PG: /healthz plus one real
# /v1/query (needs ZHIPUAI_API_KEY in .env). Inside the container the
# database is reached via host.docker.internal, not localhost — that name is
# Docker Desktop (macOS/Windows); on Linux add
# --add-host=host.docker.internal:host-gateway to the docker run line.
docker-smoke: docker-build
	envfile=$$(mktemp); \
	grep -v '^ADMIN_DATABASE_URL=' .env > $$envfile; \
	trap 'rm -f $$envfile; docker stop t2s-smoke >/dev/null 2>&1' EXIT; \
	docker run --rm -d --name t2s-smoke -p 8000:8000 \
		--env-file $$envfile \
		-e DATABASE_URL=postgresql://t2s_readonly:t2s_readonly@host.docker.internal:5432/insurance \
		insurance-text2sql:dev; \
	n=0; until curl -sf localhost:8000/healthz >/dev/null; do \
		n=$$((n+1)); [ $$n -gt 60 ] && { echo 'healthz timed out'; exit 1; }; sleep 0.5; done; \
	curl -sf localhost:8000/healthz && echo && \
	curl -sf -X POST localhost:8000/v1/query \
		-H 'content-type: application/json' \
		-d '{"question": "2024年各产品类别分别有多少张保单？"}' | head -c 600 && echo

# --- Azure (DEPLOYMENT.md §7) ---

# Deploy the full stack in two phases (revision provisioning validates the
# image pull at PUT time, so the image must exist before the workloads):
#   1. base.bicep  — network, PG, ACR, KV (secrets from params), env
#   2. push image, then workloads.bicep — app + Manual seed Job
# The trailing `az containerapp update` forces a fresh revision: re-pushing
# :dev over an existing deployment with an unchanged workloads template alone
# is a no-op and would keep serving the old digest.
# The ACR image is built --platform linux/amd64: Container Apps' Consumption
# profile is amd64-only, and this repo is developed on Apple Silicon (arm64).
# Local docker-build/docker-smoke stay native-arch.
# Requires: az login; ZHIPUAI_API_KEY in .env. The PG admin password is
# generated at deploy time (never printed, never stored in a file) and is
# recoverable afterwards from the Key Vault secret `admin-database-url`.
infra-up:
	key=$$(grep '^ZHIPUAI_API_KEY=' .env | cut -d= -f2-); \
	test -n "$$key" || { echo 'ZHIPUAI_API_KEY missing in .env'; exit 1; }; \
	pgpass="Az$$(LC_ALL=C tr -dc 'a-zA-Z0-9' </dev/urandom | head -c 24)9!"; \
	az group create -l eastasia -n rg-t2s-eastasia -o none && \
	az deployment group create -g rg-t2s-eastasia -f infra/base.bicep \
		-p pgAdminPassword="$$pgpass" zhipuaiApiKey="$$key" -o none && \
	out=$$(az deployment group show -g rg-t2s-eastasia -n base \
		--query 'properties.outputs' -o json) && \
	acr=$$(echo "$$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["acrLoginServer"]["value"])') && \
	az acr login -n "$${acr%%.*}" && \
	docker build --platform linux/amd64 -t "$$acr/insurance-text2sql:dev" . && \
	docker push "$$acr/insurance-text2sql:dev" && \
	az deployment group create -g rg-t2s-eastasia -f infra/workloads.bicep \
		-p environmentId="$$(echo "$$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["environmentId"]["value"])')" \
		   identityId="$$(echo "$$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["identityId"]["value"])')" \
		   acrLoginServer="$$acr" \
		   secretUriZhipu="$$(echo "$$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["secretUriZhipu"]["value"])')" \
		   secretUriDatabase="$$(echo "$$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["secretUriDatabase"]["value"])')" \
		   secretUriAdmin="$$(echo "$$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["secretUriAdmin"]["value"])')" \
		-o none && \
	az containerapp update -n t2s-app -g rg-t2s-eastasia \
		--image "$$acr/insurance-text2sql:dev" -o none && \
	echo "deployed; app: t2s-app (internal-only), pg: $$(echo "$$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["postgresHost"]["value"])')"

# Start the Manual seed Job (destructive: DROP SCHEMA rebuild) and list its
# executions. Verify row counts in the execution logs (M3).
infra-seed:
	az containerapp job start -n t2s-seed -g rg-t2s-eastasia -o none && \
	az containerapp job execution list -n t2s-seed -g rg-t2s-eastasia -o table

# Stop paying for idle: PG stops charging compute (storage+backup only);
# Container Apps already idles at ~0 (min replicas 0). Full teardown instead:
# az group delete -g rg-t2s-eastasia (redeploy later via make infra-up).
infra-stop:
	az postgres flexible-server stop -g rg-t2s-eastasia \
		-n $$(az postgres flexible-server list -g rg-t2s-eastasia --query '[0].name' -o tsv) -o none && \
	echo 'PG stopped (storage+backup only from now)'

infra-start:
	az postgres flexible-server start -g rg-t2s-eastasia \
		-n $$(az postgres flexible-server list -g rg-t2s-eastasia --query '[0].name' -o tsv) -o none && \
	echo 'PG started'
