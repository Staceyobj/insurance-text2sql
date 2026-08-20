"""FastAPI service (SPEC §7.2).

One shared compiled graph is built lazily on the first query (double-checked
under a lock): healthz stays reachable without an API key, and langgraph
compiled graphs plus the openai client are safe for concurrent invokes.
"""

from __future__ import annotations

import threading

from fastapi import FastAPI
from pydantic import BaseModel, Field

from text2sql.config import Settings, get_settings
from text2sql.graph import build_graph
from text2sql.llm import build_llm
from text2sql.state import QueryState, new_state


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)  # bare str would accept ""
    debug: bool = False


class QueryResponse(BaseModel):
    action: str | None
    answer: str | None
    sql: str | None
    rows: list[dict] | None  # null for clarify/refuse (faithful to state);
    # note: Decimal values inside rows serialize as lossless strings
    truncated: bool
    error: str | None  # honest failure surfaces here; HTTP stays 200
    trace: list[dict]  # populated only when debug=true


def create_app(llm=None, settings: Settings | None = None) -> FastAPI:
    """App factory; ``llm`` is injectable for tests (FakeLLM)."""
    app = FastAPI(title="insurance-text2sql", version="0.1.0")
    app_settings = settings or get_settings()
    lock = threading.Lock()
    holder: dict = {}

    def _graph():
        if "graph" not in holder:
            with lock:
                if "graph" not in holder:
                    runtime_llm = llm or build_llm(app_settings)
                    holder["graph"] = build_graph(runtime_llm, app_settings)
        return holder["graph"]

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.post("/v1/query", response_model=QueryResponse)
    def query(request: QueryRequest) -> dict:
        state: QueryState = _graph().invoke(new_state(request.question))
        return {
            "action": state.get("action"),
            "answer": state.get("answer"),
            "sql": state.get("sql"),
            "rows": state.get("rows"),
            "truncated": state.get("truncated", False),
            "error": state.get("error_feedback"),
            "trace": state.get("trace", []) if request.debug else [],
        }

    return app


app = create_app()  # uvicorn text2sql.api:app
