"""Validator rule tests (SPEC §5.4 R1–R9), written BEFORE the implementation.

R1–R8 are exercised here against the pure function `validate_sql`.
R9 (defense-in-depth: runtime read-only role + 5s statement_timeout) is not a
validator behavior — it is proven in test_executor.py.

Rule -> test map (M2 DoD evidence):
  R1 test_r1_*  R2 test_r2_*  R3 test_r3_*  R4 test_r4_*
  R5 test_r5_*  R6 test_r6_*  R7 test_r7_*  R8 test_r8_*
"""

import re

import pytest

from text2sql.validator import validate_sql

ROW_LIMIT = 200
CAP = ROW_LIMIT + 1  # 201


def accept(sql: str, **kwargs):
    result = validate_sql(sql, **kwargs)
    assert result.ok, f"expected acceptance, got error: {result.error}"
    return result


def reject(sql: str, rule: str) -> None:
    result = validate_sql(sql, row_limit=ROW_LIMIT)
    assert not result.ok, f"expected rejection ({rule}), got sql: {result.sql}"
    assert result.sql is None
    assert isinstance(result.error, str) and result.error.strip()
    assert result.error.startswith(f"{rule}:"), result.error
    assert re.fullmatch(r"R\d: .+", result.error), result.error


# ---------------------------------------------------------------- R1 statement
def test_r1_accepts_plain_select():
    accept("SELECT count(*) FROM policies")


def test_r1_accepts_cte_select():
    accept("WITH c AS (SELECT customer_id FROM policies) SELECT count(*) FROM c")


def test_r1_accepts_union_as_documented_extension():
    accept("SELECT count(*) FROM claims UNION ALL SELECT count(*) FROM payments")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM policies WHERE policy_id = 1",
        "UPDATE policies SET status = 'lapsed'",
        "INSERT INTO policies (policy_no) VALUES ('X')",
        "DROP TABLE policies",
        "CREATE TABLE tmp (id integer)",
        "TRUNCATE policies",
        "GRANT SELECT ON policies TO PUBLIC",
        "SET statement_timeout = 0",
        "SHOW ALL",
        "EXPLAIN SELECT * FROM policies",
        "COPY policies FROM '/tmp/dump.csv'",
        "SELECT 1; SELECT 2",
        "",
    ],
    ids=[
        "delete", "update", "insert", "drop", "create", "truncate", "grant",
        "set", "show", "explain", "copy", "two-statements", "empty",
    ],
)
def test_r1_rejects_non_select(sql):
    reject(sql, "R1")


# ---------------------------------------------------------------- R2 whitelist
def test_r2_accepts_join_across_whitelist():
    accept(
        "SELECT c.claim_id, p.policy_no FROM claims c "
        "JOIN policies p ON c.policy_id = p.policy_id"
    )


def test_r2_accepts_table_in_subquery():
    accept(
        "SELECT * FROM policies WHERE customer_id IN "
        "(SELECT customer_id FROM customers)"
    )


def test_r2_accepts_cte_alias_not_treated_as_table():
    accept("WITH recent AS (SELECT * FROM claims) SELECT count(*) FROM recent")


def test_r2_accepts_unquoted_mixed_case_table():
    # Unquoted identifiers fold to lowercase in PostgreSQL -> must match.
    accept("SELECT count(*) FROM Policies")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM users",
        "SELECT * FROM policies WHERE customer_id IN (SELECT id FROM users)",
        "WITH x AS (SELECT * FROM orders) SELECT count(*) FROM x",
        "SELECT * FROM policies p JOIN users u ON p.customer_id = u.customer_id",
        'SELECT * FROM "Products"',
    ],
    ids=["top-level", "subquery", "cte-body", "join-right-side", "quoted-identifier"],
)
def test_r2_rejects_tables_outside_whitelist(sql):
    reject(sql, "R2")


# ---------------------------------------------------------------- R3 system
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM information_schema.columns",
        "SELECT * FROM pg_tables",
    ],
    ids=["pg_catalog", "information_schema", "bare-pg-table"],
)
def test_r3_rejects_system_objects(sql):
    reject(sql, "R3")


# ---------------------------------------------------------------- R4 functions
BLACKLISTED_EXPRESSIONS = [  # SPEC §5.4 R4 — the full named list
    "pg_sleep(10)",
    "pg_read_file('/etc/passwd')",
    "pg_read_binary_file('/etc/passwd')",
    "pg_ls_dir('/etc')",
    "pg_stat_file('/etc/passwd')",
    "lo_import('/tmp/x')",
    "lo_export(1234, '/tmp/x')",
    "dblink('host=127.0.0.1', 'SELECT 1')",
    "dblink_exec('host=127.0.0.1', 'SELECT 1')",
    "pg_terminate_backend(123)",
    "pg_cancel_backend(123)",
    "set_config('search_path', 'public', false)",
    "pg_reload_conf()",
]


@pytest.mark.parametrize(
    "expr",
    BLACKLISTED_EXPRESSIONS,
    ids=[e.split("(")[0] for e in BLACKLISTED_EXPRESSIONS],
)
def test_r4_rejects_blacklisted_functions(expr):
    reject(f"SELECT {expr}", "R4")


def test_r4_rejects_schema_qualified_blacklisted_function():
    reject("SELECT pg_catalog.pg_sleep(10)", "R4")


def test_r4_accepts_ordinary_functions():
    accept("SELECT date_trunc('year', filed_date), count(*) FROM claims GROUP BY 1")


# ---------------------------------------------------------------- R5 into/locks
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * INTO report FROM policies",
        "SELECT * FROM policies FOR UPDATE",
        "SELECT policy_no FROM policies WHERE policy_id = 1 FOR SHARE",
    ],
    ids=["select-into", "for-update", "for-share"],
)
def test_r5_rejects_into_and_locks(sql):
    reject(sql, "R5")


# ---------------------------------------------------------------- R6 limit
def test_r6_injects_limit_when_missing():
    result = accept("SELECT * FROM policies", row_limit=ROW_LIMIT)
    assert f"LIMIT {CAP}" in result.sql


def test_r6_clamps_oversized_limit():
    result = accept("SELECT * FROM payments LIMIT 1000000", row_limit=ROW_LIMIT)
    assert f"LIMIT {CAP}" in result.sql


def test_r6_preserves_smaller_explicit_limit():
    result = accept("SELECT * FROM payments LIMIT 10", row_limit=ROW_LIMIT)
    assert "LIMIT 10" in result.sql


def test_r6_preserves_limit_equal_to_row_limit():
    result = accept("SELECT * FROM payments LIMIT 200", row_limit=ROW_LIMIT)
    assert "LIMIT 200" in result.sql


def test_r6_leaves_inner_limit_alone():
    result = accept("SELECT * FROM (SELECT * FROM payments LIMIT 5) t", row_limit=10)
    assert "LIMIT 11" in result.sql  # injected on the outer query only
    assert "LIMIT 5" in result.sql  # inner limit untouched


# ---------------------------------------------------------------- R7 parse
@pytest.mark.parametrize(
    "sql",
    ["SELEC * FROM policies", "SELECT * FRM policies", "SELECT ("],
    ids=["keyword-typo", "from-typo", "unbalanced-paren"],
)
def test_r7_rejects_parse_errors(sql):
    reject(sql, "R7")


# ---------------------------------------------------------------- R8 normalize
def test_r8_returns_normalized_sql():
    raw = (
        "select category ,count(*) as cnt from PRODUCTS "
        "where is_active=TRUE group by category"
    )
    result = accept(raw, row_limit=ROW_LIMIT)
    assert result.sql != raw
    # The normalized SQL must itself re-validate to the identical fixed point.
    again = validate_sql(result.sql, row_limit=ROW_LIMIT)
    assert again.ok, again.error
    assert again.sql == result.sql


def test_r8_deterministic():
    raw = "SELECT category, count(*) FROM products GROUP BY category"
    first = validate_sql(raw, row_limit=ROW_LIMIT)
    second = validate_sql(raw, row_limit=ROW_LIMIT)
    assert first == second
