"""Executor tests (SPEC §5.2 executor row, §5.4 R9 runtime evidence).

Integration tests against the local compose PostgreSQL (SPEC §10): run
`make up && make seed` first. No network beyond localhost, no API key.

R9 evidence lives here: the runtime connection itself must be read-only
(per-table GRANT SELECT) with a 5s role-level statement_timeout (§4.3).
"""

import os

from text2sql.nodes.executor import execute_sql

DEFAULT_CONNINFO = "postgresql://t2s_readonly:t2s_readonly@localhost:5432/insurance"
CONNINFO = os.environ.get("DATABASE_URL", DEFAULT_CONNINFO)


def run(sql: str, *, row_limit: int = 200):
    return execute_sql(CONNINFO, sql, row_limit=row_limit)


def test_select_aggregate_returns_dict_rows():
    result = run(
        "SELECT category, count(*) AS n FROM products GROUP BY category ORDER BY category"
    )
    assert result.error is None
    assert result.truncated is False
    assert result.rows, "expected non-empty result"
    first = result.rows[0]
    assert set(first) == {"category", "n"}
    assert isinstance(first["n"], int)


def test_truncated_when_over_row_limit():
    result = run("SELECT payment_id FROM payments ORDER BY payment_id", row_limit=10)
    assert result.error is None
    assert result.truncated is True
    assert len(result.rows) == 10


def test_exactly_at_row_limit_not_truncated():
    # The classic fetchmany off-by-one boundary: exactly cap rows -> not truncated.
    result = run("SELECT payment_id FROM payments LIMIT 200", row_limit=200)
    assert result.error is None
    assert len(result.rows) == 200
    assert result.truncated is False


def test_r9_write_rejected_on_readonly_role():
    result = run(
        "INSERT INTO products (product_code, product_name, category, term_years,"
        " launched_date, is_active)"
        " VALUES ('X', 'X', 'life', 1, '2024-01-01', true)"
    )
    assert result.rows == []
    assert result.error is not None
    assert "read-only" in result.error or "permission denied" in result.error


def test_r9_statement_timeout_cancels_slow_query():
    result = run("SELECT pg_sleep(6)")  # role-level statement_timeout = 5s
    assert result.error is not None
    lowered = result.error.lower()
    assert "statement timeout" in lowered or "canceling" in lowered


def test_missing_table_returns_error_not_raise():
    result = run("SELECT * FROM no_such_table")
    assert result.rows == []
    assert result.error is not None
    assert "no_such_table" in result.error  # actionable: names the object


def test_unreachable_database_error_mentions_make_up():
    result = execute_sql(
        "postgresql://t2s_readonly:t2s_readonly@localhost:59999/insurance",
        "SELECT 1",
        row_limit=10,
    )
    assert result.error is not None
    assert "make up" in result.error  # actionable hint, never a bare traceback
