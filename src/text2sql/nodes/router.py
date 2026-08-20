"""Router node: classify the question (SPEC §5.2; hard rule 7)."""

from __future__ import annotations

import time
from collections.abc import Callable

from langchain_core.messages import HumanMessage, SystemMessage

from text2sql.llm import TRANSPORT_ERRORS, load_prompt
from text2sql.state import QueryState, RouteResult, digest, trace_entry


def make_router_node(llm) -> Callable[[QueryState], dict]:
    """Wrap an LLM into the router node (LLM injected for FakeLLM tests)."""
    structured = llm.with_structured_output(
        RouteResult, method="function_calling", include_raw=True
    )
    system = SystemMessage(content=load_prompt("router"))

    def router_node(state: QueryState) -> dict:
        started = time.perf_counter()
        question = state["question"]
        messages = [system, HumanMessage(content=question)]
        try:
            route = structured.invoke(messages).get("parsed")
            if route is None or route.action not in ("sql", "clarify", "refuse"):
                raise ValueError(f"unroutable output: {route!r}")
        except TRANSPORT_ERRORS:
            raise  # infrastructure error: never burns the semantic retry budget
        except Exception as err:  # structured-output failure == validation failure
            update = {
                "action": None,
                "error_feedback": f"router parse failure: {err}",
                "retries": state["retries"] + 1,
            }
        else:
            # clarify/refuse bypass the answerer: these strings are final
            answer = None
            if route.action == "clarify":
                answer = route.clarify_question or "请补充更多信息以便查询。"
            elif route.action == "refuse":
                answer = route.refuse_reason or "该请求不在支持范围内。"
            update = {"action": route.action, "answer": answer, "error_feedback": None}
        update["trace"] = state["trace"] + [
            trace_entry("router", started, digest(question), digest(update))
        ]
        return update

    return router_node
