"""Answerer node plus the deterministic honest-failure composer (SPEC §3, §5.2)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable

from langchain_core.messages import HumanMessage, SystemMessage

from text2sql.llm import load_prompt
from text2sql.state import QueryState, digest, trace_entry

# Deterministic output text (never sent to an LLM), so it is a code constant
# rather than a prompt file — hard rule 3 concerns model-facing prompts.
HONEST_FAILURE_TEMPLATE = (
    "抱歉，本次查询未能完成：{error}。请稍后重试，或换一种问法（例如给出明确的时间范围）。"
)


def make_answerer_node(llm) -> Callable[[QueryState], dict]:
    """Wrap an LLM into the answerer node (plain text, no structured output)."""
    system = SystemMessage(content=load_prompt("answerer"))

    def answerer_node(state: QueryState) -> dict:
        started = time.perf_counter()
        content = (
            f"【问题】{state['question']}\n"
            f"【SQL】{state['sql']}\n"
            f"【查询结果】{json.dumps(state['rows'], ensure_ascii=False, default=str)}\n"
            f"【截断】{'true' if state['truncated'] else 'false'}"
        )
        reply = llm.invoke([system, HumanMessage(content=content)])
        return {
            "answer": reply.content,
            "trace": state["trace"] + [
                trace_entry("answerer", started, digest(content), digest(reply.content))
            ],
        }

    return answerer_node


def honest_failure_node(state: QueryState) -> dict:
    """Deterministic honest failure once retries are exhausted — never fabricates."""
    started = time.perf_counter()
    error = state.get("error_feedback") or "未知错误"
    return {
        "answer": HONEST_FAILURE_TEMPLATE.format(error=error),
        "trace": state["trace"] + [
            trace_entry("honest_failure", started, digest(state["question"]), digest(error))
        ],
    }
