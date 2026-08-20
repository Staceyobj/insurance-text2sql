"""CLI: one-shot question or interactive REPL (SPEC §7.1)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from text2sql.config import Settings, get_settings
from text2sql.graph import build_graph
from text2sql.llm import build_llm
from text2sql.state import QueryState, new_state


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="t2s", description="保险数据自然语言问答（text-to-SQL）"
    )
    parser.add_argument("question", nargs="?", help="一个问题；省略则进入交互模式")
    parser.add_argument("--show-sql", action="store_true", help="显示最终执行的规范化 SQL")
    parser.add_argument("--json", action="store_true", dest="as_json", help="输出完整状态 JSON")
    parser.add_argument("--debug", action="store_true", help="打印执行 trace")
    return parser.parse_args(argv)


def _render(state: QueryState, args: argparse.Namespace) -> None:
    if args.as_json:
        print(json.dumps(state, ensure_ascii=False, default=str, indent=2))
        return
    print(state.get("answer") or "")
    if args.show_sql and state.get("sql"):
        print(f"\nSQL: {state['sql']}")
    if args.debug:
        for entry in state.get("trace", []):
            print(f"  [{entry['node']}] {entry['duration_ms']}ms")
            print(f"    in  {entry['input_digest']}")
            print(f"    out {entry['output_digest']}")


def _build(settings: Settings):
    """LLM + graph; the missing-key error surfaces as a friendly message."""
    try:
        llm = build_llm(settings)
    except RuntimeError as err:
        print(str(err), file=sys.stderr)
        raise SystemExit(2) from err
    return build_graph(llm, settings)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    graph = _build(settings)

    if args.question:
        _render(graph.invoke(new_state(args.question)), args)
        return 0

    print("保险数据问答（每问独立，无多轮记忆；空行或 Ctrl-D 退出）")
    while True:
        try:
            question = input("t2s> ").strip()
        except EOFError:
            break
        if not question or question.lower() in {"exit", "quit", "q"}:
            break
        _render(graph.invoke(new_state(question)), args)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
