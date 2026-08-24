"""API tests with TestClient + FakeLLM (SPEC §7.2; offline per SPEC §10).

Prerequisite: local compose database up and seeded (graph construction reads
the schema context once) — same premise as the graph tests.
"""

import json
import os

from fastapi.testclient import TestClient
from test_graph import FakeLLM  # same directory

from text2sql.api import create_app
from text2sql.config import Settings

CONNINFO = os.environ.get(
    "DATABASE_URL", "postgresql://t2s_readonly:t2s_readonly@localhost:5432/insurance"
)


def make_client(script: list) -> TestClient:
    app = create_app(llm=FakeLLM(script), settings=Settings(database_url=CONNINFO))
    return TestClient(app)


def test_healthz():
    client = make_client([])  # no LLM script: lazy graph is never built
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_clarify_path_shape():
    client = make_client(
        [json.dumps({"action": "clarify", "clarify_question": "请问您指哪一年？"})]
    )
    resp = client.post("/v1/query", json={"question": "去年理赔了多少？"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "clarify"
    assert body["answer"] == "请问您指哪一年？"
    assert body["sql"] is None and body["rows"] is None  # rows=null for clarify
    assert body["truncated"] is False and body["error"] is None
    assert body["trace"] == []  # key exists, empty without debug


def test_debug_true_returns_trace():
    client = make_client(
        [json.dumps({"action": "clarify", "clarify_question": "哪一年？"})]
    )
    resp = client.post("/v1/query", json={"question": "去年理赔了多少？", "debug": True})
    body = resp.json()
    assert body["trace"] and body["trace"][0]["node"] == "router"


def test_request_validation():
    client = make_client([])
    assert client.post("/v1/query", json={}).status_code == 422  # missing field
    assert client.post("/v1/query", json={"question": ""}).status_code == 422  # min_length


def test_sql_happy_path_serialization():
    client = make_client(
        [
            json.dumps({"action": "sql"}),
            json.dumps({"sql": "SELECT count(*) AS n, sum(sum_assured) AS total FROM policies"}),
            "全部保单统计完成。",
        ]
    )
    resp = client.post("/v1/query", json={"question": "保单总额是多少？"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "sql"
    assert "LIMIT 201" in body["sql"]  # normalized + governed SQL is what ran
    assert body["error"] is None and body["truncated"] is False
    row = body["rows"][0]
    assert row["n"] == 2000
    # pydantic v2 response_model serializes Decimal inside plain dicts as
    # lossless strings ("1159128031.00") — pinned here as the API contract.
    assert isinstance(row["total"], str)
    assert float(row["total"]) > 0


def test_honest_failure_stays_200_with_error():
    client = make_client(
        [
            json.dumps({"action": "sql"}),
            json.dumps({"sql": "SELECT * FROM users"}),
            json.dumps({"sql": "SELECT * FROM orders"}),
        ]
    )
    resp = client.post("/v1/query", json={"question": "随便查点东西"})
    assert resp.status_code == 200  # §7.2: honest failure keeps HTTP 200
    body = resp.json()
    assert body["rows"] is None
    assert body["error"] is not None and "R2" in body["error"]
    assert "未能完成" in body["answer"]


# --- static serving (SPEC-FRONTEND §3/§8): zero Node involvement in pytest ---


def test_no_static_dir_root_is_404(tmp_path):
    """Explicit absent dist → no mount, `/` stays 404.

    The absent path is passed explicitly so the test is deterministic even on
    a machine where `make frontend-build` has populated the real frontend/dist.
    """
    app = create_app(
        llm=FakeLLM([]),
        settings=Settings(database_url=CONNINFO),
        static_dir=tmp_path / "absent",
    )
    assert TestClient(app).get("/").status_code == 404


def test_stub_dist_serves_index_and_api_routes_win(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><html>stub frontend</html>")
    app = create_app(
        llm=FakeLLM([json.dumps({"action": "clarify", "clarify_question": "哪一年？"})]),
        settings=Settings(database_url=CONNINFO),
        static_dir=dist,
    )
    client = TestClient(app)
    root = client.get("/")
    assert root.status_code == 200
    assert "stub frontend" in root.text
    # Routes registered before the mount keep winning (SPEC-FRONTEND §3).
    assert client.get("/healthz").json() == {"status": "ok"}
    body = client.post("/v1/query", json={"question": "去年理赔了多少？"}).json()
    assert body["action"] == "clarify"
