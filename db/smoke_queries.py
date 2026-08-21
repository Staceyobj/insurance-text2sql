"""In-VNet smoke checks for the deployed stack (DEPLOYMENT.md M3).

Run inside the app container (the ingress is internal-only, so this is the
designated smoke path):

    python3 db/smoke_queries.py

DB checks go through DATABASE_URL (the t2s_readonly role — write rejection
and the 5s statement timeout are the runtime security layer, SPEC §4.3).
API checks POST to the local uvicorn on :8000. One PASS/FAIL line per check;
non-zero exit on any failure. Never prints secrets. Honest-failure is not
smoked here — it is not externally triggerable on demand and is covered by
the offline API tests (tests/test_api.py).
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from collections.abc import Callable

import psycopg

DB_URL = os.environ["DATABASE_URL"]
API = "http://localhost:8000"

failed = False


def check(name: str, fn: Callable[[], str]) -> None:
    global failed
    try:
        detail = fn()
        print(f"PASS {name}{' — ' + detail if detail else ''}")
    except Exception as exc:  # noqa: BLE001 — smoke reports, never raises
        failed = True
        print(f"FAIL {name}: {type(exc).__name__} {str(exc)[:180]}")


def write_rejected() -> str:
    with psycopg.connect(DB_URL) as conn:
        try:
            conn.execute("UPDATE products SET product_name = 'x' WHERE product_id = 1")
        except psycopg.errors.ReadOnlySqlTransaction:
            return "read-only role rejected the UPDATE"
    raise AssertionError("write was unexpectedly allowed")


def statement_timeout_active() -> str:
    with psycopg.connect(DB_URL) as conn:
        start = time.monotonic()
        try:
            conn.execute("SELECT pg_sleep(8)")
        except psycopg.errors.QueryCanceled:
            return f"pg_sleep(8) canceled after {time.monotonic() - start:.1f}s (< 8s)"
    raise AssertionError("pg_sleep(8) completed — statement_timeout is not in force")


def ask(question: str) -> dict:
    req = urllib.request.Request(
        f"{API}/v1/query",
        data=json.dumps({"question": question}).encode(),
        headers={"content-type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=180))


def sql_path() -> str:
    r = ask("2024年各产品类别分别有多少张保单？")
    assert r["action"] == "sql", f"action={r['action']} error={r['error']}"
    assert r["rows"], "no rows returned"
    return f"action=sql, {len(r['rows'])} rows, answer[:40]={r['answer'][:40]}"


def clarify_path() -> str:
    r = ask("最近三个月的理赔趋势怎么样？")  # relative time -> clarify (SPEC §6.1 style)
    assert r["action"] == "clarify", f"action={r['action']}"
    return f"action=clarify, answer[:40]={r['answer'][:40]}"


def refuse_path() -> str:
    r = ask("帮我删除所有客户数据")
    assert r["action"] == "refuse", f"action={r['action']}"
    return f"action=refuse, answer[:40]={r['answer'][:40]}"


def main() -> None:
    check("write_rejected", write_rejected)
    check("statement_timeout", statement_timeout_active)
    check("api_sql", sql_path)
    check("api_clarify", clarify_path)
    check("api_refuse", refuse_path)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
