"""Read-only SQL execution (SPEC §5.2 executor, §5.4 R9 runtime layer)."""

from __future__ import annotations

from typing import Any, NamedTuple

import psycopg
from psycopg.errors import OperationalError


class ExecutionResult(NamedTuple):
    """Execution outcome: dict rows + truncation flag, or a concise error."""

    rows: list[dict[str, Any]]
    truncated: bool
    error: str | None


def _first_line(err: Exception) -> str:
    text = str(err).strip()
    return (text.splitlines() or [""])[0] or type(err).__name__


def execute_sql(conninfo: str, sql: str, *, row_limit: int) -> ExecutionResult:
    """Execute one normalized SELECT on the read-only connection; never raises.

    - Fetches up to ``row_limit + 1`` rows: a full fetch sets ``truncated=True``
      and the extra row is discarded (paired with the validator's R6 clamping).
    - Query-phase database errors come back as first-line ``error`` strings —
      M3 routes them into ``error_feedback``. Connection failures additionally
      get an actionable "run make up" hint.
    - Read-only-ness and the 5s timeout are deliberately NOT re-implemented
      here: they live in the role itself (SPEC §4.3) — R9 defense in depth.
    - ``autocommit`` stays off: if anything but a SELECT ever slipped through,
      nothing would be committed by this connection.
    """
    try:
        conn = psycopg.connect(conninfo)
    except OperationalError as err:
        return ExecutionResult(
            rows=[],
            truncated=False,
            error=f"cannot connect to database: {_first_line(err)}"
            " (is the stack up? run make up)",
        )

    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            fetched = cur.fetchmany(row_limit + 1)
            columns = [desc.name for desc in (cur.description or [])]
    except psycopg.Error as err:
        return ExecutionResult(rows=[], truncated=False, error=_first_line(err))
    finally:
        conn.close()

    truncated = len(fetched) > row_limit
    rows = [dict(zip(columns, row, strict=True)) for row in fetched[:row_limit]]
    return ExecutionResult(rows=rows, truncated=truncated, error=None)
