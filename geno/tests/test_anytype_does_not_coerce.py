"""
Regression tests for #615 — AnyType soundness.

The typechecker used to return ``AnyType()`` from several constructor-call
fallbacks where a type parameter couldn't be inferred from arguments
(see ``_check_constructor_call`` in ``typechecker.py``).  Because
``_types_structurally_compatible`` treats ``AnyType`` as universally
compatible, that silently laundered an unbound type parameter into
any downstream position — the "silent coercion" hole described by the
audit.

These tests pin down the tightening:

1. Constructor calls with unbound type parameters now return a fresh
   ``TypeVar`` (same pattern already used by ``_check_list_literal``
   and ``_check_type_identifier``).  Downstream unification may still
   bind that ``TypeVar`` legitimately via the HM fallback in
   ``_types_strictly_compatible`` (so explicit annotations like
   ``let x: Option[Int] = None`` still typecheck), but a mismatch at
   the outer class level is caught cleanly.

2. A new ``in_recovery`` flag on ``_types_structurally_compatible``
   and ``_types_compatible_with_subs`` gates whether ``AnyType``
   coerces.  The default (``True``) preserves the existing cascade
   suppression for error recovery.  Passing ``in_recovery=False`` at
   a specific call site causes ``AnyType`` on either side to no
   longer satisfy the check, providing a single seam for future
   hardening passes.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.parser import parse
from geno.typechecker import (
    AnyType,
    IntType,
    OptionType,
    ResultType,
    StringType,
    TypeChecker,
    TypeVar,
    UserType,
    type_check,
)
from geno.typechecker import TypeError as GenoTypeError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def check(source: str) -> None:
    program = parse(source)
    type_check(program)


def expect_error(source: str) -> GenoTypeError:
    program = parse(source)
    with pytest.raises(GenoTypeError) as exc_info:
        type_check(program)
    return exc_info.value  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# 1. Constructors with unbound type parameters do not leak AnyType
# ---------------------------------------------------------------------------


class TestConstructorUnboundTypeParamUsesFreshTypeVar:
    """Constructor sites that used ``AnyType()`` fallbacks now emit a
    fresh ``TypeVar`` so downstream unification is real, not silent."""

    def test_phantom_type_param_with_matching_annotation_infers(self) -> None:
        """A phantom type parameter is bound through the declaration
        annotation via the HM fallback — must still typecheck."""
        source = """
        type Wrapper[T] = Wrap(tag: String)

        func main() -> Int
            let x: Wrapper[Int] = Wrap("hi")
            return 0
        end func
        """
        check(source)

    def test_phantom_type_param_passed_to_unrelated_concrete_type_fails(
        self,
    ) -> None:
        """A phantom-param constructor must not silently coerce to a
        completely unrelated concrete type (the audit's key case)."""
        source = """
        type Wrapper[T] = Wrap(tag: String)

        func take_int(x: Int) -> Int
            example 1 -> 1
            return x
        end func

        func main() -> Int
            return take_int(Wrap("hi"))
        end func
        """
        err = expect_error(source)
        # outer type classes differ (Int vs Wrapper); must not coerce
        assert "Wrapper" in str(err) or "Int" in str(err)

    def test_generic_nullary_user_type_with_annotation(self) -> None:
        """Generic ADT with a nullary variant and an explicit target
        type: ``type Box[T] = Empty | Full(v: T); let x: Box[Int] = Full(5)``."""
        source = """
        type Box[T] = Empty | Full(v: T)

        func main() -> Int
            let x: Box[Int] = Full(5)
            return 0
        end func
        """
        check(source)

    def test_nullary_constructor_no_annotation_errors(self) -> None:
        """``let x = Wrap("hi")`` (phantom-param constructor, no
        annotation) must NOT silently succeed.  Previously the
        fallback was ``Wrapper[AnyType]``, which the let-binding's
        ``_contains_any`` guard already caught.  After the fix it is
        ``Wrapper[fresh_T]``, which the sibling ``_has_type_vars``
        branch catches — so the tightening does not regress this
        diagnostic.  The user is told to add an annotation."""
        source = """
        type Wrapper[T] = Wrap(tag: String)

        func main() -> Int
            let x = Wrap("hi")
            return 0
        end func
        """
        err = expect_error(source)
        assert "Cannot infer" in str(err)
        assert "Wrapper" in str(err)

    def test_nullary_constructor_let_binding_with_annotation_works(
        self,
    ) -> None:
        """Adding the annotation resolves the inference — the HM
        fallback in ``_types_strictly_compatible`` binds the fresh
        ``TypeVar`` to the declared parameter type."""
        source = """
        type Wrapper[T] = Wrap(tag: String)

        func main() -> Int
            let x: Wrapper[Int] = Wrap("hi")
            return 0
        end func
        """
        check(source)

    def test_option_none_with_annotation(self) -> None:
        """The classic case: ``let x: Option[Int] = None``.  Must keep
        working after the tightening."""
        source = """
        func main() -> Int
            let x: Option[Int] = None
            return 0
        end func
        """
        check(source)

    def test_option_some_with_wrong_type_fails(self) -> None:
        """``Some("s")`` assigned to ``Option[Int]`` must fail — the
        inner ``T=String`` is bound from the argument, not left as a
        fresh TypeVar that silently unifies with ``Int``."""
        source = """
        func main() -> Int
            let x: Option[Int] = Some("s")
            return 0
        end func
        """
        err = expect_error(source)
        assert "Option" in str(err) or "String" in str(err) or "Int" in str(err)

    def test_result_ok_with_inferrable_ok_type(self) -> None:
        source = """
        func main() -> Int
            let r: Result[Int, String] = Ok(5)
            return 0
        end func
        """
        check(source)

    def test_result_mismatched_ok_type_fails(self) -> None:
        """``Ok("s")`` bound to ``Result[Int, _]`` must fail."""
        source = """
        func main() -> Int
            let r: Result[Int, String] = Ok("s")
            return 0
        end func
        """
        err = expect_error(source)
        assert "Result" in str(err) or "String" in str(err) or "Int" in str(err)

    def test_no_bare_anytype_in_compound_constructor_result(self) -> None:
        """Low-level: the ``Type`` returned from ``_check_constructor_call``
        for a phantom-param constructor contains a fresh ``TypeVar``
        rather than an ``AnyType`` — this is the structural property
        the rest of the PR depends on.

        We use a program with an explicit annotation so the let-
        binding's inference guard does not fire, then read the
        resolved type that the compiler attached to the RHS
        expression during checking.
        """
        from geno.ast_nodes import ConstructorCall, FunctionDef
        from geno.parser import parse as parse_source

        program = parse_source(
            """
            type Wrapper[T] = Wrap(tag: String)
            func main() -> Int
                let w: Wrapper[Int] = Wrap("hi")
                return 0
            end func
            """
        )
        checker = TypeChecker()
        checker.check_program(program)
        assert not checker.errors, [str(e) for e in checker.errors]

        main_func = next(d for d in program.definitions if isinstance(d, FunctionDef))
        let_stmt = main_func.body[0]
        wrap_expr = getattr(let_stmt, "value", None)
        assert isinstance(wrap_expr, ConstructorCall)
        resolved = getattr(wrap_expr, "_resolved_type", None)
        assert isinstance(resolved, UserType)
        assert resolved.name == "Wrapper"
        assert len(resolved.type_args) == 1
        # The key assertion: phantom param is a TypeVar, NOT AnyType
        assert not isinstance(resolved.type_args[0], AnyType), (
            f"Phantom type parameter leaked as AnyType: {resolved}"
        )
        assert isinstance(resolved.type_args[0], TypeVar), (
            f"Phantom type parameter should be a fresh TypeVar, got "
            f"{resolved.type_args[0]!r}"
        )

    def test_nullary_generic_constructor_with_annotation_works(self) -> None:
        """Zero-arg variants exercise the same `_check_constructor_call`
        path as phantom constructors and need the same fresh-TypeVar
        behavior to support annotated bindings."""
        source = """
        type Maybe[T] = Absent | Present(value: T)

        func main() -> Int
            let x: Maybe[Int] = Absent
            return 0
        end func
        """
        check(source)

    def test_nullary_generic_constructor_without_annotation_errors(self) -> None:
        """Without an annotation, a nullary generic constructor should still
        be rejected as underconstrained rather than silently inferred via
        AnyType."""
        source = """
        type Maybe[T] = Absent | Present(value: T)

        func main() -> Int
            let x = Absent
            return 0
        end func
        """
        err = expect_error(source)
        assert "Cannot infer" in str(err)
        assert "Maybe" in str(err)


# ---------------------------------------------------------------------------
# 2. The in_recovery flag on compatibility methods
# ---------------------------------------------------------------------------


class TestInRecoveryFlag:
    """``_types_structurally_compatible`` and ``_types_compatible_with_subs``
    now accept ``in_recovery``.  The default (``True``) is backwards
    compatible; setting it to ``False`` makes ``AnyType`` stop coercing."""

    def test_default_is_backward_compatible(self) -> None:
        checker = TypeChecker()
        # AnyType on either side -> True when the (default) in_recovery
        # flag is in effect.
        assert checker._types_structurally_compatible(
            IntType(), AnyType(), allow_typevar_wildcards=False
        )
        assert checker._types_structurally_compatible(
            AnyType(), IntType(), allow_typevar_wildcards=False
        )
        assert checker._types_compatible_with_subs(IntType(), AnyType(), {})
        assert checker._types_compatible_with_subs(AnyType(), IntType(), {})

    def test_in_recovery_false_rejects_anytype_on_actual(self) -> None:
        checker = TypeChecker()
        assert not checker._types_structurally_compatible(
            IntType(),
            AnyType(),
            allow_typevar_wildcards=False,
            in_recovery=False,
        )

    def test_in_recovery_false_rejects_anytype_on_expected(self) -> None:
        checker = TypeChecker()
        assert not checker._types_structurally_compatible(
            AnyType(),
            IntType(),
            allow_typevar_wildcards=False,
            in_recovery=False,
        )

    def test_in_recovery_false_rejects_anytype_in_subs_check(self) -> None:
        checker = TypeChecker()
        assert not checker._types_compatible_with_subs(
            IntType(), AnyType(), {}, in_recovery=False
        )

    def test_in_recovery_false_propagates_through_compound_recursion(
        self,
    ) -> None:
        """When the inner recursion is reached through a compound type
        (e.g. ``Option[AnyType]`` vs ``Option[Int]``), the flag must
        propagate — otherwise the seam is pointless."""
        checker = TypeChecker()
        nested_any = OptionType(AnyType())
        nested_int = OptionType(IntType())
        # default: coerces (in_recovery=True)
        assert checker._types_structurally_compatible(
            nested_int, nested_any, allow_typevar_wildcards=False
        )
        # tightened: does not coerce
        assert not checker._types_structurally_compatible(
            nested_int,
            nested_any,
            allow_typevar_wildcards=False,
            in_recovery=False,
        )

    def test_in_recovery_false_propagates_through_result(self) -> None:
        checker = TypeChecker()
        r_any = ResultType(AnyType(), StringType())
        r_int = ResultType(IntType(), StringType())
        assert checker._types_compatible_with_subs(r_int, r_any, {})
        assert not checker._types_compatible_with_subs(
            r_int, r_any, {}, in_recovery=False
        )

    def test_never_remains_universally_assignable_regardless_of_flag(
        self,
    ) -> None:
        """NeverType is the bottom type — it should remain assignable
        to any expected type regardless of ``in_recovery``.  ``Never``
        is a legitimate inhabitant of the type lattice, not an
        error-recovery escape hatch like ``AnyType``."""
        from geno.typechecker import NeverType

        checker = TypeChecker()
        assert checker._types_structurally_compatible(
            IntType(),
            NeverType(),
            allow_typevar_wildcards=False,
            in_recovery=False,
        )
        assert checker._types_compatible_with_subs(
            IntType(), NeverType(), {}, in_recovery=False
        )


# ---------------------------------------------------------------------------
# 3. Smoke tests for unchanged error-recovery behaviour
# ---------------------------------------------------------------------------


class TestErrorRecoveryUnchanged:
    """Error-recovery ``AnyType`` producers that already had a matching
    diagnostic still suppress cascades — we only fixed the silent
    producers (constructors with unbound type parameters).  Existing
    programs with a single real bug should still produce one error
    rather than an avalanche."""

    def test_undefined_identifier_reports_only_the_real_error(self) -> None:
        """``no_such_name + 1`` must report the undefined identifier,
        not also cascade into an arithmetic type error."""
        source = """
        func main() -> Int
            return no_such_name + 1
        end func
        """
        err = expect_error(source)
        # The primary error is the undefined name, not an arithmetic
        # cascade from the AnyType fallback.
        assert "Undefined" in str(err) or "no_such_name" in str(err)

    def test_unknown_field_does_not_cascade_to_return_type(self) -> None:
        """Accessing a missing field reports the field error; a
        subsequent return-type check against the recovery ``AnyType``
        should not also fire."""
        source = """
        type Point = MkPoint(x: Int, y: Int)
        func main() -> Int
            let p: Point = MkPoint(1, 2)
            return p.nonexistent
        end func
        """
        err = expect_error(source)
        msg = str(err)
        assert "nonexistent" in msg or "field" in msg.lower()

    def test_constructor_argument_recovery_does_not_cascade(self) -> None:
        """The constructor path still uses recovery-mode compatibility for an
        inner expression that already emitted a diagnostic, so the enclosing
        annotated binding should not add a second mismatch error."""
        source = """
        type Box[T] = Box(value: T)

        func main() -> Int
            let x: Box[Int] = Box(no_such_name)
            return 0
        end func
        """
        err = expect_error(source)
        msg = str(err)
        assert "Undefined" in msg or "no_such_name" in msg
