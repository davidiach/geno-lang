"""
Regressions for reported language-surface defects
=================================================

Each class covers one reported issue:

* #69 — the default sandboxed ``geno run`` hid the real compile diagnostic
  behind "the isolated frontend failed safely (CompileError)".
* #70 — ``geno check``/``geno test`` accepted reserved runtime names that the
  default ``geno run`` rejects, so a suite could be green while the program
  could not run at all.
* #71 — ``example -> value`` was a parse error for zero-argument functions even
  though examples are mandatory.
* #78 — a leading ``|`` before the first ADT variant was rejected, which is what
  actually broke idiomatic multiline sum types.
* #79 — ``| (a, b) ->`` did not parse, so tuples could only be let-destructured.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from geno.ast_nodes import (
    FunctionDef,
    MatchStatement,
    TupleExpr,
    TuplePattern,
    TypeDef,
    VariablePattern,
)
from geno.parser import ParseError, ParseErrors, parse
from geno.test_runner import run_test_suite
from geno.typechecker import TypeChecker
from geno.typechecker import TypeError as GenoTypeError


def _run_geno(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "geno", *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _write(tmp_path: Path, source: str, name: str = "Main.geno") -> Path:
    path = tmp_path / name
    path.write_text(source)
    return path


def _check(source: str) -> None:
    TypeChecker().check_program(parse(source))


# ---------------------------------------------------------------------------
# #78 — leading '|' before the first ADT variant
# ---------------------------------------------------------------------------


class TestLeadingBarTypeDefinitions:
    def test_leading_bar_before_first_variant_parses(self):
        program = parse(
            "type Tx =\n"
            "    | Deposit(amount: Int)\n"
            "    | Withdraw(amount: Int)\n"
            "    | Audit\n"
        )
        type_def = program.definitions[0]
        assert isinstance(type_def, TypeDef)
        assert [variant.name for variant in type_def.variants] == [
            "Deposit",
            "Withdraw",
            "Audit",
        ]

    def test_leading_bar_matches_single_line_form(self):
        multiline = parse("type Color =\n    | Red\n    | Green\n").definitions[0]
        single_line = parse("type Color = Red | Green\n").definitions[0]
        assert isinstance(multiline, TypeDef)
        assert isinstance(single_line, TypeDef)
        assert [v.name for v in multiline.variants] == [
            v.name for v in single_line.variants
        ]

    @pytest.mark.parametrize(
        "source",
        [
            "type Coordinate = Tuple[Int, Int]\n",
            "type Predicate = (Int) -> Bool\n",
            "type Name = String\n",
        ],
    )
    def test_type_aliases_still_parse(self, source: str):
        # A leading bar settles the alias-vs-ADT disambiguation early, so the
        # alias forms must keep reaching their own branches.
        program = parse(source)
        assert not isinstance(program.definitions[0], TypeDef)

    def test_dangling_leading_bar_is_a_parse_error(self):
        with pytest.raises((ParseError, ParseErrors)):
            parse("type Bad = |\n")


# ---------------------------------------------------------------------------
# #71 — `example -> value` for zero-argument functions
# ---------------------------------------------------------------------------


class TestZeroArgExampleClause:
    def test_bare_arrow_example_parses(self):
        program = parse(
            'func usage() -> String\n    example -> "hi"\n    return "hi"\nend func\n'
        )
        function = program.definitions[0]
        assert isinstance(function, FunctionDef)
        example = function.specs.examples[0]
        assert isinstance(example.input_expr, TupleExpr)
        assert example.input_expr.elements == []

    def test_bare_arrow_matches_parenthesized_form(self):
        bare = parse(
            "func f() -> Int\n    example -> 1\n    return 1\nend func\n"
        ).definitions[0]
        parens = parse(
            "func f() -> Int\n    example () -> 1\n    return 1\nend func\n"
        ).definitions[0]
        assert isinstance(bare, FunctionDef)
        assert isinstance(parens, FunctionDef)
        assert type(bare.specs.examples[0].input_expr) is type(
            parens.specs.examples[0].input_expr
        )
        assert (
            bare.specs.examples[0].input_expr.elements
            == parens.specs.examples[0].input_expr.elements
        )

    def test_bare_arrow_still_arity_checked(self):
        # The bare form means "no input", so it must not silently satisfy a
        # function that does take a parameter.
        with pytest.raises(GenoTypeError):
            _check(
                "func double(n: Int) -> Int\n"
                "    example -> 2\n"
                "    return n * 2\n"
                "end func\n"
            )


# ---------------------------------------------------------------------------
# #79 — tuple patterns in match arms
# ---------------------------------------------------------------------------


_TUPLE_MATCH_SOURCE = """\
func pick(p: (Int, String)) -> String
    example (1, "a") -> "a"
    match p with
        | (a, b) -> return b
    end match
end func
"""


class TestTuplePatterns:
    def test_tuple_pattern_parses(self):
        program = parse(_TUPLE_MATCH_SOURCE)
        function = program.definitions[0]
        assert isinstance(function, FunctionDef)
        statement = function.body[0]
        assert isinstance(statement, MatchStatement)
        pattern = statement.arms[0].pattern
        assert isinstance(pattern, TuplePattern)
        assert all(isinstance(e, VariablePattern) for e in pattern.elements)

    def test_tuple_pattern_typechecks(self):
        _check(_TUPLE_MATCH_SOURCE)

    def test_all_catchall_tuple_pattern_is_exhaustive(self):
        # Every value of a tuple type has that type's arity, so one all-catchall
        # arm covers everything and must not demand a default arm.
        _check(_TUPLE_MATCH_SOURCE)

    def test_finite_element_types_get_full_case_analysis(self):
        _check(
            "func classify(p: (Bool, Bool)) -> String\n"
            '    example (true, true) -> "both"\n'
            "    match p with\n"
            '        | (true, true) -> return "both"\n'
            '        | (true, false) -> return "first"\n'
            '        | (false, true) -> return "second"\n'
            '        | (false, false) -> return "neither"\n'
            "    end match\n"
            "end func\n"
        )

    def test_missing_tuple_case_is_reported_with_a_witness(self):
        with pytest.raises(GenoTypeError) as excinfo:
            _check(
                "func classify(p: (Bool, Bool)) -> String\n"
                '    example (true, true) -> "x"\n'
                "    match p with\n"
                '        | (true, true) -> return "x"\n'
                '        | (false, false) -> return "y"\n'
                "    end match\n"
                "end func\n"
            )
        message = str(excinfo.value)
        assert "Non-exhaustive" in message
        assert "(true, false)" in message

    def test_tuple_pattern_nested_in_constructor(self):
        _check(
            "func total(o: Option[(Int, Int)]) -> Int\n"
            "    example Some((2, 3)) -> 5\n"
            "    example None -> 0\n"
            "    match o with\n"
            "        | Some((a, b)) -> return a + b\n"
            "        | None -> return 0\n"
            "    end match\n"
            "end func\n"
        )

    def test_arity_mismatch_is_rejected(self):
        with pytest.raises(GenoTypeError) as excinfo:
            _check(
                "func pick(p: (Int, String)) -> String\n"
                '    example (1, "a") -> "a"\n'
                "    match p with\n"
                "        | (a, b, c) -> return b\n"
                "    end match\n"
                "end func\n"
            )
        assert "Tuple pattern expects 2 elements" in str(excinfo.value)

    def test_tuple_pattern_on_non_tuple_type_is_rejected(self):
        with pytest.raises(GenoTypeError) as excinfo:
            _check(
                "func pick(n: Int) -> String\n"
                '    example 1 -> "a"\n'
                "    match n with\n"
                '        | (a, b) -> return "a"\n'
                "    end match\n"
                "end func\n"
            )
        assert "non-tuple type" in str(excinfo.value)

    def test_single_element_tuple_pattern_is_rejected_with_guidance(self):
        with pytest.raises(ParseErrors) as excinfo:
            parse(
                "func pick(p: (Int, String)) -> String\n"
                '    example (1, "a") -> "a"\n'
                "    match p with\n"
                '        | (a) -> return "a"\n'
                "    end match\n"
                "end func\n"
            )
        assert "at least two elements" in str(excinfo.value.errors[0])

    def test_list_pattern_does_not_match_a_tuple_in_the_interpreter(self):
        # Tuples and lists share a representation on the JS backend, so the
        # interpreter must keep them apart on its own.
        from geno.ast_nodes import ListPattern
        from geno.interpreter import Interpreter

        interpreter = Interpreter()
        pattern = ListPattern(location=None, elements=[])
        assert interpreter._match_pattern(pattern, ()) is None


# ---------------------------------------------------------------------------
# #69 / #70 — reserved runtime names agree across check, test, and run
# ---------------------------------------------------------------------------


_RESERVED_NAME_SOURCE = """\
func widen(len: Int) -> Int
    example 2 -> 4
    return len * 2
end func

func main() -> Int
    return widen(2)
end func
"""


class TestReservedRuntimeNameAgreement:
    def test_check_rejects_a_reserved_runtime_name(self, tmp_path):
        path = _write(tmp_path, _RESERVED_NAME_SOURCE)
        result = _run_geno("check", str(path))
        assert result.returncode != 0
        assert "reserved runtime name" in result.stdout + result.stderr

    def test_test_rejects_a_reserved_runtime_name(self, tmp_path):
        path = _write(tmp_path, _RESERVED_NAME_SOURCE)
        suite = run_test_suite([path])
        assert not suite.success
        assert "reserved runtime name" in (suite.file_results[0].error or "")

    def test_run_reports_the_same_diagnostic(self, tmp_path):
        path = _write(tmp_path, _RESERVED_NAME_SOURCE)
        result = _run_geno("run", str(path))
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        # #69: the actionable text must survive the process-isolated frontend
        # instead of collapsing to "the isolated frontend failed safely".
        assert "reserved runtime name" in combined
        assert "failed safely" not in combined

    def test_check_test_and_run_agree(self, tmp_path):
        path = _write(tmp_path, _RESERVED_NAME_SOURCE)
        check = _run_geno("check", str(path))
        run = _run_geno("run", str(path))
        suite = run_test_suite([path])
        assert check.returncode != 0
        assert run.returncode != 0
        assert not suite.success

    def test_library_module_without_main_is_not_rejected(self, tmp_path):
        # Standalone lowering reserves more names than project lowering, and a
        # module with no `main` is never executed by `geno run`, so checking one
        # must not fail for a program role it does not have.
        path = _write(
            tmp_path,
            "func map_values(xs: List[Int]) -> Int\n"
            "    example [1] -> 1\n"
            "    return length(xs)\n"
            "end func\n"
            "func abs_value(n: Int) -> Int\n"
            "    example -1 -> 1\n"
            "    if n < 0 then\n"
            "        return 0 - n\n"
            "    end if\n"
            "    return n\n"
            "end func\n",
            name="Lib.geno",
        )
        result = _run_geno("check", str(path))
        assert result.returncode == 0, result.stdout + result.stderr

    def test_runnable_program_with_clean_names_still_passes(self, tmp_path):
        path = _write(
            tmp_path,
            "func widen(count: Int) -> Int\n"
            "    example 2 -> 4\n"
            "    return count * 2\n"
            "end func\n"
            "func main() -> Int\n"
            "    return widen(2)\n"
            "end func\n",
        )
        assert _run_geno("check", str(path)).returncode == 0
        assert _run_geno("run", str(path)).returncode == 0
        assert run_test_suite([path]).success


@pytest.mark.parametrize("manifest", [False, True])
def test_imported_main_does_not_turn_library_into_runnable_project(tmp_path, manifest):
    from geno.test_runner import run_project_test_suite

    source = "import Demo\n" + _RESERVED_NAME_SOURCE.split("func main()")[0]
    path = _write(tmp_path, source, name="Lib.geno")
    _write(tmp_path, "func main() -> Int\n  return 0\nend func\n", name="Demo.geno")
    if manifest:
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Lib"\nfiles = ["Lib", "Demo"]\n'
        )
    result = _run_geno("check", str(path))
    assert result.returncode == 0, result.stdout + result.stderr
    suite = run_project_test_suite(tmp_path) if manifest else run_test_suite([path])
    assert suite.success, suite.file_results


def test_default_test_lowering_preserves_entrypoint_module_name(tmp_path):
    path = _write(
        tmp_path,
        "import Helper\nfunc main() -> Int\n  return 0\nend func\n",
        name="Constructor.geno",
    )
    _write(
        tmp_path,
        "func helper() -> Int\n  example () -> 0\n  return 0\nend func\n",
        name="Helper.geno",
    )
    suite = run_test_suite([path])
    assert not suite.success
    assert "reserved runtime module name" in (suite.file_results[0].error or "")


def test_directory_without_manifest_validates_selected_main(tmp_path):
    from geno.test_runner import run_project_test_suite

    _write(tmp_path, _RESERVED_NAME_SOURCE)
    result = _run_geno("check", str(tmp_path))
    assert result.returncode != 0
    assert "reserved runtime name" in result.stdout + result.stderr
    suite = run_project_test_suite(tmp_path)
    assert not suite.success
    assert "reserved runtime name" in (suite.file_results[0].error or "")
