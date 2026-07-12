"""Tests for the effect typing system — Phase 2: parsing and AST representation."""

import pytest

from geno.ast_nodes import FunctionDef
from geno.parser import parse
from geno.typechecker import TypeChecker
from geno.types import FuncType


def _parse(source: str):
    return parse(source)


def _typecheck(source: str):
    program = _parse(source)
    checker = TypeChecker()
    checker.check_program(program)
    return checker


# ---------------------------------------------------------------------------
# Parsing effect annotations on function definitions
# ---------------------------------------------------------------------------


class TestFunctionDefEffectParsing:
    def test_no_effects(self):
        program = _parse("""
func add(a: Int, b: Int) -> Int
    example (1, 2) -> 3
    return a + b
end func add
""")
        func_def = program.definitions[0]
        assert isinstance(func_def, FunctionDef)
        assert func_def.effects == []

    def test_single_effect(self):
        program = _parse("""
func read_file(path: String) -> String with fs
    example "test.txt" -> "hello"
    return fs_read_text(path)
end func read_file
""")
        func_def = program.definitions[0]
        assert isinstance(func_def, FunctionDef)
        assert func_def.effects == ["fs"]

    def test_multiple_effects(self):
        program = _parse("""
func fetch_and_log(url: String) -> Unit with http, io
    example "http://example.com" -> ()
    return ()
end func fetch_and_log
""")
        func_def = program.definitions[0]
        assert isinstance(func_def, FunctionDef)
        assert func_def.effects == ["http", "io"]

    def test_three_effects(self):
        program = _parse("""
func do_all(x: Int) -> Unit with fs, http, io
    example 0 -> ()
    return ()
end func do_all
""")
        func_def = program.definitions[0]
        assert isinstance(func_def, FunctionDef)
        assert func_def.effects == ["fs", "http", "io"]


# ---------------------------------------------------------------------------
# Parsing effect annotations on function type annotations
# ---------------------------------------------------------------------------


class TestFunctionTypeEffectParsing:
    def test_function_type_no_effects(self):
        program = _parse("""
type Predicate = (Int) -> Bool
""")
        # This is a type alias
        assert len(program.definitions) == 1

    def test_function_type_with_effects(self):
        program = _parse("""
type FileReader = (String) -> String with fs
""")
        assert len(program.definitions) == 1

    def test_function_param_with_effects(self):
        """Function that takes a callback and has effects on itself."""
        program = _parse("""
func identity(x: Int) -> Int
    example 1 -> 1
    return x
end func identity

func run_with_log(f: (Int) -> Int, x: Int) -> Int with io
    example (identity, 1) -> 1
    return f(x)
end func run_with_log
""")
        func_def = program.definitions[1]
        assert isinstance(func_def, FunctionDef)
        assert func_def.effects == ["io"]


# ---------------------------------------------------------------------------
# Typechecker integration — effect resolution
# ---------------------------------------------------------------------------


class TestEffectResolution:
    def test_pure_function_typechecks(self):
        checker = _typecheck("""
func add(a: Int, b: Int) -> Int
    example (1, 2) -> 3
    return a + b
end func add
""")
        ft = checker.global_env.lookup("add")
        assert isinstance(ft, FuncType)
        assert ft.effects == frozenset()

    def test_annotated_effects_in_signature(self):
        checker = _typecheck("""
func greet(name: String) -> Unit with io
    example "world" -> ()
    return ()
end func greet
""")
        ft = checker.global_env.lookup("greet")
        assert isinstance(ft, FuncType)
        assert ft.effects == frozenset({"io"})

    def test_multiple_annotated_effects_in_signature(self):
        checker = _typecheck("""
func do_stuff(x: Int) -> Unit with fs, http
    example 0 -> ()
    return ()
end func do_stuff
""")
        ft = checker.global_env.lookup("do_stuff")
        assert isinstance(ft, FuncType)
        assert ft.effects == frozenset({"fs", "http"})

    def test_unknown_effect_raises_error(self):
        with pytest.raises(Exception, match="Unknown effect 'banana'"):
            _typecheck("""
func bad(x: Int) -> Int with banana
    example 0 -> 0
    return x
end func bad
""")

    def test_function_type_annotation_carries_effects(self):
        """Function type in a type alias resolves effects."""
        checker = _typecheck("""
func identity(x: Int) -> Int
    example 5 -> 5
    return x
end func identity

func apply(f: (Int) -> Int, x: Int) -> Int
    example (identity, 5) -> 5
    return f(x)
end func apply
""")
        ft = checker.global_env.lookup("apply")
        assert isinstance(ft, FuncType)
        # The function itself is pure; the callback parameter is also pure
        assert ft.effects == frozenset()
