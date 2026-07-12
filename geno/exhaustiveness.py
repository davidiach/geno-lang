"""
Pattern exhaustiveness analysis for Geno type checking.

This module keeps the finite-case pattern matrix logic separate from the
broader typechecker while preserving TypeChecker as the caller-facing API.
"""

from __future__ import annotations

from typing import cast

from .ast_nodes import (
    ConstructorPattern,
    ListPattern,
    LiteralPattern,
    MatchArm,
    Pattern,
    RestPattern,
    VariablePattern,
    WildcardPattern,
)
from .tokens import SourceLocation
from .types import (
    BoolType,
    FloatType,
    IntType,
    ListType,
    OptionType,
    ResultType,
    StringType,
    TupleType,
    Type,
    TypeDefInfo,
    TypeError,
    UserType,
)


class ExhaustivenessMixin:
    """Mixin for TypeChecker pattern exhaustiveness helpers."""

    type_defs: dict[str, TypeDefInfo]

    def _error(self, message: str, location: SourceLocation) -> TypeError:
        raise NotImplementedError

    def _substitute_type_vars(self, t: Type, substitutions: dict[str, Type]) -> Type:
        raise NotImplementedError

    def _check_pattern_exhaustiveness(
        self, scrutinee_type: Type, arms: list[MatchArm], location: SourceLocation
    ) -> None:
        """Check that patterns exhaustively cover all cases."""
        patterns = [arm.pattern for arm in arms if arm.guard is None]

        # Unguarded wildcard or variable patterns cover everything.
        if any(
            isinstance(pattern, (WildcardPattern, VariablePattern))
            for pattern in patterns
        ):
            return

        if isinstance(scrutinee_type, ListType):
            if self._list_patterns_are_exhaustive(patterns):
                return
            self._error(
                f"Non-exhaustive patterns: match on {scrutinee_type} "
                "requires a default arm",
                location,
            )
            return

        # Finite types (Bool, Option, Result, ADTs) get full case analysis below.
        # Non-finite types (Int, Float, String, List, Tuple) cannot be exhaustively
        # enumerated, so require a catch-all arm. Unknown types are left unchecked.
        if not self._supports_exhaustiveness(scrutinee_type):
            if isinstance(
                scrutinee_type,
                (IntType, FloatType, StringType, TupleType),
            ):
                self._error(
                    f"Non-exhaustive patterns: match on {scrutinee_type} "
                    "requires a default arm",
                    location,
                )
            return

        missing = self._find_missing_pattern_vectors(
            [scrutinee_type], [[pattern] for pattern in patterns], location, limit=3
        )
        if missing:
            rendered = ", ".join(
                self._render_pattern_vector(vector) for vector in missing
            )
            self._error(f"Non-exhaustive patterns: missing {rendered}", location)

    def _supports_exhaustiveness(self, t: Type) -> bool:
        """Return whether this type has a finite set of statically-known cases."""
        if isinstance(t, (BoolType, OptionType, ResultType)):
            return True
        if isinstance(t, UserType):
            return t.name in self.type_defs
        return False

    def _list_patterns_are_exhaustive(self, patterns: list[Pattern]) -> bool:
        """Return whether the list patterns cover every possible list.

        A list pattern only counts toward coverage when every fixed element
        subpattern is a catch-all. A constrained element (e.g. the literal
        ``true`` in ``[true, ...rest]``) means the pattern does not cover its
        whole length class, so treating it as coverage would be unsound; such
        patterns are skipped, which forces a default arm.
        """
        fixed_lengths: set[int] = set()
        min_rest_length: int | None = None

        for pattern in patterns:
            if not isinstance(pattern, ListPattern):
                continue

            fixed_len = 0
            has_rest = False
            all_elements_catchall = True
            for elem in pattern.elements:
                if isinstance(elem, RestPattern):
                    has_rest = True
                else:
                    fixed_len += 1
                    if not self._is_catchall_pattern(elem):
                        all_elements_catchall = False

            if not all_elements_catchall:
                continue

            if has_rest:
                if min_rest_length is None or fixed_len < min_rest_length:
                    min_rest_length = fixed_len
            else:
                fixed_lengths.add(fixed_len)

        if min_rest_length is None:
            return False

        return all(length in fixed_lengths for length in range(min_rest_length))

    def _constructor_cases_for_type(
        self, t: Type
    ) -> list[tuple[str, tuple[Type, ...]]] | None:
        """Return constructor cases for a finite ADT type, if known."""
        if isinstance(t, OptionType):
            return [("Some", (t.value_type,)), ("None", ())]
        if isinstance(t, ResultType):
            return [("Ok", (t.ok_type,)), ("Err", (t.err_type,))]
        if isinstance(t, UserType):
            type_info = self.type_defs.get(t.name)
            if type_info is None:
                return None
            substitutions = {
                param: arg for param, arg in zip(type_info.type_params, t.type_args)
            }
            cases: list[tuple[str, tuple[Type, ...]]] = []
            for constructor, fields in type_info.variants.items():
                resolved_fields = tuple(
                    self._substitute_type_vars(field_type, substitutions)
                    for _field_name, field_type in fields
                )
                cases.append((constructor, resolved_fields))
            return cases
        return None

    @staticmethod
    def _is_catchall_pattern(pattern: Pattern) -> bool:
        return isinstance(pattern, (WildcardPattern, VariablePattern))

    def _safe_list_shape(self, pattern: Pattern) -> tuple[str, int] | None:
        """Return a sound list-shape descriptor when element values are unconstrained.

        exact(n): matches every list of exactly length n
        at_least(n): matches every list of length >= n
        """
        if self._is_catchall_pattern(pattern):
            return ("at_least", 0)
        if not isinstance(pattern, ListPattern):
            return None

        fixed_len = 0
        has_rest = False
        for elem in pattern.elements:
            if isinstance(elem, RestPattern):
                has_rest = True
                continue
            if not self._is_catchall_pattern(elem):
                return None
            fixed_len += 1

        return ("at_least", fixed_len) if has_rest else ("exact", fixed_len)

    @staticmethod
    def _list_shape_matches_length(shape: tuple[str, int], length: int) -> bool:
        kind, size = shape
        if kind == "exact":
            return length == size
        return length >= size

    def _list_shape_scan(
        self, rows: list[list[Pattern]]
    ) -> tuple[list[int], int, bool] | None:
        """Return representative lengths for sound list-shape analysis."""
        saw_shape = False
        max_size = 0
        has_nonempty_shape = False

        for row in rows:
            shape = self._safe_list_shape(row[0])
            if shape is None:
                continue
            saw_shape = True
            _kind, size = shape
            max_size = max(max_size, size)
            if self._list_shape_matches_length(shape, 1):
                has_nonempty_shape = True

        if not saw_shape:
            return None

        return (list(range(max_size + 2)), max_size, has_nonempty_shape)

    @staticmethod
    def _render_list_length_witness(
        length: int, max_size: int, has_nonempty_shape: bool
    ) -> str:
        if length == 0:
            return "[]"
        if length == 1 and max_size == 0 and not has_nonempty_shape:
            return "[_, ...]"
        return "[" + ", ".join("_" for _ in range(length)) + "]"

    @staticmethod
    def _default_missing_vector(types: list[Type]) -> list[str]:
        return ["_" for _ in types]

    def _default_witness_for_type(self, t: Type) -> str:
        """Return a generic witness value for a type without recursive descent."""
        if isinstance(t, BoolType):
            return "true"
        if isinstance(t, IntType):
            return "<int>"
        if isinstance(t, FloatType):
            return "<float>"
        if isinstance(t, StringType):
            return "<string>"
        if isinstance(t, ListType):
            return "[...]"
        if isinstance(t, TupleType):
            inner = ", ".join(
                self._default_witness_for_type(et) for et in t.element_types
            )
            return f"({inner})"
        if isinstance(t, OptionType):
            return "Some(_)"
        if isinstance(t, ResultType):
            return "Ok(_)"
        if isinstance(t, UserType):
            type_info = self.type_defs.get(t.name)
            if type_info and type_info.variants:
                constructor, fields = next(iter(type_info.variants.items()))
                if not fields:
                    return cast(str, constructor)
                return self._render_constructor_witness(
                    constructor, ["_"] * len(fields)
                )
        return "_"

    def _find_missing_pattern_vectors(
        self,
        types: list[Type],
        rows: list[list[Pattern]],
        location: SourceLocation,
        limit: int = 3,
    ) -> list[list[str]]:
        """Return example missing pattern vectors for the given pattern matrix."""
        if not types:
            return [] if rows else [[]]

        first_type = types[0]
        rest_types = types[1:]

        if not rows:
            return [[self._default_witness_for_type(t) for t in types]]

        if rows and all(self._is_catchall_pattern(row[0]) for row in rows):
            if not rest_types:
                return []
            return [
                ["_"] + suffix
                for suffix in self._find_missing_pattern_vectors(
                    rest_types,
                    [list(row[1:]) for row in rows],
                    location,
                    limit=limit,
                )
            ]

        if isinstance(first_type, BoolType):
            missing: list[list[str]] = []
            for value in (True, False):
                specialized: list[list[Pattern]] = []
                for row in rows:
                    first, tail = row[0], row[1:]
                    if self._is_catchall_pattern(first) or (
                        isinstance(first, LiteralPattern) and first.value is value
                    ):
                        specialized.append(list(tail))
                for suffix in self._find_missing_pattern_vectors(
                    rest_types, specialized, location, limit=limit - len(missing)
                ):
                    missing.append([("true" if value else "false")] + suffix)
                    if len(missing) >= limit:
                        return missing
            return missing

        if isinstance(first_type, ListType):
            scan = self._list_shape_scan(rows)
            if scan is not None:
                representative_lengths, max_size, has_nonempty_shape = scan
                missing_list: list[list[str]] = []
                for length in representative_lengths:
                    specialized = [
                        list(row[1:])
                        for row in rows
                        if (shape := self._safe_list_shape(row[0])) is not None
                        and self._list_shape_matches_length(shape, length)
                    ]
                    for suffix in self._find_missing_pattern_vectors(
                        rest_types,
                        specialized,
                        location,
                        limit=limit - len(missing_list),
                    ):
                        missing_list.append(
                            [
                                self._render_list_length_witness(
                                    length, max_size, has_nonempty_shape
                                )
                            ]
                            + suffix
                        )
                        if len(missing_list) >= limit:
                            return missing_list
                return missing_list

        cases = self._constructor_cases_for_type(first_type)
        if cases is not None:
            missing_vectors: list[list[str]] = []
            for constructor, field_types in cases:
                specialized = self._specialize_constructor_rows(
                    rows, constructor, len(field_types), location
                )
                sub_missing = self._find_missing_pattern_vectors(
                    list(field_types) + rest_types,
                    specialized,
                    location,
                    limit=limit - len(missing_vectors),
                )
                for vector in sub_missing:
                    field_count = len(field_types)
                    field_witnesses = vector[:field_count]
                    rest_witnesses = vector[field_count:]
                    head = self._render_constructor_witness(
                        constructor, field_witnesses
                    )
                    missing_vectors.append([head] + rest_witnesses)
                    if len(missing_vectors) >= limit:
                        return missing_vectors
            return missing_vectors

        default_rows = [
            list(row[1:]) for row in rows if self._is_catchall_pattern(row[0])
        ]
        if not default_rows:
            witness = self._default_witness_for_type(first_type)
            rest = self._default_missing_vector(rest_types)
            return [[witness] + rest]

        return [
            ["_"] + suffix
            for suffix in self._find_missing_pattern_vectors(
                rest_types, default_rows, location, limit=limit
            )
        ]

    def _specialize_constructor_rows(
        self,
        rows: list[list[Pattern]],
        constructor: str,
        arity: int,
        location: SourceLocation,
    ) -> list[list[Pattern]]:
        """Specialize a pattern matrix to one constructor case."""
        specialized: list[list[Pattern]] = []
        for row in rows:
            first, tail = row[0], row[1:]
            if self._is_catchall_pattern(first):
                wildcards = [WildcardPattern(location=location) for _ in range(arity)]
                specialized.append(wildcards + list(tail))
            elif (
                isinstance(first, ConstructorPattern)
                and first.constructor == constructor
            ):
                specialized.append(list(first.subpatterns) + list(tail))
        return specialized

    @staticmethod
    def _render_constructor_witness(
        constructor: str, field_witnesses: list[str]
    ) -> str:
        if not field_witnesses:
            return constructor
        return f"{constructor}({', '.join(field_witnesses)})"

    @staticmethod
    def _render_pattern_vector(vector: list[str]) -> str:
        if not vector:
            return "()"
        if len(vector) == 1:
            return vector[0]
        return f"({', '.join(vector)})"
