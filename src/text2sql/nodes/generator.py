"""Generator node: produce one SELECT from the schema context (SPEC §5.2)."""

from __future__ import annotations

import re
import time
from collections.abc import Callable

from langchain_core.messages import HumanMessage, SystemMessage

from text2sql.llm import TRANSPORT_ERRORS, load_prompt
from text2sql.state import QueryState, SqlResult, digest, trace_entry

_FENCE_RE = re.compile(r"```(?:sql)?\s*(.+?)\s*```", re.DOTALL | re.IGNORECASE)


def _extract_sql(text: str | None) -> str | None:
    """Salvage SQL when the model answers as plain text instead of a tool call.

    Observed with glm-4.7 on English questions: the reply is exactly the SQL
    statement, but not via a function call. The salvaged text still goes
    through the full R1–R8 validation, so this is safe by construction.
    """
    candidate = (text or "").strip()
    fenced = _FENCE_RE.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()
    if candidate.lower().startswith(("select", "with")):
        return candidate
    return None


def make_generator_node(llm, schema_context: str) -> Callable[[QueryState], dict]:
    """Wrap an LLM into the generator node; schema context is baked in once."""
    structured = llm.with_structured_output(
        SqlResult, method="function_calling", include_raw=True
    )
    prompt = load_prompt("generator").replace("{schema_context}", schema_context)
    system = SystemMessage(content=prompt)

    def generator_node(state: QueryState) -> dict:
        started = time.perf_counter()
        content = state["question"]
        if state.get("error_feedback"):
            previous = state.get("sql") or "（无）"
            content += f"\n\n【上次错误】{state['error_feedback']}\n【上一版 SQL】{previous}"
        messages = [system, HumanMessage(content=content)]
        try:
            outcome = structured.invoke(messages)  # {raw, parsed, parsing_error}
            parsed = outcome.get("parsed")
            if parsed is not None:
                sql = parsed.sql
                if not sql or not sql.strip():
                    raise ValueError("empty SQL in structured output")
            else:
                sql = _extract_sql(outcome.get("raw").content if outcome.get("raw") else None)
                if sql is None:
                    raise ValueError("model returned no structured output")
        except TRANSPORT_ERRORS:
            raise  # infrastructure error: never burns the semantic retry budget
        except Exception as err:  # structured-output failure == validation failure
            update = {
                "error_feedback": f"generator parse failure: {err}",
                "retries": state["retries"] + 1,
            }
        else:
            update = {"sql": sql, "error_feedback": None}
        update["trace"] = state["trace"] + [
            trace_entry("generator", started, digest(content), digest(update))
        ]
        return update

    return generator_node
