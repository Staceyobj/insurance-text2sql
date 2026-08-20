"""Golden-set evaluation runner (SPEC §6).

Runs every golden case against the real LLM and the real database, judges
sql cases by result-set equivalence and clarify/refuse cases by action,
writes evals/report.md, and exits non-zero when the §6.3 gates are not met.

RED LINE (hard rule 2): expected_sql in the golden set is never modified to
make an evaluation pass — fixes go into prompts or implementation only.
"""

from __future__ import annotations

import argparse
import re
import sys
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import yaml

from text2sql.config import Settings, get_settings
from text2sql.graph import build_graph
from text2sql.llm import build_llm
from text2sql.nodes.executor import execute_sql
from text2sql.schema_context import build_schema_context
from text2sql.state import new_state
from text2sql.validator import validate_sql

EVALS_DIR = Path(__file__).resolve().parent
GOLDEN_PATH = EVALS_DIR / "golden" / "cases.yaml"
REPORT_PATH = EVALS_DIR / "report.md"

# SPEC §6.3 gates: category -> (minimum pass, total); refuse is exact and hard.
GATES = {"refuse": (6, 6), "sql": (27, 30), "clarify": (5, 6), "total": (38, 42)}
TOLERANCE = 1e-6
RELATIVE_WORDS = re.compile(r"去年|最近|上个月|本周|今年|上周|昨天|至今")
TIME_FUNCTIONS = re.compile(r"now\(\)|current_date|current_timestamp|localtimestamp", re.I)


# ---------------------------------------------------------------- comparison
def _canonical_cell(value) -> tuple:
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, (int, float, Decimal)):
        return ("num", float(value))
    if isinstance(value, (date, datetime)):
        return ("date", value.isoformat())
    return ("str", str(value))


def _cells_close(a: tuple, b: tuple, tolerance: float) -> bool:
    kind_a, kind_b = a[0], b[0]
    if kind_a != kind_b:
        return False
    if kind_a == "null":
        return True
    if kind_a == "num":
        return abs(a[1] - b[1]) <= tolerance
    return a[1] == b[1]


def _rows_close(a: Sequence[tuple], b: Sequence[tuple], tolerance: float) -> bool:
    return len(a) == len(b) and all(
        _cells_close(ca, cb, tolerance) for ca, cb in zip(a, b, strict=True)
    )


def rows_to_tuples(rows: list[dict]) -> list[tuple]:
    """Executor dict rows -> positional value tuples (column names ignored)."""
    return [tuple(row.values()) for row in rows]


def compare_result_sets(
    generated: list[tuple],
    expected: list[tuple],
    *,
    ordered: bool = False,
    tolerance: float = TOLERANCE,
) -> tuple[bool, str]:
    """SPEC §6.2: column counts equal, names ignored; order-insensitive unless
    ordered; numeric tolerance; NULL equals only NULL; multiset semantics."""
    if len(generated) != len(expected):
        return False, f"row count mismatch: generated {len(generated)}, expected {len(expected)}"
    if generated and expected and len(generated[0]) != len(expected[0]):
        return False, (
            f"column count mismatch: generated {len(generated[0])}, expected {len(expected[0])}"
        )
    gen = [tuple(_canonical_cell(v) for v in row) for row in generated]
    exp = [tuple(_canonical_cell(v) for v in row) for row in expected]

    if ordered:
        for i, (g_row, e_row) in enumerate(zip(gen, exp, strict=True)):
            if not _rows_close(g_row, e_row, tolerance):
                got, want = generated[i], expected[i]
                return False, f"row {i} differs (ordered): got {got}, expected {want}"
        return True, ""

    # order-insensitive: greedy matching with consumption (duplicate-safe)
    remaining = list(gen)
    for e_row in exp:
        match = next((g for g in remaining if _rows_close(g, e_row, tolerance)), None)
        if match is None:
            return False, f"expected row not found in generated: {expected[exp.index(e_row)]}"
        remaining.remove(match)
    return True, ""


# ---------------------------------------------------------------- golden load
@dataclass
class Case:
    id: str
    category: str
    question: str
    expected_sql: str | None
    ordered: bool
    expected_rows: list[tuple] | None = None


def load_golden(settings: Settings) -> list[Case]:
    """Load and hard-validate the golden set; execute expected SQL once."""
    raw = yaml.safe_load(GOLDEN_PATH.read_text(encoding="utf-8"))
    if len(raw) != 42:
        sys.exit(f"golden set invalid: {len(raw)} cases, expected 42")
    counts: dict[str, int] = {}
    ids: set[str] = set()
    cases: list[Case] = []
    for item in raw:
        cid, cat = item["id"], item["category"]
        if cid in ids:
            sys.exit(f"golden set invalid: duplicate id {cid}")
        ids.add(cid)
        counts[cat] = counts.get(cat, 0) + 1
        if cat == "sql" and RELATIVE_WORDS.search(item["question"]):
            sys.exit(f"golden set invalid: {cid} question uses relative time")
        if cat == "sql" and TIME_FUNCTIONS.search(item.get("expected_sql") or ""):
            sys.exit(f"golden set invalid: {cid} expected_sql contains a time function")
        if (cat == "sql") != ("expected_sql" in item):
            sys.exit(f"golden set invalid: {cid} expected_sql/category mismatch")
        cases.append(
            Case(
                id=cid,
                category=cat,
                question=item["question"],
                expected_sql=item.get("expected_sql"),
                ordered=bool(item.get("ordered", False)),
            )
        )
    if counts != {"sql": 30, "clarify": 6, "refuse": 6}:
        sys.exit(f"golden set invalid: category counts {counts} != 30/6/6")

    for case in cases:
        if case.category != "sql":
            continue
        result = validate_sql(case.expected_sql, row_limit=settings.row_limit)
        if not result.ok:
            sys.exit(f"golden set invalid: {case.id} expected_sql fails validation: {result.error}")
        executed = execute_sql(
            settings.database_url, result.sql, row_limit=settings.row_limit
        )
        if executed.error is not None:
            sys.exit(f"golden set invalid: {case.id} expected_sql fails to run: {executed.error}")
        if executed.truncated:
            sys.exit(f"golden set invalid: {case.id} expected result unbounded (>ROW_LIMIT)")
        if not executed.rows:
            sys.exit(f"golden set invalid: {case.id} expected result is empty (weak case)")
        case.expected_rows = rows_to_tuples(executed.rows)
    return sorted(cases, key=lambda c: c.id)


# ---------------------------------------------------------------- execution
@dataclass
class CaseResult:
    case: Case
    passed: bool
    detail: str
    action: str | None
    generated_sql: str | None
    duration_ms: float


_local = threading.local()


def _graph_for_thread(settings: Settings, schema_context: str):
    graph = getattr(_local, "graph", None)
    if graph is None:  # per-thread LLM + graph; schema context is shared
        graph = build_graph(build_llm(settings), settings, schema_context=schema_context)
        _local.graph = graph
    return graph


def run_case(case: Case, settings: Settings, schema_context: str) -> CaseResult:
    started = time.perf_counter()
    try:
        state = _graph_for_thread(settings, schema_context).invoke(new_state(case.question))
    except Exception as err:  # e.g. API rate limit raised outside guarded nodes
        return CaseResult(
            case=case,
            passed=False,
            detail=f"exception: {err}",
            action=None,
            generated_sql=None,
            duration_ms=(time.perf_counter() - started) * 1000,
        )
    duration_ms = (time.perf_counter() - started) * 1000

    if case.category in ("clarify", "refuse"):
        action = state.get("action")
        return CaseResult(
            case=case,
            passed=action == case.category,
            detail=f"action={action}",
            action=action,
            generated_sql=None,
            duration_ms=duration_ms,
        )

    if state.get("action") != "sql" or state.get("rows") is None:
        feedback = state.get("error_feedback") or (state.get("answer") or "")[:80]
        return CaseResult(
            case=case,
            passed=False,
            detail=f"no executed result (action={state.get('action')}): {feedback}",
            action=state.get("action"),
            generated_sql=state.get("sql"),
            duration_ms=duration_ms,
        )

    passed, detail = compare_result_sets(
        rows_to_tuples(state["rows"]), case.expected_rows, ordered=case.ordered
    )
    return CaseResult(
        case=case,
        passed=passed,
        detail=detail if not passed else "result sets match",
        action=state.get("action"),
        generated_sql=state.get("sql"),
        duration_ms=duration_ms,
    )


# ---------------------------------------------------------------- report/gates
def gates_met(results: list[CaseResult]) -> tuple[bool, dict[str, int]]:
    passes: dict[str, int] = {"sql": 0, "clarify": 0, "refuse": 0}
    for r in results:
        if r.passed:
            passes[r.case.category] += 1
    total = sum(passes.values())
    ok = (
        passes["refuse"] == GATES["refuse"][0]
        and passes["sql"] >= GATES["sql"][0]
        and passes["clarify"] >= GATES["clarify"][0]
        and total >= GATES["total"][0]
    )
    return ok, {**passes, "total": total}


def write_report(results: list[CaseResult], ok: bool, counts: dict[str, int]) -> None:
    mark = lambda cond: "✓" if cond else "✗"  # noqa: E731
    lines = [
        "# Eval Report",
        "",
        f"- generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- overall: {'PASS' if ok else 'FAIL'} — total {counts['total']}/42",
        "",
        "| category | pass / total | gate | verdict |",
        "|---|---|---|---|",
        f"| refuse  | {counts['refuse']}/6  | 6/6 (hard) | {mark(counts['refuse'] == 6)} |",
        f"| sql     | {counts['sql']}/30 | ≥ 27/30 | {mark(counts['sql'] >= 27)} |",
        f"| clarify | {counts['clarify']}/6 | ≥ 5/6 | {mark(counts['clarify'] >= 5)} |",
        f"| total   | {counts['total']}/42 | ≥ 38/42 | {mark(counts['total'] >= 38)} |",
        "",
        "## Failures",
        "",
    ]
    failures = [r for r in results if not r.passed]
    if not failures:
        lines.append("none — all 42 cases passed.")
    for r in failures:
        lines += [
            f"### {r.case.id} [{r.case.category}] — {r.detail}",
            f"- question: {r.case.question}",
            f"- action: {r.action}, duration: {r.duration_ms:.0f} ms",
        ]
        if r.generated_sql:
            lines.append(f"- generated SQL: `{r.generated_sql}`")
        if r.case.expected_sql:
            lines.append(f"- expected SQL: `{r.case.expected_sql}`")
        lines.append("")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval")
    parser.add_argument(
        "--jobs", type=int, default=1, help="parallel workers; >1 may trigger provider rate limits"
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.zhipuai_api_key:
        sys.exit("ZHIPUAI_API_KEY is not set; put it in .env (see .env.example)")

    cases = load_golden(settings)
    schema_context = build_schema_context(settings.database_url)

    if args.jobs <= 1:
        results = [run_case(case, settings, schema_context) for case in cases]
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            results = list(
                pool.map(lambda c: run_case(c, settings, schema_context), cases)
            )
    # Transport errors (429) must not consume gate margin: one retry pass
    # after a backoff, sequential, only for the affected cases.
    rate_limited = [r for r in results if not r.passed and "429" in r.detail]
    if rate_limited:
        ids = ", ".join(r.case.id for r in rate_limited)
        print(f"rate-limited: {len(rate_limited)} case(s) [{ids}]; 30s backoff, one retry pass")
        time.sleep(30)
        rerun = {r.case.id: run_case(r.case, settings, schema_context) for r in rate_limited}
        results = [rerun.get(r.case.id, r) for r in results]

    results.sort(key=lambda r: r.case.id)  # stable report regardless of finish order

    ok, counts = gates_met(results)
    write_report(results, ok, counts)

    print(f"refuse {counts['refuse']}/6 (hard), sql {counts['sql']}/30, "
          f"clarify {counts['clarify']}/6, total {counts['total']}/42 -> "
          f"{'PASS' if ok else 'FAIL'}")
    print(f"report: {REPORT_PATH}")
    if any("429" in r.detail for r in results):
        print("hint: rate limited mid-run — rerun with --jobs 1")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
