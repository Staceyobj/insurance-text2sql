# App image (DEPLOYMENT.md §3). One image serves both the API and the seed
# Job — the Job overrides the command with `python db/seed.py`.
#
# Install must be `uv sync` (editable, into the source tree): llm.py resolves
# prompts/ relative to the package via parents[2], so a site-packages install
# (`uv pip install .`) breaks prompt loading.

FROM python:3.12-slim AS builder
# Pinned to the uv that generated uv.lock (same source, closed reproducibility).
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Dependencies first so they cache independently of the source.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /app /app
# prompts/ is resolved relative to the source tree (llm.py parents[2]);
# db/ is inert in the app — only the seed Job executes it.
COPY prompts/ prompts/
COPY db/ db/
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "text2sql.api:app", "--host", "0.0.0.0", "--port", "8000"]
