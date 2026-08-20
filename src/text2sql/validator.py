"""AST-level SQL validation and normalization (SPEC §5.4, rules R1–R8).

Pure function: no I/O, no mutable globals, deterministic — the same input
always yields the same ``ValidationResult``. On success ``sql`` carries the
sqlglot-rendered normalized statement (R8) which the executor is allowed to
run; on failure ``error`` carries a concise ``"R<n>: ..."`` message ready to
be fed back through ``error_feedback``.

Interpretation notes:
- Top-level set operations (UNION / EXCEPT / INTERSECT) are accepted as a
  documented extension of R1's "top level must be a SELECT": every arm still
  passes R2–R5, so the security posture is equivalent, and questions shaped
  like "how many X and how many Y" legitimately need a UNION.
- Quoted identifiers match the whitelist case-sensitively ("Products" does
  NOT match products); unquoted identifiers fold to lowercase as in PostgreSQL.
- R9 (defense-in-depth) lives outside this module: the runtime connection is
  the read-only role with a 5s statement_timeout (SPEC §4.3), proven in
  tests/test_executor.py.
"""

from __future__ import annotations

from typing import NamedTuple

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


class ValidationResult(NamedTuple):
    """Validation outcome: normalized SQL on success, actionable error otherwise."""

    ok: bool
    sql: str | None
    error: str | None


TABLES = frozenset(
    {"products", "agents", "customers", "policies", "claims", "payments"}
)
SYSTEM_SCHEMAS = frozenset({"pg_catalog", "information_schema"})

# R4 blacklist: the functions SPEC §5.4 names, matched exactly ...
BLACKLISTED_FUNCTIONS = frozenset(
    {
        "pg_sleep",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "lo_import",
        "lo_export",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "set_config",
        "pg_reload_conf",
    }
)
# ... plus prefix families; over-blocking is the stated policy (R4).
BLACKLISTED_PREFIXES = ("dblink", "lo_", "pg_advisory")

TOP_LEVEL_TYPES = (exp.Select, exp.Union, exp.Except, exp.Intersect)


def _fail(rule: str, message: str) -> ValidationResult:
    return ValidationResult(ok=False, sql=None, error=f"{rule}: {message}")


def _is_quoted(table: exp.Table) -> bool:
    ident = table.this
    return isinstance(ident, exp.Identifier) and bool(ident.quoted)


def _check_tables(stmt: exp.Expression) -> ValidationResult | None:
    """R2 + R3 over every real table reference (CTE aliases excluded)."""
    cte_aliases = {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE)}
    for table in stmt.find_all(exp.Table):
        name = table.name
        folded = name.lower()
        schema = (table.db or "").lower()
        catalog = (table.catalog or "").lower()
        if schema in SYSTEM_SCHEMAS or catalog in SYSTEM_SCHEMAS or folded.startswith("pg_"):
            return _fail("R3", f"system object access is not allowed: {name!r}")
        if folded in cte_aliases:
            continue
        target = name if _is_quoted(table) else folded
        if target not in TABLES:
            return _fail("R2", f"table {name!r} is not in the whitelist")
    return None


def _check_functions(stmt: exp.Expression) -> ValidationResult | None:
    """R4 over every function call, schema-qualified or not."""
    for func in stmt.find_all(exp.Func):
        head = func.sql(dialect="postgres").split("(", 1)[0]
        name = head.strip().strip('"').rsplit(".", 1)[-1].strip('"').lower()
        if name in BLACKLISTED_FUNCTIONS or name.startswith(BLACKLISTED_PREFIXES):
            return _fail("R4", f"function {name!r} is blacklisted")
    return None


def _govern_limit(stmt: exp.Expression, row_limit: int) -> exp.Expression:
    """R6: missing or oversized LIMIT -> row_limit + 1; smaller limits kept.

    Applies to the outermost query only; inner subquery limits are untouched.
    """
    cap = row_limit + 1
    limit = stmt.args.get("limit")
    if limit is None:
        return stmt.limit(cap)
    # sqlglot >= 30: the count lives in Limit.expression ("this" is reserved)
    try:
        value = int(limit.expression.name)
    except (AttributeError, ValueError):
        value = None
    if value is None:
        return stmt.limit(cap)  # malformed/non-literal limit: replace wholesale
    if value > row_limit:
        limit.expression.replace(exp.Literal.number(cap))  # keep OFFSET intact
    return stmt


def validate_sql(sql: str, *, row_limit: int = 200) -> ValidationResult:
    """Validate one SQL string against SPEC §5.4 and return the normalized form."""
    if not sql or not sql.strip():
        return _fail("R1", "empty statement")

    try:  # R7: strict parse failure is a validation failure
        statements = sqlglot.parse(sql, read="postgres")
    except ParseError as err:
        first_line = str(err).splitlines()[0]
        return _fail("R7", f"parse error: {first_line}")

    # R1: exactly one statement whose top level is a SELECT (CTE form included)
    if len(statements) != 1:
        return _fail("R1", f"expected a single statement, got {len(statements)}")
    stmt = statements[0]
    if not isinstance(stmt, TOP_LEVEL_TYPES):
        return _fail("R1", f"top level must be a SELECT, got {type(stmt).__name__}")

    # R5 before R2: SELECT ... INTO introduces a target table that must not be
    # misreported as a whitelist miss.
    if stmt.find(exp.Into) is not None:
        return _fail("R5", "SELECT ... INTO is not allowed")
    if stmt.find(exp.Lock) is not None:
        return _fail("R5", "locking clauses (FOR UPDATE / FOR SHARE) are not allowed")

    failure = _check_tables(stmt) or _check_functions(stmt)
    if failure is not None:
        return failure

    stmt = _govern_limit(stmt, row_limit)

    # R8: the executor only ever sees this normalized rendering
    return ValidationResult(ok=True, sql=stmt.sql(dialect="postgres"), error=None)
