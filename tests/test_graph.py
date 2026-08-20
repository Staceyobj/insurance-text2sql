"""Graph tests with a scripted FakeLLM (SPEC §10: offline, no API key).

Prerequisite: the local compose database must be up and seeded
(`make up && make seed`) — the generator's schema context and the executor
run against the real tables; only the LLM is faked.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any

import pytest

from text2sql.config import Settings
from text2sql.graph import build_graph
from text2sql.state import new_state

DEFAULT_CONNINFO = "postgresql://t2s_readonly:t2s_readonly@localhost:5432/insurance"
CONNINFO = os.environ.get("DATABASE_URL", DEFAULT_CONNINFO)


class FakeLLM:
    """Scripted ChatOpenAI stand-in: pops canned outputs, records every call.

    Plain ``invoke`` returns an AIMessage-like namespace with ``content``;
    ``with_structured_output`` mimics langchain's ``include_raw=True`` shape:
    ``{"raw": <msg>, "parsed": <model or None>}``. A ``None`` script item
    simulates a model turn with no tool call and no content at all.
    """

    def __init__(self, script: list[str | None]):
        self.script = list(script)
        self.calls: list[list[Any]] = []

    def invoke(self, messages):
        self.calls.append(list(messages))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(content=item)

    def with_structured_output(self, schema, method=None, **kwargs):
        outer = self

        class _Structured:
            def invoke(self, messages):
                outer.calls.append(list(messages))
                raw = outer.script.pop(0)
                if isinstance(raw, Exception):  # simulate an API/transport error
                    raise raw
                content = None if raw is None else raw
                parsed = None
                if raw is not None:
                    try:
                        parsed = schema.model_validate(json.loads(raw))
                    except (json.JSONDecodeError, ValueError):
                        parsed = None
                # mirrors langchain's include_raw=True outcome shape
                return {"raw": SimpleNamespace(content=content), "parsed": parsed}

        return _Structured()


def run(script: list[str], question: str, **settings_overrides):
    fake = FakeLLM(script)
    settings = Settings(database_url=CONNINFO, **settings_overrides)
    state = build_graph(fake, settings).invoke(new_state(question))
    return state, fake


def nodes(state) -> list[str]:
    return [entry["node"] for entry in state["trace"]]


def test_sql_happy_path():
    state, fake = run(
        [
            json.dumps({"action": "sql"}),
            json.dumps({"sql": "SELECT count(*) FROM policies"}),
            "共 2000 张保单。",
        ],
        "一共有多少张保单？",
    )
    assert state["action"] == "sql"
    # normalized (R8) and limit-governed (R6) before execution
    assert state["sql"].startswith("SELECT") and "LIMIT 201" in state["sql"]
    assert state["rows"] == [{"count": 2000}]
    assert state["truncated"] is False
    assert state["retries"] == 0
    assert state["answer"] == "共 2000 张保单。"
    assert nodes(state) == ["router", "generator", "validator", "executor", "answerer"]
    assert len(fake.calls) == 3


def test_clarify_path():
    state, fake = run(
        [json.dumps({"action": "clarify", "clarify_question": "请问您指哪一年？"})],
        "去年理赔了多少？",
    )
    assert state["action"] == "clarify"
    assert state["answer"] == "请问您指哪一年？"
    assert state["sql"] is None and state["rows"] is None
    assert nodes(state) == ["router"]
    assert len(fake.calls) == 1


def test_refuse_path():
    state, _ = run(
        [json.dumps({"action": "refuse", "refuse_reason": "仅支持只读查询，不能执行删除操作。"})],
        "删除所有保单",
    )
    assert state["action"] == "refuse"
    assert "只读" in state["answer"] and "删除" in state["answer"]
    assert nodes(state) == ["router"]


def test_validator_failure_then_success():
    state, _ = run(
        [
            json.dumps({"action": "sql"}),
            json.dumps({"sql": "SELECT * FROM users"}),  # R2: whitelist miss
            json.dumps({"sql": "SELECT count(*) FROM claims"}),  # fixed
            "共 600 条理赔。",
        ],
        "一共有多少条理赔？",
    )
    assert state["retries"] == 1
    assert state["rows"] == [{"count": 600}]
    assert state["error_feedback"] is None  # cleared on success
    assert state["answer"] == "共 600 条理赔。"
    assert nodes(state) == [
        "router", "generator", "validator",
        "generator", "validator", "executor", "answerer",
    ]


def test_retries_exhausted_honest_failure():
    state, fake = run(
        [
            json.dumps({"action": "sql"}),
            json.dumps({"sql": "SELECT * FROM users"}),
            json.dumps({"sql": "SELECT * FROM orders"}),
        ],
        "随便查点东西",
    )
    assert state["retries"] == 2
    assert state["rows"] is None
    assert "未能完成" in state["answer"] and "R2" in state["answer"]
    assert len(fake.calls) == 3  # answerer LLM is never consulted
    assert nodes(state)[-1] == "honest_failure"


def test_executor_error_then_success():
    state, _ = run(
        [
            json.dumps({"action": "sql"}),
            json.dumps({"sql": "SELECT nonexistent_col FROM policies"}),  # executor error
            json.dumps({"sql": "SELECT count(*) FROM payments"}),  # fixed
            "共 8000 条缴费记录。",
        ],
        "一共有多少条缴费记录？",
    )
    assert state["retries"] == 1
    assert state["rows"] == [{"count": 8000}]
    assert state["error_feedback"] is None
    assert nodes(state) == [
        "router", "generator", "validator", "executor",
        "generator", "validator", "executor", "answerer",
    ]


def test_router_parse_failure_recovers():
    state, _ = run(
        [
            "这根本不是 JSON",
            json.dumps({"action": "sql"}),
            json.dumps({"sql": "SELECT count(*) FROM agents"}),
            "共 40 名代理人。",
        ],
        "一共有多少名代理人？",
    )
    assert state["retries"] == 1
    assert state["rows"] == [{"count": 40}]
    assert nodes(state) == [
        "router", "router", "generator", "validator", "executor", "answerer",
    ]


def test_truncated_flag_reaches_answerer():
    state, fake = run(
        [
            json.dumps({"action": "sql"}),
            json.dumps({"sql": "SELECT payment_id FROM payments ORDER BY payment_id"}),
            "（仅展示部分行）",
        ],
        "列出缴费记录",
        row_limit=5,
    )
    assert state["truncated"] is True
    assert len(state["rows"]) == 5
    answerer_prompt = fake.calls[-1][-1].content
    assert "【截断】" in answerer_prompt and "true" in answerer_prompt


def test_generator_none_output_treated_as_parse_failure():
    # Real-world case seen at M3 DoD: the model sometimes answers without
    # calling the tool; structured output is then None, which must be a
    # clean, retryable parse failure — never an AttributeError.
    state, _ = run(
        [
            json.dumps({"action": "sql"}),
            None,
            json.dumps({"sql": "SELECT count(*) FROM customers"}),
            "共 500 名客户。",
        ],
        "一共有多少名客户？",
    )
    assert state["retries"] == 1  # the None turn consumed the shared budget
    assert state["rows"] == [{"count": 500}]


def test_generator_plain_text_fallback():
    # Real-world case seen at M3 DoD: for English questions glm-4.7 sometimes
    # answers with the bare SQL as plain text instead of a tool call. The
    # generator must salvage it (still validated by R1–R8) without burning
    # the retry budget.
    state, _ = run(
        [
            json.dumps({"action": "sql"}),
            "SELECT count(*) FROM agents",  # plain text, no tool call
            "共 40 名代理人。",
        ],
        "How many agents are there?",
    )
    assert state["retries"] == 0  # salvaged: no budget consumed
    assert state["rows"] == [{"count": 40}]
    assert state["answer"] == "共 40 名代理人。"


def test_transport_error_not_treated_as_parse_failure():
    # SPEC §3's retry budget is for semantic failures only; a transport-level
    # API error must escape the node untouched instead of becoming feedback
    # (misclassification burned the budget during a rate-limited eval run).
    from openai import APIConnectionError

    from text2sql.nodes.generator import make_generator_node

    node = make_generator_node(
        FakeLLM([APIConnectionError(message="connection boom", request=None)]), ""
    )
    with pytest.raises(APIConnectionError):
        node(new_state("任意问题"))
