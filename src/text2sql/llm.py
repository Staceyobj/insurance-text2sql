"""LLM construction and prompt loading (SPEC §5.3; hard rules 3 and 8)."""

from __future__ import annotations

from pathlib import Path

from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APITimeoutError, RateLimitError

from text2sql.config import Settings

# Transport-level API failures (langchain's wrapped variants subclass these).
# They are consumed by the openai client's own exponential-backoff retry
# (max_retries below); if one still surfaces, nodes re-raise it instead of
# converting it into error_feedback — SPEC §3's retry budget is for
# validation/execution/parse failures only, and a rate limit is none of them.
TRANSPORT_ERRORS = (APIConnectionError, APITimeoutError, RateLimitError)

# prompts/ lives at the repo root (SPEC §9); resolved relative to this package.
# This intentionally binds the app to a repo checkout ("clone and run"), not
# to an installed wheel — the project is never published as one.
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def build_llm(settings: Settings) -> ChatOpenAI:
    """ChatOpenAI against the Zhipu OpenAI-compatible endpoint.

    - temperature=0 and deliberately NO seed parameter: determinism comes
      from result-set comparison in evaluation, not from the model.
    - Thinking mode is off by default, toggled via extra_body.
    - base_url defaults to .../api/paas/v4 — /v4, NOT /v1 (SPEC §5.3).
    """
    if not settings.zhipuai_api_key:
        raise RuntimeError("ZHIPUAI_API_KEY is not set; put it in .env (see .env.example)")
    thinking = "enabled" if settings.llm_thinking_enabled else "disabled"
    return ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.zhipuai_api_key,
        model=settings.llm_model,
        temperature=0,
        extra_body={"thinking": {"type": thinking}},
        max_retries=5,  # transport-level 429/backoff handled here, not in node semantics
        # Bounds a hung request: the timeout raises APITimeoutError (already in
        # TRANSPORT_ERRORS), so it retries here instead of pending forever.
        timeout=settings.llm_timeout_s,
    )


def load_prompt(name: str) -> str:
    """Read prompts/<name>.md — prompts live only there, zero inline strings."""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
