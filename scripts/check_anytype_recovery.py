"""Ratchet ``AnyType()`` recovery sites in ``geno.typechecker``.

The typechecker intentionally returns ``AnyType`` after some diagnostics to
avoid cascading errors. This check keeps those recovery sites explicit: adding
or removing a direct ``AnyType()`` construction in ``geno/typechecker.py``
requires updating the baseline below with the reason for the change.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
TYPECHECKER_PATH = REPO_ROOT / "geno" / "typechecker.py"


@dataclass(frozen=True)
class AnyTypeRecoverySite:
    count: int
    reason: str


ANYTYPE_RECOVERY_BASELINE: dict[str, AnyTypeRecoverySite] = {
    "<module>": AnyTypeRecoverySite(
        1,
        "Shared singleton used where a reusable permissive type is needed.",
    ),
    "_check_await_expr": AnyTypeRecoverySite(
        1,
        "Recover after diagnosing await on a non-Async expression.",
    ),
    "_check_constructor_call": AnyTypeRecoverySite(
        1,
        "Recover after an unknown constructor diagnostic.",
    ),
    "_check_field_access": AnyTypeRecoverySite(
        3,
        "Recover after invalid field targets, missing type metadata, or unknown fields.",
    ),
    "_check_for_statement": AnyTypeRecoverySite(
        1,
        "Recover loop element type after diagnosing a non-iterable for target.",
    ),
    "_check_identifier": AnyTypeRecoverySite(
        2,
        "Recover after target rejection or undefined-variable diagnostics.",
    ),
    "_check_index_access": AnyTypeRecoverySite(
        1,
        "Recover after diagnosing an invalid index target.",
    ),
    "_check_list_comprehension": AnyTypeRecoverySite(
        1,
        "Recover comprehension element type after diagnosing a non-iterable input.",
    ),
    "_check_match_expr": AnyTypeRecoverySite(
        1,
        "Recover empty or fully-invalid match expressions with a permissive result.",
    ),
    "_check_propagate_expr": AnyTypeRecoverySite(
        2,
        "Recover after invalid use of the propagation operator.",
    ),
    "_check_with_expr": AnyTypeRecoverySite(
        3,
        "Recover after invalid record-update targets or metadata.",
    ),
    "_literal_type": AnyTypeRecoverySite(
        1,
        "Fallback for parser literals outside the typed primitive set.",
    ),
}


@dataclass(frozen=True)
class AnyTypeCall:
    function: str
    line: int
    source: str


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return "<module>"


def scan_anytype_calls(path: Path = TYPECHECKER_PATH) -> list[AnyTypeCall]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    parents = _parent_map(tree)
    calls: list[AnyTypeCall] = []

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "AnyType"
        ):
            segment = ast.get_source_segment(source, node) or "AnyType()"
            calls.append(
                AnyTypeCall(
                    function=_enclosing_function(node, parents),
                    line=node.lineno,
                    source=segment,
                )
            )

    return sorted(calls, key=lambda call: (call.function, call.line, call.source))


def grouped_counts(calls: Sequence[AnyTypeCall]) -> Counter[str]:
    return Counter(call.function for call in calls)


def compare_to_baseline(
    counts: Counter[str],
    baseline: dict[str, AnyTypeRecoverySite] = ANYTYPE_RECOVERY_BASELINE,
) -> list[str]:
    errors: list[str] = []
    for function, count in sorted(counts.items()):
        expected = baseline.get(function)
        if expected is None:
            errors.append(f"{function}: {count} unclassified AnyType() call(s)")
        elif count != expected.count:
            errors.append(
                f"{function}: expected {expected.count} AnyType() call(s), found {count}"
            )

    for function, expected in sorted(baseline.items()):
        if function not in counts:
            errors.append(
                f"{function}: baseline expects {expected.count} AnyType() call(s), found 0"
            )

    return errors


def _print_report(calls: Sequence[AnyTypeCall]) -> None:
    counts = grouped_counts(calls)
    for function, count in sorted(counts.items()):
        reason = ANYTYPE_RECOVERY_BASELINE.get(function)
        reason_text = reason.reason if reason is not None else "UNCLASSIFIED"
        print(f"{function}: {count} - {reason_text}")
        for call in calls:
            if call.function == function:
                print(f"  line {call.line}: {call.source}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the classified AnyType recovery baseline."
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the classified recovery sites before returning.",
    )
    args = parser.parse_args(argv)

    calls = scan_anytype_calls()
    errors = compare_to_baseline(grouped_counts(calls))

    if args.list:
        _print_report(calls)

    if errors:
        print("AnyType recovery baseline is out of date:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    if not args.list:
        print("AnyType recovery baseline is current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
