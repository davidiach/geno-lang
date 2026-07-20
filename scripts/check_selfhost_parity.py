#!/usr/bin/env python3
"""Check selfhost compiler parity against canonical implementation.

Reports drift in keywords and builtins between selfhost/ and the
canonical geno/ implementation. Exits non-zero if drift exceeds the
declared parity subset.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELFHOST = ROOT / "selfhost"

sys.path.insert(0, str(ROOT))

warnings: list[str] = []
errors: list[str] = []


def _union_groups(groups: dict[str, set[str]]) -> set[str]:
    values: set[str] = set()
    for names in groups.values():
        values.update(names)
    return values


def _group_duplicates(groups: dict[str, set[str]]) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for group_name, names in groups.items():
        for name in names:
            owners.setdefault(name, []).append(group_name)
    return {
        name: group_names
        for name, group_names in owners.items()
        if len(group_names) > 1
    }


def _format_group_counts(groups: dict[str, set[str]]) -> str:
    return ", ".join(f"{name}={len(values)}" for name, values in groups.items())


# ---------------------------------------------------------------------------
# Parity subset declarations
# ---------------------------------------------------------------------------
# These define what selfhost is EXPECTED to support. Anything in canonical
# but outside these sets is a known gap, not a failure. Anything in these
# sets but missing from selfhost IS a failure.

SELFHOST_REQUIRED_KEYWORDS = {
    "func",
    "end",
    "let",
    "var",
    "if",
    "then",
    "else",
    "while",
    "do",
    "for",
    "in",
    "match",
    "with",
    "return",
    "and",
    "or",
    "not",
    "true",
    "false",
    "type",
    "requires",
    "ensures",
    "example",
    "ref",
    "fn",
    "where",
    "import",
}

# Keywords that canonical has but selfhost intentionally does not support yet
SELFHOST_KEYWORD_GAP_GROUPS = {
    "loop_control": {"break", "continue"},
    "exception_control": {"try", "catch", "throw"},
    "abstraction": {"trait", "impl"},
    "async_control": {"async", "await"},
    "tests_and_exports": {"test", "assert", "export"},
}
KNOWN_KEYWORD_GAPS = _union_groups(SELFHOST_KEYWORD_GAP_GROUPS)


def get_canonical_keywords() -> set[str]:
    from geno.tokens import KEYWORDS

    return set(KEYWORDS.keys())


def get_selfhost_keywords() -> set[str]:
    """Extract keywords from selfhost/Tokens.geno build_keyword_map."""
    source = (SELFHOST / "Tokens.geno").read_text()
    return set(re.findall(r'mutable_map_set\(map: m, key: "(\w+)", value:', source))


def get_canonical_builtins() -> set[str]:
    """Return the canonical builtin name set derived from the manifest."""
    from geno.builtin_manifest import BUILTIN_MANIFEST
    from geno.builtin_registry import SOURCE_BUILTIN_NAME_OVERRIDES

    return {SOURCE_BUILTIN_NAME_OVERRIDES.get(name, name) for name in BUILTIN_MANIFEST}


def get_selfhost_builtins() -> set[str]:
    """Extract builtin names from selfhost/TypeChecker.geno."""
    source = (SELFHOST / "TypeChecker.geno").read_text()
    return set(re.findall(r'register_builtin\(cs: cs, name: "(\w+)"', source))


# Builtins that canonical has but selfhost intentionally does not support yet.
# New canonical builtins must be added to one of these groups explicitly or the
# parity check fails. Pure groups are listed first to keep the burn-down order
# visible and biased toward host-independent implementation work.
SELFHOST_BUILTIN_GAP_GROUPS = {
    "pure_collection_next": {
        "enumerate",
        "flat_map",
        "list_all",
        "list_any",
        "list_chunk",
        "list_drop",
        "list_enumerate",
        "list_find",
        "list_find_index",
        "list_flatten",
        "list_fold_right",
        "list_group_by",
        "list_intersperse",
        "list_take",
        "list_zip",
        "map_entries",
        "map_filter_map",
        "map_from_entries",
        "map_from_list",
        "map_map_values",
        "map_merge",
        "range",
        "set_add",
        "set_contains",
        "set_from_list",
        "set_intersection",
        "set_new",
        "set_remove",
        "set_size",
        "set_to_list",
        "set_union",
        "sort",
        "sort_by",
        "zip",
    },
    "pure_string_next": {
        "format",
    },
    "pure_math_next": {
        "bit_or",
        "ceil",
        "clamp",
        "floor",
        "math_ceil",
        "math_clamp",
        "math_cos",
        "math_e",
        "math_floor",
        "math_log",
        "math_min",
        "math_pi",
        "math_round",
        "math_sin",
        "math_sqrt",
        "parse_float",
        "round",
    },
    "pure_option_result_next": {
        "option_and_then",
        "option_flatten",
        "option_map",
        "option_to_result",
        "result_and_then",
        "result_is_err",
        "result_is_ok",
        "result_map",
        "result_map_err",
        "result_to_option",
        "result_unwrap_or",
    },
    "pure_path_data_next": {
        "csv_parse",
        "csv_parse_with_headers",
        "json_parse",
        "json_stringify",
        "json_stringify_pretty",
        "json_to_string",
        "path_extension",
        "path_filename",
        "path_is_absolute",
        "path_join",
        "path_parent",
        "regex_find_all",
        "regex_match",
        "regex_replace",
        "toml_parse",
    },
    "host_dependent": {
        "clock_elapsed",
        "clock_format",
        "clock_parse",
        "datetime_elapsed",
        "datetime_format",
        "datetime_now",
        "datetime_parse",
        "env_get",
        "env_get_or",
        "exec",
        "exec_with_input",
        "fs_canonicalize",
        "fs_list_dir",
        "fs_metadata",
        "fs_symlink_metadata",
        "http_listen",
        "http_post",
        "http_request",
        "http_respond",
        "http_route",
        "sleep_ms",
        "spawn",
        "spawn_with_input",
        "stdin_read_all",
    },
    "browser_target": {
        "clear_text_input",
        "get_text_input",
        "is_mouse_clicked",
        "is_mouse_down",
        "mouse_x",
        "mouse_y",
    },
}
KNOWN_BUILTIN_GAPS = _union_groups(SELFHOST_BUILTIN_GAP_GROUPS)


def check_budget_groups() -> None:
    """Verify the structured budget metadata is internally consistent."""
    for label, groups, declared_gaps in (
        ("keyword", SELFHOST_KEYWORD_GAP_GROUPS, KNOWN_KEYWORD_GAPS),
        ("builtin", SELFHOST_BUILTIN_GAP_GROUPS, KNOWN_BUILTIN_GAPS),
    ):
        duplicates = _group_duplicates(groups)
        if duplicates:
            errors.append(
                f"Selfhost {label} gap appears in multiple groups: {duplicates}"
            )

        grouped_gaps = _union_groups(groups)
        if grouped_gaps != declared_gaps:
            errors.append(
                f"Selfhost {label} gap groups do not match declared gaps: "
                f"grouped_only={sorted(grouped_gaps - declared_gaps)}, "
                f"declared_only={sorted(declared_gaps - grouped_gaps)}"
            )


def check_keywords() -> None:
    canonical = get_canonical_keywords()
    selfhost = get_selfhost_keywords()

    # Check required subset is present
    missing_required = SELFHOST_REQUIRED_KEYWORDS - selfhost
    if missing_required:
        errors.append(f"Selfhost missing required keywords: {sorted(missing_required)}")

    resolved_gaps = KNOWN_KEYWORD_GAPS & selfhost
    if resolved_gaps:
        errors.append(
            f"Known keyword gaps now implemented in selfhost: {sorted(resolved_gaps)}"
        )

    # Check for unknown gaps (canonical has it, selfhost doesn't, not in known gaps)
    unknown_gaps = canonical - selfhost - KNOWN_KEYWORD_GAPS
    if unknown_gaps:
        errors.append(
            f"New canonical keywords not in selfhost or known gaps: {sorted(unknown_gaps)}"
        )

    # Report known gaps as info
    actual_gaps = canonical - selfhost
    if actual_gaps:
        warnings.append(
            f"Selfhost keyword gaps ({len(actual_gaps)}): {sorted(actual_gaps)}"
        )
        warnings.append(
            "Selfhost keyword gap budget: "
            f"{_format_group_counts(SELFHOST_KEYWORD_GAP_GROUPS)}"
        )


def check_builtins() -> None:
    canonical = get_canonical_builtins()
    selfhost = get_selfhost_builtins()

    # Report coverage
    coverage = len(selfhost & canonical)
    total = len(canonical)
    warnings.append(
        f"Selfhost builtin coverage: {coverage}/{total} ({100 * coverage // total}%)"
    )
    warnings.append(
        "Selfhost builtin gap budget: "
        f"{_format_group_counts(SELFHOST_BUILTIN_GAP_GROUPS)}"
    )

    # Check for phantom builtins (in selfhost but not canonical)
    phantom = selfhost - canonical
    if phantom:
        errors.append(
            f"Selfhost registers builtins not in canonical: {sorted(phantom)}"
        )

    resolved_gaps = KNOWN_BUILTIN_GAPS & selfhost
    if resolved_gaps:
        errors.append(
            f"Known builtin gaps now implemented in selfhost: {sorted(resolved_gaps)}"
        )

    # Check for unknown gaps: canonical builtins that are not implemented in
    # selfhost and not explicitly tracked as known gaps.
    unknown_gaps = canonical - selfhost - KNOWN_BUILTIN_GAPS
    if unknown_gaps:
        errors.append(
            "New canonical builtins not in selfhost or known gaps: "
            f"{sorted(unknown_gaps)}"
        )


def main() -> int:
    if not SELFHOST.exists():
        print("selfhost/ directory not found - skipping parity check")
        return 0

    check_budget_groups()
    check_keywords()
    check_builtins()

    if warnings:
        print("Selfhost parity notes:")
        for w in warnings:
            print(f"  [info] {w}")

    if errors:
        print("\nSelfhost parity errors:")
        for e in errors:
            print(f"  [FAIL] {e}")
        return 1

    print("\nSelfhost parity check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
