"""LangGraph assembly: router → generator → validator → executor → answerer,
with the shared retry budget and the honest-failure fallback (SPEC §3).

The validator stays here as an inline step (no nodes/validator.py): M2's
``validate_sql`` is a top-level pure function, and SPEC §9's nodes/ directory
is reserved for router / generator / executor / answerer.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from langgraph.graph import END, START, StateGraph

from text2sql.config import Settings
from text2sql.nodes.answerer import honest_failure_node, make_answerer_node
from text2sql.nodes.executor import execute_sql
from text2sql.nodes.generator import make_generator_node
from text2sql.nodes.router import make_router_node
from text2sql.schema_context import build_schema_context
from text2sql.state import QueryState, digest, trace_entry
from text2sql.validator import validate_sql


def make_validator_step(row_limit: int) -> Callable[[QueryState], dict]:
    """Inline validator step: AST validation + normalization (R1–R8)."""

    def validator_step(state: QueryState) -> dict:
        started = time.perf_counter()
        result = validate_sql(state["sql"], row_limit=row_limit)
        if not result.ok:
            update = {"error_feedback": result.error, "retries": state["retries"] + 1}
        else:
            # only the normalized rendering (R8) ever proceeds to execution
            update = {"sql": result.sql, "error_feedback": None}
        update["trace"] = state["trace"] + [
            trace_entry("validator", started, digest(state["sql"]), digest(result))
        ]
        return update

    return validator_step


def make_executor_step(conninfo: str, row_limit: int) -> Callable[[QueryState], dict]:
    """Inline executor step over M2's execute_sql primitive."""

    def executor_step(state: QueryState) -> dict:
        started = time.perf_counter()
        result = execute_sql(conninfo, state["sql"], row_limit=row_limit)
        if result.error is not None:
            update = {"error_feedback": result.error, "retries": state["retries"] + 1}
        else:
            update = {
                "rows": result.rows,
                "truncated": result.truncated,
                "error_feedback": None,
            }
        update["trace"] = state["trace"] + [
            trace_entry("executor", started, digest(state["sql"]), digest(result))
        ]
        return update

    return executor_step


def build_graph(llm, settings: Settings, schema_context: str | None = None):
    """Assemble the compiled graph; llm is injected so tests can pass a FakeLLM.

    Retry semantics (SPEC §3): every failure increments ``retries`` first;
    ``retries >= max_retries`` (default 2) routes to honest failure, anything
    below loops back to the failing producer.
    """
    if schema_context is None:
        schema_context = build_schema_context(settings.database_url)
    max_retries = settings.max_retries

    def _failed(state: QueryState) -> str:
        if state["retries"] >= max_retries:
            return "honest_failure"
        return "generator"

    def route_after_router(state: QueryState) -> str:
        if state["action"] == "sql":
            return "generator"
        if state["action"] in ("clarify", "refuse"):
            return END
        if state["retries"] >= max_retries:  # unresolved parse failure
            return "honest_failure"
        return "router"  # self-retry

    def after_generator(state: QueryState) -> str:
        if state.get("error_feedback"):
            if state["retries"] >= max_retries:
                return "honest_failure"
            return "generator"
        return "validator"

    def after_validator(state: QueryState) -> str:
        if state.get("error_feedback"):
            return _failed(state)
        return "executor"

    def after_executor(state: QueryState) -> str:
        if state.get("error_feedback"):
            return _failed(state)
        return "answerer"

    graph = StateGraph(QueryState)
    graph.add_node("router", make_router_node(llm))
    graph.add_node("generator", make_generator_node(llm, schema_context))
    graph.add_node("validator", make_validator_step(settings.row_limit))
    graph.add_node("executor", make_executor_step(settings.database_url, settings.row_limit))
    graph.add_node("answerer", make_answerer_node(llm))
    graph.add_node("honest_failure", honest_failure_node)

    graph.add_edge(START, "router")
    graph.add_conditional_edges("router", route_after_router)
    graph.add_conditional_edges("generator", after_generator)
    graph.add_conditional_edges("validator", after_validator)
    graph.add_conditional_edges("executor", after_executor)
    graph.add_edge("answerer", END)
    graph.add_edge("honest_failure", END)
    return graph.compile()
