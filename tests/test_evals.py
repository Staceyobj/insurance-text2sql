"""Unit tests for the pure result-set comparison used by the eval runner.

These exercise ONLY the offline comparison semantics (SPEC §6.2): column
count equality, order sensitivity, numeric tolerance, NULL handling, and
multiset row equality. No LLM, no database.
"""

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root: evals/

from evals.runner import compare_result_sets, rows_to_tuples  # noqa: E402


def ok(generated, expected, **kwargs):
    passed, detail = compare_result_sets(generated, expected, **kwargs)
    assert passed, f"expected match, got: {detail}"


def bad(generated, expected, **kwargs):
    passed, detail = compare_result_sets(generated, expected, **kwargs)
    assert not passed, "expected mismatch"
    assert detail, "mismatch must carry a diff summary"


def test_identical_rows_match():
    ok([("a", 1), ("b", 2)], [("a", 1), ("b", 2)])


def test_order_insensitive_by_default():
    ok([("b", 2), ("a", 1)], [("a", 1), ("b", 2)])


def test_order_sensitive_when_ordered_true():
    bad([("b", 2), ("a", 1)], [("a", 1), ("b", 2)], ordered=True)


def test_column_count_mismatch_fails():
    bad([("a", 1)], [("a", 1, "x")])


def test_numeric_tolerance_within_bound():
    ok([(0.1,)], [(0.1000001,)])
    ok([(Decimal("2.0"),)], [(2,)])


def test_numeric_beyond_tolerance_fails():
    bad([(0.1,)], [(0.101,)])


def test_decimal_and_int_equivalent():
    ok([(Decimal("1044119.00"), 661)], [(1044119, Decimal("661.0"))])


def test_null_equals_only_null():
    ok([(None, "x")], [(None, "x")])
    bad([(None,)], [(0,)])
    bad([(None,)], [("",)])


def test_duplicate_row_counts_matter():
    ok([("a",), ("a",), ("b",)], [("a",), ("a",), ("b",)])
    bad([("a",), ("a",), ("b",)], [("a",), ("b",), ("b",)])


def test_empty_versus_nonempty_fails():
    bad([], [("a",)])
    bad([("a",)], [])


def test_bool_not_confused_with_int():
    bad([(True,)], [(1,)])


def test_rows_to_tuples_uses_value_order():
    assert rows_to_tuples([{"city": "杭州", "n": 3}]) == [("杭州", 3)]
