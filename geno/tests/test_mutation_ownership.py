"""Tests for the owned-collection analysis of proposal 0019 (section 3).

``geno.mutation_ownership.owned_names`` decides which names a function or
lambda owns: every collection the name holds was allocated by the unit, and
none can be reached through other storage while the unit runs. The checker
does not use it yet (proposal 0019, Package 2), so these tests exercise it
directly on type-checked programs. Programs that cannot type-check (an operand
of ``?`` or ``await`` that is a collection, say) are walked after
``_resolve_like_checker`` marks builtin calls the way the checker would.
"""

from __future__ import annotations

import dataclasses
import inspect
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from geno import ast_nodes
from geno.ast_nodes import (
    Expression,
    ExpressionStatement,
    FunctionCall,
    FunctionDef,
    Identifier,
    ImplDef,
    LambdaExpr,
    Pattern,
    Program,
    Statement,
)
from geno.builtin_registry import build_builtin_signatures
from geno.lexer import LexerError
from geno.mutation_ownership import (
    _EXPRESSION_HANDLERS,
    _PATTERN_HANDLERS,
    _STATEMENT_HANDLERS,
    _OwnershipWalker,
    function_owned_names,
    is_fresh_expression,
    lambda_owned_names,
    owned_names,
)
from geno.parser import parse
from geno.parser_base import ParseError, ParseErrors
from geno.typechecker import TypeChecker

_REPO_ROOT = Path(__file__).resolve().parents[2]

_PRELUDE = """\
type Box = Box(items: Array[Int])
type P = P(x: Int)

func helper(a: Array[Int]) -> Int
    example array_new(1, 0) -> 1
    return array_length(a)
end func

func weigh(k: Int, a: Array[Int]) -> Int
    example 2, array_new(1, 0) -> 2
    return k * array_length(a)
end func
"""


def _checked(source: str) -> Program:
    program = parse(source)
    TypeChecker().check_program(program)
    return program


def _function(program: Program, name: str) -> FunctionDef:
    for definition in program.definitions:
        if isinstance(definition, FunctionDef) and definition.name == name:
            return definition
    raise AssertionError(f"no function {name}")


def _with_body(body: str) -> str:
    """A program whose `f(n: Int, param: Array[Int]) -> Int` runs *body*."""
    return (
        _PRELUDE
        + "\nfunc f(n: Int, param: Array[Int]) -> Int\n"
        + "    example 1, array_new(1, 0) -> 0\n"
        + textwrap.indent(textwrap.dedent(body).strip("\n"), "    ")
        + "\nend func\n"
    )


def _owned_in(body: str) -> frozenset[str]:
    return function_owned_names(_function(_checked(_with_body(body)), "f"))


def _owned(source: str, name: str = "f") -> frozenset[str]:
    return function_owned_names(_function(_checked(source), name))


def _walk(value: Any) -> Iterator[Any]:
    """Every AST node and helper record reachable through public fields."""
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk(item)
        return
    if not dataclasses.is_dataclass(value) or isinstance(value, type):
        return
    yield value
    for field in dataclasses.fields(value):
        if field.name.startswith("_") or field.name == "location":
            continue
        yield from _walk(getattr(value, field.name))


def _lambdas(node: Any) -> list[LambdaExpr]:
    return [item for item in _walk(node) if isinstance(item, LambdaExpr)]


def _resolve_like_checker(program: Program) -> Program:
    """Mark builtin calls as the checker would, for programs it rejects."""
    builtins = build_builtin_signatures()
    for node in _walk(program):
        if (
            isinstance(node, FunctionCall)
            and isinstance(node.function, Identifier)
            and node.function.name in builtins
        ):
            node._resolved_builtin_name = node.function.name
    return program


def _owned_unchecked_in(body: str) -> frozenset[str]:
    program = _resolve_like_checker(parse(_with_body(body)))
    return function_owned_names(_function(program, "f"))


# ---------------------------------------------------------------------------
# Permitted positions (P1-P6)
# ---------------------------------------------------------------------------


class TestPermittedPositions:
    def test_write_root_index_assignment(self):
        assert _owned_in("""
            var a = array_new(n, 0)
            a[0] = 1
            return 0
        """) == frozenset({"a"})

    def test_write_root_field_write_into_an_element(self):
        assert _owned_in("""
            var ps = array_new(2, P(x: 0))
            ps[0].x = 5
            return ps[0].x
        """) == frozenset({"ps"})

    def test_write_root_mutable_map_index_assignment(self):
        assert _owned_in("""
            var m: MutableMap[String, Int] = mutable_map_new()
            m["k"] = 1
            return 0
        """) == frozenset({"m"})

    def test_index_read(self):
        assert _owned_in("""
            var a = array_new(3, 7)
            let y = a[1]
            return y + a[a[0]]
        """) == frozenset({"a"})

    def test_borrowed_builtin_arguments_by_position(self):
        assert _owned_in("""
            var a = array_new(3, 0)
            array_fill(a, 2)
            let k = array_length(a) + length(a)
            let xs = array_to_list(a)
            let c = array_copy(a)
            return k + array_get(a, 0) + length(xs) + array_length(c)
        """) == frozenset({"a"})

    def test_borrowed_builtin_arguments_by_name(self):
        assert _owned_in("""
            var a = array_new(3, 0)
            array_set(array: a, index: 0, value: 1)
            array_fill(value: 2, array: a)
            var m: MutableMap[String, Int] = mutable_map_new()
            mutable_map_set(map: m, key: "k", value: 1)
            return array_get(index: 0, array: a)
        """) == frozenset({"a", "m"})

    def test_both_operands_of_set_union_and_intersection(self):
        assert _owned_in("""
            var s1: Set[Int] = set_from_list([1, 2])
            var s2: Set[Int] = set_new()
            var u: Set[Int] = set_union(s1, s2)
            var i: Set[Int] = set_intersection(u, s1)
            i.add(3)
            return set_size(u)
        """) == frozenset({"s1", "s2", "u", "i"})

    def test_membership_container(self):
        assert _owned_in("""
            var s: Set[Int] = set_new()
            set_add(s, 1)
            var m: MutableMap[String, Int] = mutable_map_new()
            m["k"] = 1
            if 1 in s and 2 not in s and "k" in m then
                return 1
            end if
            return 0
        """) == frozenset({"s", "m"})

    def test_for_iterable(self):
        assert _owned_in("""
            var a = array_new(3, 1)
            var total = 0
            for x in a do
                total = total + x
            end for
            a[0] = total
            return total
        """) == frozenset({"a"})

    def test_comprehension_iterable(self):
        # The checker only comprehends lists; the position is still P5.
        assert _owned_unchecked_in("""
            var a = array_new(3, 1)
            let doubled = [x * 2 for x in a]
            return 0
        """) == frozenset({"a"})

    def test_return_positions(self):
        source = """\
type Box = Box(items: Array[Int])

func plain(n: Int) -> Array[Int]
    example 1 -> array_new(1, 1)
    var a = array_new(n, 0)
    a[0] = 1
    return a
end func

func pair(n: Int) -> (Array[Int], Int)
    example 1 -> (array_new(1, 1), 1)
    var a = array_new(n, 0)
    a[0] = 1
    return (a, n)
end func

func listed(n: Int) -> List[(Array[Int], Int)]
    example 1 -> [(array_new(1, 1), 1)]
    var a = array_new(n, 0)
    a[0] = 1
    return [(a, n)]
end func

func boxed(n: Int) -> Box
    example 1 -> Box(items: array_new(1, 1))
    var a = array_new(n, 0)
    a[0] = 1
    return Box(items: a)
end func

func maybe(n: Int) -> Option[Array[Int]]
    example 1 -> Some(array_new(1, 1))
    var a = array_new(n, 0)
    a[0] = 1
    return Some(a)
end func

func branch(n: Int) -> Array[Int]
    example 1 -> array_new(1, 1)
    var a = array_new(n, 0)
    a[0] = 1
    return if n > 0 then a else array_new(0, 0) end if
end func

func picked(n: Int) -> Array[Int]
    example 1 -> array_new(1, 1)
    var a = array_new(n, 0)
    a[0] = 1
    return match n with
    | 0 -> array_new(0, 0)
    | _ -> a
    end match
end func
"""
        program = _checked(source)
        for name in ("plain", "pair", "listed", "boxed", "maybe", "branch", "picked"):
            assert function_owned_names(_function(program, name)) == frozenset({"a"}), (
                name
            )

    def test_return_inside_a_loop_and_a_match_statement(self):
        assert _owned("""\
func f(n: Int) -> Array[Int]
    example 1 -> array_new(1, 1)
    var a = array_new(n, 0)
    for i in range(n) do
        a[i] = 1
        if i == 2 then
            return a
        end if
    end for
    match n with
    | 0 -> return a
    | _ -> a[0] = 2
    end match
    return a
end func
""") == frozenset({"a"})

    def test_method_spellings(self):
        assert _owned_in("""
            var v: Vec[Int] = vec_new()
            v.push(1)
            v.set(index: 0, value: 2)
            let popped = v.pop()
            var a = array_new(3, 0)
            var b = a.copy()
            b[0] = a.length() + b.length()
            var s: Set[Int] = set_new()
            s.add(1)
            s.remove(1)
            var m: MutableMap[String, Int] = mutable_map_new()
            m.set("k", 1)
            m.delete("k")
            return v.length() + b[0] + s.size() + m.size() + length(m.keys())
        """) == frozenset({"v", "a", "b", "s", "m"})

    def test_vec_written_through_builtins(self):
        assert _owned_in("""
            var v: Vec[Int] = vec_from_list([1, 2])
            vec_set(vec: v, index: 0, value: 5)
            vec_push(v, 6)
            return vec_get(v, 0) + length(vec_to_list(v))
        """) == frozenset({"v"})

    def test_every_allocating_builtin_is_fresh(self):
        assert _owned_in("""
            var a = array_new(2, 0)
            var b = array_from_list([1, 2])
            var c = array_copy(param)
            var v: Vec[Int] = vec_new()
            var w: Vec[Int] = vec_from_list([1])
            var s: Set[Int] = set_new()
            var t: Set[Int] = set_from_list([1])
            var u: Set[Int] = set_union(s, t)
            var i: Set[Int] = set_intersection(s, t)
            var m: MutableMap[String, Int] = mutable_map_new()
            a[0] = 1
            b[0] = 1
            c[0] = 1
            return 0
        """) == frozenset({"a", "b", "c", "v", "w", "s", "t", "u", "i", "m"})

    def test_only_the_root_of_a_table_is_fresh(self):
        # `array_new(2, param)` stores `param` in both slots. The table itself
        # is owned; a write below the root is the checker's to charge (L3).
        assert _owned_in("""
            var g = array_new(2, param)
            g[0][0] = 1
            return 0
        """) == frozenset({"g"})


# ---------------------------------------------------------------------------
# Escapes (O5)
# ---------------------------------------------------------------------------


_ESCAPES = {
    "user function argument": "let k = helper(a)",
    "non-borrowed builtin parameter": "let s = to_string(a)",
    "builtin default element": "var g = array_new(2, a)",
    "let initializer": "let b = a",
    "var initializer": "var b = a",
    "assignment right side": "var b = array_new(1, 0)\nb = a",
    "value stored by an index write": "var rows = array_new(2, array_new(1, 0))\nrows[0] = a",
    "value stored by a field write": "var bx = Box(items: array_new(1, 0))\nbx.items = a",
    "tuple not returned": "let t = (a, 1)",
    "list not returned": "let l = [a]",
    "constructor not returned": "let bx = Box(items: a)",
    "map literal": 'let m = {"k": a}',
    "with update": "let bx = Box(items: param)\nlet b2 = bx with (items: a)",
    "pipeline input": "let k = a |> array_length",
    "pipeline stage argument": "let k = 2 |> weigh(_, a)",
    "match scrutinee": "let k = match a with\n| _ -> 0\nend match",
    "if expression value": "let b = if n > 0 then a else param end if",
    "branch passed to a borrowing builtin": (
        "let k = array_length(if n > 0 then a else param end if)"
    ),
    "f-string interpolation": 'let s = f"{a}"',
    "equality operand": "let same = a == param",
    "comprehension element": "let l = [a for x in [1, 2]]",
    "returned through a user function": "return helper(a)",
    "closure reading it": "let g = fn(i: Int) -> a[i]",
    "closure writing it": "let g = fn(i: Int) do\na[i] = 1\nreturn i\nend fn",
    "expression statement": "a",
}

_UNCHECKED_ESCAPES = {
    "propagation operand": "let k = a?",
    "await operand": "let k = await a",
    "callee": "let k = a(1)",
    "field access target": "let k = a.size",
    "unary operand": "let k = not a",
    "index expression": "let k = param[a]",
    "typed hole constraint": "let k: Int = ?h: Int where helper(a) > 0",
    "positional argument after a named one": "array_fill(value: 0, a)",
}


class TestEscapes:
    @pytest.mark.parametrize("snippet", list(_ESCAPES.values()), ids=list(_ESCAPES))
    def test_escape_disqualifies(self, snippet: str):
        owned = _owned_in(f"var a = array_new(3, 0)\na[0] = 1\n{snippet}\nreturn 0")
        assert "a" not in owned

    @pytest.mark.parametrize(
        "snippet", list(_UNCHECKED_ESCAPES.values()), ids=list(_UNCHECKED_ESCAPES)
    )
    def test_escape_disqualifies_in_programs_the_checker_rejects(self, snippet: str):
        body = f"var a = array_new(3, 0)\na[0] = 1\n{snippet}\nreturn 0"
        # Without the escape the same walk owns `a`, so the test is not vacuous.
        assert _owned_unchecked_in("var a = array_new(3, 0)\nreturn 0") == {"a"}
        assert "a" not in _owned_unchecked_in(body)

    def test_a_stored_collection_does_not_disqualify_its_container(self):
        assert _owned_in("""
            var a = array_new(3, 0)
            var rows: Vec[Array[Int]] = vec_new()
            vec_push(rows, a)
            return 0
        """) == frozenset({"rows"})


# ---------------------------------------------------------------------------
# Ownership rules O0-O4
# ---------------------------------------------------------------------------


class TestOwnershipRules:
    def test_o0_a_name_a_lambda_captures_is_not_owned_by_the_lambda(self):
        program = _checked(
            _with_body("""
                var a = array_new(3, 0)
                let read = fn(i: Int) -> a[i]
                let write = fn(i: Int) do
                    a[i] = 1
                    return a[i]
                end fn
                return read(0) + write(1)
            """)
        )
        f = _function(program, "f")
        # O4 for the enclosing function, O0 for each lambda: the lambdas only
        # use `a` in permitted positions, yet never declare it.
        assert function_owned_names(f) == frozenset()
        lambdas = _lambdas(f)
        assert len(lambdas) == 2
        for lam in lambdas:
            assert lambda_owned_names(lam) == frozenset()

    def test_o0_a_module_constant_is_never_owned(self):
        assert not _owned("""\
let table = [1, 2, 3]

func f(n: Int) -> Int
    example 2 -> 1
    var hits = 0
    for x in table do
        if x == n and n in table then
            hits = hits + table[0]
        end if
    end for
    return hits
end func
""")

    def test_o0_names_without_a_fresh_var_are_never_owned(self):
        owned = _owned_in("""
            let l = array_new(3, 0)
            var c = 0
            c = c + 1
            var alias = param
            alias[0] = l[0]
            return c
        """)
        assert owned == frozenset()

    def test_o1_a_parameter_is_never_owned(self):
        assert not _owned_in("""
            var param = param
            param[0] = 1
            return 0
        """)
        # Redeclaring the parameter from a fresh copy still shares its name.
        assert not _owned_in("""
            var param = array_copy(param)
            param[0] = 1
            return 0
        """)

    def test_o1_a_lambda_parameter_is_never_owned_by_the_lambda(self):
        program = _checked(
            _with_body("""
                let g = fn(xs: Array[Int]) do
                    var xs = array_copy(xs)
                    xs[0] = 1
                    return 0
                end fn
                return g(param)
            """)
        )
        (lam,) = _lambdas(_function(program, "f"))
        assert lambda_owned_names(lam) == frozenset()

    @pytest.mark.parametrize(
        "declaration",
        [
            "let a = array_new(3, 0)",
            "var (a, k) = (array_new(1, 0), 1)",
            "for a in [1, 2] do\nlet z = 0\nend for",
            "let k = match n with\n| a -> 0\nend match",
            "let k = match [n] with\n| [...a] -> 0\nend match",
        ],
        ids=["let", "destructure", "for variable", "pattern variable", "rest pattern"],
    )
    def test_o2_every_declaration_must_be_a_fresh_var(self, declaration: str):
        owned = _owned_in(f"var a = array_new(3, 0)\na[0] = 1\n{declaration}\nreturn 0")
        assert "a" not in owned

    def test_o2_a_comprehension_variable_disqualifies(self):
        assert "a" not in _owned_unchecked_in("""
            var a = array_new(3, 0)
            let l = [0 for a in [1, 2]]
            return 0
        """)

    def test_o3_assignments_must_be_fresh(self):
        assert _owned_in("""
            var a = array_new(3, 0)
            a = array_new(5, 1)
            a = array_copy(a)
            a[0] = 1
            return 0
        """) == frozenset({"a"})
        assert not _owned_in("""
            var a = array_new(3, 0)
            a = param
            return 0
        """)

    def test_o4_a_declaration_in_a_nested_lambda_disqualifies_the_name(self):
        program = _checked(
            _with_body("""
                var a = array_new(1, 0)
                a[0] = 1
                let g = fn() do
                    var a = array_new(2, 0)
                    a[0] = 2
                    return a[0]
                end fn
                return g()
            """)
        )
        f = _function(program, "f")
        assert function_owned_names(f) == frozenset()
        (lam,) = _lambdas(f)
        assert lambda_owned_names(lam) == frozenset({"a"})

    def test_o4_a_lambda_parameter_of_the_same_name_disqualifies(self):
        assert not _owned_in("""
            var a = array_new(1, 0)
            a[0] = 1
            let g = fn(a: Int) -> a + 1
            return g(1)
        """)

    def test_o4_a_nested_lambda_inside_a_lambda(self):
        program = _checked(
            _with_body("""
                let outer = fn(k: Int) do
                    var a = array_new(k, 0)
                    let inner = fn(i: Int) -> a[i]
                    a[0] = 1
                    return inner(0)
                end fn
                return outer(1)
            """)
        )
        lambdas = _lambdas(_function(program, "f"))
        outer = next(lam for lam in lambdas if lam.block_body is not None)
        assert lambda_owned_names(outer) == frozenset()

    def test_same_block_redeclaration_in_both_orders(self):
        assert not _owned_in("""
            var a = array_new(3, 0)
            var a = param
            a[0] = 1
            return 0
        """)
        assert not _owned_in("""
            var a = param
            var a = array_new(3, 0)
            a[0] = 1
            return 0
        """)
        assert _owned_in("""
            var a = array_new(3, 0)
            var a = array_new(4, 0)
            a[0] = 1
            return 0
        """) == frozenset({"a"})

    def test_nested_block_shadow(self):
        assert not _owned_in("""
            var a = array_new(3, 0)
            if n > 0 then
                var a = param
                a[0] = 1
            end if
            a[0] = 2
            return 0
        """)
        assert _owned_in("""
            var a = array_new(3, 0)
            if n > 0 then
                var a = array_new(1, 0)
                a[0] = 1
            end if
            a[0] = 2
            return 0
        """) == frozenset({"a"})

    def test_flow_insensitive_a_later_assignment_disqualifies_an_earlier_write(self):
        assert not _owned_in("""
            var a = array_new(3, 0)
            a[0] = 1
            a = param
            return 0
        """)

    def test_flow_insensitive_a_reassignment_in_a_loop(self):
        assert not _owned_in("""
            var a = array_new(3, 0)
            var i = 0
            while i < n do
                a[0] = i
                a = param
                i = i + 1
            end while
            return 0
        """)

    def test_a_top_level_function_named_array_new_is_not_fresh(self):
        assert not _owned("""\
func array_new(size: Int, default: Int) -> Array[Int]
    example 1, 0 -> array_from_list([0])
    return array_from_list([default])
end func

func f(n: Int) -> Int
    example 1 -> 1
    var a = array_new(n, 0)
    a[0] = 1
    return a[0]
end func
""")

    def test_a_local_binding_named_array_new_is_not_fresh(self):
        # Checker-only: the JavaScript backend refuses a local binding named
        # after a runtime builtin, while the interpreter and Python accept it.
        assert not _owned_in("""
            let array_new = fn(k: Int, d: Int) -> array_from_list([d])
            var a = array_new(3, 0)
            a[0] = 1
            return 0
        """)

    def test_a_lambda_unit_owns_its_own_collection(self):
        program = _checked(
            _with_body("""
                let build = fn(k: Int) do
                    var a = array_new(k, 0)
                    a[0] = 1
                    return a
                end fn
                let size = fn(k: Int) -> k + 1
                return build(2)[0] + size(1)
            """)
        )
        f = _function(program, "f")
        assert function_owned_names(f) == frozenset()
        build, size = _lambdas(f)
        assert lambda_owned_names(build) == frozenset({"a"})
        # An expression body declares nothing, so it owns nothing.
        assert lambda_owned_names(size) == frozenset()

    def test_async_function_and_impl_method(self):
        program = _checked("""\
trait Sized
    func size(self: Self) -> Int
end trait

type Bag = Bag(n: Int)

impl Sized for Bag
    func size(self: Bag) -> Int
        example Bag(n: 2) -> 2
        var a = array_new(self.n, 1)
        a[0] = 1
        return array_length(a)
    end func
end impl

async func fetch(n: Int) -> Int
    example 1 -> 1
    var a = array_new(n, 0)
    a[0] = n
    return a[0]
end func
""")
        assert function_owned_names(_function(program, "fetch")) == frozenset({"a"})
        impl = next(d for d in program.definitions if isinstance(d, ImplDef))
        (method,) = impl.methods
        assert function_owned_names(method) == frozenset({"a"})

    def test_owned_names_accepts_an_expression_body(self):
        program = _checked(_with_body("let g = fn(k: Int) -> k\nreturn g(1)"))
        (lam,) = _lambdas(_function(program, "f"))
        assert lam.body is not None
        assert owned_names(lam.body, lam.params) == frozenset()


class TestFreshExpressions:
    @staticmethod
    def _initializers(program: Program) -> list[Expression]:
        return [
            stmt.value
            for stmt in _function(program, "f").body
            if isinstance(stmt, ast_nodes.VarStatement)
        ]

    def test_fresh_requires_a_resolved_allocating_builtin(self):
        program = _checked(
            _with_body("""
                var a = array_new(3, 0)
                var b = array_to_list(a)
                return 0
            """)
        )
        first, second = self._initializers(program)
        assert is_fresh_expression(first)
        assert not is_fresh_expression(second)

    def test_an_unresolved_call_is_not_fresh(self):
        program = parse(_with_body("var a = array_new(3, 0)\nreturn 0"))
        (call,) = self._initializers(program)
        assert not is_fresh_expression(call)

    def test_a_call_retargeted_after_resolution_is_not_fresh(self):
        program = _checked(_with_body("var a = array_new(3, 0)\nreturn 0"))
        (call,) = self._initializers(program)
        assert isinstance(call, FunctionCall)
        assert is_fresh_expression(call)
        call.function = Identifier(location=call.function.location, name="helper")
        assert not is_fresh_expression(call)


# ---------------------------------------------------------------------------
# Completeness and fail-closed behaviour
# ---------------------------------------------------------------------------


def _concrete_subclasses(base: type) -> set[type]:
    return {
        cls
        for _name, cls in inspect.getmembers(ast_nodes, inspect.isclass)
        if issubclass(cls, base) and cls is not base
    }


class TestCompleteness:
    def test_every_statement_class_has_a_handler(self):
        classes = _concrete_subclasses(Statement)
        assert ast_nodes.IndexAssignStatement in classes
        assert set(_STATEMENT_HANDLERS) == classes

    def test_every_expression_class_has_a_handler(self):
        classes = _concrete_subclasses(Expression)
        assert ast_nodes.LambdaExpr in classes
        assert set(_EXPRESSION_HANDLERS) == classes

    def test_every_pattern_class_has_a_handler(self):
        classes = _concrete_subclasses(Pattern)
        assert ast_nodes.RestPattern in classes
        assert set(_PATTERN_HANDLERS) == classes

    def test_an_unknown_node_makes_the_unit_own_nothing(self):
        @dataclasses.dataclass
        class Novel(Expression):
            def accept(self, visitor: Any) -> Any:
                return None

        program = _checked(_with_body("var a = array_new(3, 0)\na[0] = 1\nreturn 0"))
        f = _function(program, "f")
        assert function_owned_names(f) == frozenset({"a"})
        location = f.body[0].location
        f.body.insert(
            1, ExpressionStatement(location=location, expression=Novel(location))
        )
        assert function_owned_names(f) == frozenset()

    def test_repository_programs_walk_without_unknown_nodes(self):
        """Every unit in the examples, the stdlib and selfhost walks completely.

        With builtin calls marked as the checker would, each owned name is
        also declared by a `var` in its unit and is never a parameter (O0, O1).
        """
        paths = sorted(
            [
                *(_REPO_ROOT / "examples").rglob("*.geno"),
                *(_REPO_ROOT / "geno" / "std").glob("*.geno"),
                *(_REPO_ROOT / "selfhost").rglob("*.geno"),
            ]
        )
        assert paths
        units = 0
        owned_total = 0
        for path in paths:
            try:
                program = parse(path.read_text(encoding="utf-8"), str(path))
            except (LexerError, ParseError, ParseErrors):
                continue  # deliberately invalid examples
            _resolve_like_checker(program)
            functions = [d for d in program.definitions if isinstance(d, FunctionDef)]
            for impl in program.definitions:
                if isinstance(impl, ImplDef):
                    functions.extend(impl.methods)
            for defn in functions:
                bodies: list[tuple[Any, list[Any]]] = [(defn.body, defn.params)]
                for lam in _lambdas(defn.body):
                    body = lam.block_body if lam.block_body is not None else lam.body
                    bodies.append((body, lam.params))
                for body, params in bodies:
                    walker = _OwnershipWalker()
                    if isinstance(body, Expression):
                        walker.expression(body, returned=True)
                    else:
                        walker.block(body, returned=True)
                    assert not walker._incomplete, f"{path}: {defn.name}"
                    owned = owned_names(body, params)
                    declared = {
                        node.name
                        for node in _walk(body)
                        if isinstance(node, ast_nodes.VarStatement)
                    }
                    assert owned <= declared, f"{path}: {defn.name}"
                    assert not owned & {param.name for param in params}
                    units += 1
                    owned_total += len(owned)
        assert units > 100
        assert owned_total > 0
