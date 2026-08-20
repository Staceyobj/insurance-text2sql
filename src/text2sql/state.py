"""Pipeline state and structured-output models (SPEC §5.1–5.2)."""

from __future__ import annotations

import hashlib
import time
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


class QueryState(TypedDict):
    """Shared LangGraph state, verbatim from SPEC §5.1."""

    question: str
    action: Literal["sql", "clarify", "refuse"] | None
    sql: str | None
    error_feedback: str | None  # validation/execution error fed back to generator
    retries: int  # hard cap 2; reaching it triggers honest failure
    rows: list[dict] | None
    truncated: bool
    answer: str | None
    trace: list[dict]


def new_state(question: str) -> QueryState:
    """Fresh state for one question."""
    return QueryState(
        question=question,
        action=None,
        sql=None,
        error_feedback=None,
        retries=0,
        rows=None,
        truncated=False,
        answer=None,
        trace=[],
    )


class RouteResult(BaseModel):
    """Router structured output (SPEC §5.2)."""

    action: Literal["sql", "clarify", "refuse"]
    clarify_question: str | None = None
    refuse_reason: str | None = None


class SqlResult(BaseModel):
    """Generator structured output: one SELECT statement."""

    sql: str = Field(description="The single PostgreSQL SELECT statement to run")


def digest(value: Any, limit: int = 72) -> str:
    """Short hash + preview of an arbitrary value, for trace records."""
    text = repr(value)
    short = hashlib.sha1(text.encode()).hexdigest()[:8]
    preview = text if len(text) <= limit else text[:limit] + "…"
    return f"{short} {preview}"


def trace_entry(node: str, started: float, input_digest: str, output_digest: str) -> dict:
    """One trace record: {node, duration_ms, input_digest, output_digest}."""
    return {
        "node": node,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "input_digest": input_digest,
        "output_digest": output_digest,
    }
