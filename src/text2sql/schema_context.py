"""Schema context generated on the fly from DB COMMENTs (SPEC §4.1, hard rule 6).

The schema block fed to the generator prompt is derived from the bilingual
COMMENTs seeded by db/01_schema.sql — never hand-written here. Enum values
are already spelled out inside those COMMENTs.
"""

from __future__ import annotations

import psycopg

_TABLES_QUERY = """
SELECT c.relname AS table_name, obj_description(c.oid) AS table_comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY c.relname
"""

_COLUMNS_QUERY = """
SELECT c.relname AS table_name, a.attname AS column_name,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       col_description(a.attrelid, a.attnum) AS column_comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
WHERE n.nspname = 'public' AND c.relkind = 'r'
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""


def build_schema_context(conninfo: str) -> str:
    """Render every table/column COMMENT as the prompt's schema block."""
    with psycopg.connect(conninfo) as conn:
        tables = conn.execute(_TABLES_QUERY).fetchall()
        columns = conn.execute(_COLUMNS_QUERY).fetchall()

    by_table: dict[str, list[tuple[str, str, str]]] = {}
    for _table, name, data_type, comment in columns:
        by_table.setdefault(_table, []).append((name, data_type, comment or ""))

    blocks: list[str] = []
    for table_name, table_comment in tables:
        lines = [f"{table_name} — {table_comment or ''}"]
        for name, data_type, comment in by_table.get(table_name, []):
            lines.append(f"  {name} {data_type}: {comment}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
