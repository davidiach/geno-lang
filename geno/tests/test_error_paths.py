"""
Error-path tests (#387) — parser, typechecker, and LSP negative test cases.

Tests error conditions that were previously uncovered: try-catch parsing,
f-string errors, @untested annotations, type annotation errors,
circular/unknown imports, recursive type aliases.
"""

import pytest

from geno.lexer import LexerError
from geno.parser import ParseError, ParseErrors, parse
from geno.typechecker import TypeChecker, type_check
from geno.typechecker import TypeError as GenoTypeError


def _expect_parse_error(source: str) -> Exception:
    """Helper: parse source and assert it produces a parse error."""
    with pytest.raises((ParseError, ParseErrors)) as exc_info:
        parse(source)
    return exc_info.value  # type: ignore[no-any-return]


def _expect_type_error(source: str) -> GenoTypeError:
    """Helper: type-check source and assert it produces a type error."""
    program = parse(source)
    with pytest.raises(GenoTypeError) as exc_info:
        type_check(program)
    return exc_info.value  # type: ignore[no-any-return]


def _primary_parse_error(source: str) -> ParseError:
    """Parse source and return the first reported ParseError."""
    err = _expect_parse_error(source)
    if isinstance(err, ParseErrors):
        return err.errors[0]
    assert isinstance(err, ParseError)
    return err


# ===========================================================================
# Parser error paths
# ===========================================================================


class TestTryCatchParseErrors:
    """Try-catch statement parsing error conditions."""

    def test_try_missing_catch(self):
        source = """
func main() -> Int
    try
        return 1
    end try
    return 0
end func
"""
        _expect_parse_error(source)

    def test_try_missing_end_try(self):
        source = """
func main() -> Int
    try
        return 1
    catch e: String
        return 0
end func
"""
        _expect_parse_error(source)

    def test_try_catch_missing_variable(self):
        source = """
func main() -> Int
    try
        return 1
    catch : String
        return 0
    end try
end func
"""
        _expect_parse_error(source)

    def test_try_catch_missing_type(self):
        source = """
func main() -> Int
    try
        return 1
    catch e:
        return 0
    end try
end func
"""
        _expect_parse_error(source)


class TestFStringParseErrors:
    """F-string parsing error conditions."""

    def test_fstring_unterminated_expression(self):
        source = """
func main() -> String
    let x: Int = 42
    return f"value is {x"
end func
"""
        with pytest.raises((ParseError, ParseErrors, LexerError)):
            parse(source)

    def test_fstring_unterminated_expression_reports_expression_location(self):
        source = 'func main() -> String\n    return f"value is {x\nend func\n'
        with pytest.raises(LexerError) as exc_info:
            parse(source)

        err = exc_info.value
        line = source.splitlines()[1]
        assert "Unterminated f-string expression" in str(err)
        assert err.location is not None
        assert err.location.line == 2
        assert err.location.column == line.index("x") + 1

    def test_fstring_empty_expression(self):
        source = """
func main() -> String
    return f"empty {}"
end func
"""
        with pytest.raises((ParseError, ParseErrors, LexerError)):
            parse(source)

    def test_fstring_trailing_token_reports_true_location(self):
        """A stray token inside the interpolation is flagged at its own column,
        not at the f-string's opening quote."""
        source = 'func main() -> String\n    return f"hi {a b}"\nend func\n'
        err = _primary_parse_error(source)
        line = source.splitlines()[1]
        assert err.location.line == 2
        assert err.location.column == line.index("b}") + 1

    def test_fstring_incomplete_expr_reports_real_line(self):
        """Sub-parser errors must map onto the real source line, not line 1."""
        source = 'func main() -> String\n    return f"hi {1 +}"\nend func\n'
        err = _primary_parse_error(source)
        # The f-string lives on line 2; the old behaviour reported line 1.
        assert err.location.line == 2

    def test_fstring_error_column_accounts_for_escapes(self):
        """De-escaping in the literal part must not shift the reported column.

        The ``\\t`` escape is two source characters but one character in the
        decoded string, so a naive offset would mislocate the error by one.
        """
        source = 'func main() -> String\n    return f"a\\tb{x y}"\nend func\n'
        err = _primary_parse_error(source)
        line = source.splitlines()[1]
        assert err.location.line == 2
        assert err.location.column == line.index("y}") + 1

    def test_fstring_empty_expression_reports_brace_location(self):
        """The empty-interpolation error points at the interpolation, not the
        f-string start."""
        source = 'func main() -> String\n    return f"hi {}"\nend func\n'
        err = _primary_parse_error(source)
        line = source.splitlines()[1]
        assert err.location.line == 2
        assert err.location.column == line.index("}") + 1

    def test_fstring_multiline_interpolation_reports_true_line(self):
        """An interpolation that spans lines reports errors on the right line."""
        source = 'func main() -> String\n    return f"v={a +\n@}"\nend func\n'
        err = _primary_parse_error(source)
        assert err.location.line == 3
        assert err.location.column == 1


class TestTypeAnnotationErrors:
    """Type annotation parsing error conditions."""

    def test_invalid_type_in_param(self):
        source = """
func foo(x: 123) -> Int
    return x
end func
"""
        _expect_parse_error(source)

    def test_invalid_return_type(self):
        source = """
func foo(x: Int) -> 123
    return x
end func
"""
        _expect_parse_error(source)


class TestUntestedAnnotationErrors:
    """@untested annotation error conditions."""

    def test_untested_on_non_function(self):
        source = """
@untested "reason"
type Color = Red | Green
"""
        _expect_parse_error(source)

    def test_untested_empty_reason(self):
        source = """
@untested ""
func foo(x: Int) -> Int
    return x
end func
"""
        _expect_parse_error(source)


class TestAssignmentTargetErrors:
    """Invalid assignment target errors."""

    def test_assign_to_literal(self):
        source = """
func main() -> Int
    42 = 1
    return 0
end func
"""
        _expect_parse_error(source)

    def test_assign_to_binary_expr(self):
        source = """
func main() -> Int
    let x: Int = 1
    let y: Int = 2
    x + y = 3
    return 0
end func
"""
        _expect_parse_error(source)


# ===========================================================================
# Typechecker error paths
# ===========================================================================


class TestTypecheckerTypeErrors:
    """Various type error conditions."""

    def test_if_condition_not_bool(self):
        _expect_type_error("""
func main() -> Int
    if 42 then
        return 1
    end if
    return 0
end func
""")

    def test_binary_op_type_mismatch(self):
        _expect_type_error("""
func main() -> Int
    return 1 + "hello"
end func
""")

    def test_match_wrong_pattern_type(self):
        _expect_type_error("""
type Color = Red | Green | Blue

func describe(c: Color) -> String
    example Red -> "red"
    match c with
    | 42 -> return "number"
    end match
    return "unknown"
end func
""")


class TestTypecheckerThrowErrors:
    """Throw expression type error conditions."""

    def test_throw_wrong_type(self):
        source = """
func main() -> Result[Int, String]
    throw 42
end func
"""
        _expect_type_error(source)


class TestTypecheckerMiscErrors:
    """Other typechecker error conditions."""

    def test_return_type_mismatch(self):
        err = _expect_type_error("""
func add(a: Int, b: Int) -> String
    example 1, 2 -> "three"
    return a + b
end func
""")
        assert "type" in str(err).lower() or "mismatch" in str(err).lower()

    def test_undefined_variable(self):
        _expect_type_error("""
func main() -> Int
    return undefined_var
end func
""")

    def test_undefined_type_in_annotation(self):
        _expect_type_error("""
func main() -> Int
    let x: NonExistentType = 42
    return 0
end func
""")

    def test_wrong_argument_count(self):
        _expect_type_error("""
func add(a: Int, b: Int) -> Int
    example 1, 2 -> 3
    return a + b
end func

func main() -> Int
    return add(1)
end func
""")
