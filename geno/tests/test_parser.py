"""
Tests for the Geno Parser
=========================
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.ast_nodes import (
    BinaryOp,
    ConstructorCall,
    ConstructorPattern,
    FieldAccess,
    ForStatement,
    FunctionCall,
    FunctionDef,
    FunctionType,
    IfStatement,
    ImportStatement,
    IntegerLiteral,
    LetStatement,
    ListLiteral,
    MatchExpr,
    MatchStatement,
    Pipeline,
    Program,
    ReturnStatement,
    SimpleType,
    TypeDef,
    TypeIdentifier,
    UnaryOp,
    VariablePattern,
    VarStatement,
    WhileStatement,
    WildcardPattern,
)
from geno.parser import ParseError, ParseErrors, parse


def parse_source(source: str) -> Program:
    """Helper to parse source code."""
    return parse(source)


class TestParserBasics:
    """Basic parser functionality tests."""

    def test_empty_program(self):
        """Empty program parses to empty definitions."""
        program = parse_source("")
        assert isinstance(program, Program)
        assert len(program.definitions) == 0

    def test_simple_function(self):
        """Simple function parses correctly."""
        source = """
        func foo() -> Int
            example () -> 0
            return 0
        end func
        """
        program = parse_source(source)
        assert len(program.definitions) == 1
        assert isinstance(program.definitions[0], FunctionDef)
        assert program.definitions[0].name == "foo"

    def test_function_with_params(self):
        """Function with parameters parses correctly."""
        source = """
        func add(x: Int, y: Int) -> Int
            example 1, 2 -> 3
            return x + y
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        assert len(func.params) == 2
        assert func.params[0].name == "x"
        assert func.params[1].name == "y"


class TestParserTypes:
    """Type annotation parsing tests."""

    def test_simple_type(self):
        """Simple type annotation."""
        source = """
        func foo(x: Int) -> String
            example 0 -> "zero"
            return "zero"
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        assert isinstance(func.params[0].param_type, SimpleType)
        assert func.params[0].param_type.name == "Int"
        assert isinstance(func.return_type, SimpleType)
        assert func.return_type.name == "String"

    def test_generic_type(self):
        """Generic type annotation."""
        source = """
        func foo(x: List[Int]) -> List[String]
            example [1] -> ["1"]
            return ["1"]
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        param_type = func.params[0].param_type
        assert isinstance(param_type, SimpleType)
        assert param_type.name == "List"
        assert len(param_type.type_params) == 1
        inner = param_type.type_params[0]
        assert isinstance(inner, SimpleType)
        assert inner.name == "Int"

    def test_function_type(self):
        """Function type annotation."""
        source = """
        func apply(f: (Int) -> Int, x: Int) -> Int
            example fn(y: Int) -> y, 5 -> 5
            return f(x)
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        f_type = func.params[0].param_type
        assert isinstance(f_type, FunctionType)
        assert len(f_type.param_types) == 1


class TestParserExpressions:
    """Expression parsing tests."""

    def test_integer_literal(self):
        """Integer literal expression."""
        source = """
        func foo() -> Int
            example () -> 42
            return 42
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        ret = func.body[0]
        assert isinstance(ret, ReturnStatement)
        assert isinstance(ret.value, IntegerLiteral)
        assert ret.value.value == 42

    def test_binary_op(self):
        """Binary operation expression."""
        source = """
        func foo() -> Int
            example () -> 5
            return 2 + 3
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        ret = func.body[0]
        assert isinstance(ret, ReturnStatement)
        assert isinstance(ret.value, BinaryOp)
        assert ret.value.operator == "+"

    def test_operator_precedence(self):
        """Operator precedence is correct."""
        source = """
        func foo() -> Int
            example () -> 14
            return 2 + 3 * 4
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        ret = func.body[0]
        assert isinstance(ret, ReturnStatement)
        expr = ret.value
        # Should be: 2 + (3 * 4)
        assert isinstance(expr, BinaryOp)
        assert expr.operator == "+"
        assert isinstance(expr.right, BinaryOp)
        assert expr.right.operator == "*"

    def test_not_binds_after_comparison(self):
        """`not a == b` parses as `not (a == b)`."""
        source = """
        func foo() -> Bool
            example () -> true
            return not 1 == 2
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        ret = func.body[0]
        assert isinstance(ret, ReturnStatement)
        expr = ret.value
        assert isinstance(expr, UnaryOp)
        assert expr.operator == "not"
        assert isinstance(expr.operand, BinaryOp)
        assert expr.operand.operator == "=="

    def test_and_binds_tighter_than_or(self):
        """`and` should remain tighter than `or`."""
        source = """
        func foo() -> Bool
            example () -> true
            return true or false and false
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        ret = func.body[0]
        assert isinstance(ret, ReturnStatement)
        expr = ret.value
        assert isinstance(expr, BinaryOp)
        assert expr.operator == "or"
        assert isinstance(expr.right, BinaryOp)
        assert expr.right.operator == "and"

    def test_not_binds_tighter_than_and(self):
        """`not` should remain tighter than `and`."""
        source = """
        func foo() -> Bool
            example () -> true
            return not false and true
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        ret = func.body[0]
        assert isinstance(ret, ReturnStatement)
        expr = ret.value
        assert isinstance(expr, BinaryOp)
        assert expr.operator == "and"
        assert isinstance(expr.left, UnaryOp)
        assert expr.left.operator == "not"

    def test_bitwise_binds_tighter_than_comparison(self):
        """Bitwise expressions should be parsed inside comparisons."""
        source = """
        func foo() -> Bool
            example () -> true
            return 1 & 2 == 0
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        ret = func.body[0]
        assert isinstance(ret, ReturnStatement)
        expr = ret.value
        assert isinstance(expr, BinaryOp)
        assert expr.operator == "=="
        assert isinstance(expr.left, BinaryOp)
        assert expr.left.operator == "&"

    def test_bitwise_precedence_order(self):
        """`<<` binds tighter than `&`, and `&` binds tighter than `^`."""
        source = """
        func foo() -> Int
            example () -> 0
            return 1 ^ 2 & 3 << 4 + 5
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        ret = func.body[0]
        assert isinstance(ret, ReturnStatement)
        expr = ret.value
        assert isinstance(expr, BinaryOp)
        assert expr.operator == "^"
        assert isinstance(expr.right, BinaryOp)
        assert expr.right.operator == "&"
        assert isinstance(expr.right.right, BinaryOp)
        assert expr.right.right.operator == "<<"
        assert isinstance(expr.right.right.right, BinaryOp)
        assert expr.right.right.right.operator == "+"

    def test_exponentiation_stays_right_associative(self):
        """Exponentiation should remain right-associative."""
        source = """
        func foo() -> Int
            example () -> 512
            return 2 ** 3 ** 2
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        ret = func.body[0]
        assert isinstance(ret, ReturnStatement)
        expr = ret.value
        assert isinstance(expr, BinaryOp)
        assert expr.operator == "**"
        assert isinstance(expr.right, BinaryOp)
        assert expr.right.operator == "**"

    def test_unary_minus_stays_tighter_than_exponentiation(self):
        """Unary minus should still bind before `**` in Geno."""
        source = """
        func foo() -> Int
            example () -> -8
            return -2 ** 3
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        ret = func.body[0]
        assert isinstance(ret, ReturnStatement)
        expr = ret.value
        assert isinstance(expr, BinaryOp)
        assert expr.operator == "**"
        assert isinstance(expr.left, UnaryOp)
        assert expr.left.operator == "-"

    def test_function_call(self):
        """Function call expression."""
        source = """
        func foo() -> Int
            example () -> 0
            return bar(1, 2)
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        ret = func.body[0]
        assert isinstance(ret, ReturnStatement)
        call = ret.value
        assert isinstance(call, FunctionCall)
        assert len(call.arguments) == 2

    def test_list_literal(self):
        """List literal expression."""
        source = """
        func foo() -> List[Int]
            example () -> [1, 2, 3]
            return [1, 2, 3]
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        ret = func.body[0]
        assert isinstance(ret, ReturnStatement)
        lst = ret.value
        assert isinstance(lst, ListLiteral)
        assert len(lst.elements) == 3

    def test_constructor_call(self):
        """Constructor call expression."""
        source = """
        func foo() -> Option[Int]
            example () -> Some(5)
            return Some(5)
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        ret = func.body[0]
        assert isinstance(ret, ReturnStatement)
        ctor = ret.value
        assert isinstance(ctor, ConstructorCall)
        assert ctor.constructor == "Some"

    def test_pipeline(self):
        """Pipeline expression."""
        source = """
        func foo() -> Int
            example () -> 0
            return [1, 2, 3] |> length
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        ret = func.body[0]
        assert isinstance(ret, ReturnStatement)
        pipe = ret.value
        assert isinstance(pipe, Pipeline)
        assert len(pipe.stages) == 1

    def test_pipeline_with_qualified_stage(self):
        """Qualified pipeline stage expression."""
        source = """
        import Math

        func foo() -> Int
            example () -> 3
            return -3 |> Math.abs
        end func
        """
        program = parse_source(source)
        func = program.definitions[1]
        assert isinstance(func, FunctionDef)
        ret = func.body[0]
        assert isinstance(ret, ReturnStatement)
        pipe = ret.value
        assert isinstance(pipe, Pipeline)
        assert len(pipe.stages) == 1
        stage_func = pipe.stages[0].function
        assert isinstance(stage_func, FieldAccess)
        assert isinstance(stage_func.target, TypeIdentifier)
        assert stage_func.target.name == "Math"
        assert stage_func.field_name == "abs"


class TestParserStatements:
    """Statement parsing tests."""

    def test_let_statement(self):
        """Let statement."""
        source = """
        func foo() -> Int
            example () -> 5
            let x: Int = 5
            return x
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        let_stmt = func.body[0]
        assert isinstance(let_stmt, LetStatement)
        assert let_stmt.name == "x"

    def test_var_statement(self):
        """Var statement."""
        source = """
        func foo() -> Int
            example () -> 5
            var x: Int = 5
            return x
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        var_stmt = func.body[0]
        assert isinstance(var_stmt, VarStatement)
        assert var_stmt.name == "x"

    def test_if_statement(self):
        """If statement."""
        source = """
        func foo(x: Int) -> Int
            example 1 -> 1
            if x > 0 then
                return 1
            else
                return 0
            end if
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        if_stmt = func.body[0]
        assert isinstance(if_stmt, IfStatement)
        assert len(if_stmt.then_body) == 1
        assert len(if_stmt.else_body) == 1

    def test_while_statement(self):
        """While statement."""
        source = """
        func foo() -> Int
            example () -> 0
            var x: Int = 0
            while x < 10 do
                x = x + 1
            end while
            return x
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        while_stmt = func.body[1]
        assert isinstance(while_stmt, WhileStatement)

    def test_for_statement(self):
        """For statement."""
        source = """
        func foo() -> Int
            example () -> 0
            var sum: Int = 0
            for x: Int in [1, 2, 3] do
                sum = sum + x
            end for
            return sum
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        for_stmt = func.body[1]
        assert isinstance(for_stmt, ForStatement)
        assert for_stmt.variable == "x"

    def test_match_statement(self):
        """Match statement."""
        source = """
        func foo(opt: Option[Int]) -> Int
            example Some(5) -> 5
            match opt with
                | Some(x) -> return x
                | None -> return 0
            end match
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        match_stmt = func.body[0]
        assert isinstance(match_stmt, MatchStatement)
        assert len(match_stmt.arms) == 2

    def test_match_statement_guard_uses_when(self):
        """Match statement guards use contextual `when`."""
        source = """
        func foo(x: Int) -> Int
            example 1 -> 1
            match x with
                | y when y > 0 -> return y
                | _ -> return 0
            end match
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        match_stmt = func.body[0]
        assert isinstance(match_stmt, MatchStatement)
        assert isinstance(match_stmt.arms[0].guard, BinaryOp)
        assert match_stmt.arms[0].guard.operator == ">"
        assert match_stmt.arms[1].guard is None

    def test_match_expression_guard_uses_when(self):
        """Match expression guards use contextual `when`."""
        source = """
        func foo(x: Int) -> Int
            example 1 -> 1
            let y: Int = match x with
                | v when v > 0 -> 1
                | _ -> 0
            end match
            return y
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        let_stmt = func.body[0]
        assert isinstance(let_stmt, LetStatement)
        match_expr = let_stmt.value
        assert isinstance(match_expr, MatchExpr)
        assert isinstance(match_expr.arms[0].guard, BinaryOp)
        assert match_expr.arms[0].guard.operator == ">"
        assert match_expr.arms[1].guard is None

    def test_match_guard_rejects_if_keyword(self):
        """`if` remains a keyword, not the contextual match-guard marker."""
        source = """
        func foo(x: Int) -> Int
            example 1 -> 1
            match x with
                | y if y > 0 -> return y
                | _ -> return 0
            end match
        end func
        """
        with pytest.raises((ParseError, ParseErrors)) as exc_info:
            parse_source(source)
        assert "Expected '->', got 'if'" in str(exc_info.value)


class TestParserPatterns:
    """Pattern parsing tests."""

    def test_variable_pattern(self):
        """Variable pattern."""
        source = """
        func foo(x: Int) -> Int
            example 5 -> 5
            match x with
                | y -> return y
            end match
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        match_stmt = func.body[0]
        assert isinstance(match_stmt, MatchStatement)
        pattern = match_stmt.arms[0].pattern
        assert isinstance(pattern, VariablePattern)
        assert pattern.name == "y"

    def test_constructor_pattern(self):
        """Constructor pattern."""
        source = """
        func foo(opt: Option[Int]) -> Int
            example Some(5) -> 5
            match opt with
                | Some(x) -> return x
                | None -> return 0
            end match
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        match_stmt = func.body[0]
        assert isinstance(match_stmt, MatchStatement)
        pattern1 = match_stmt.arms[0].pattern
        assert isinstance(pattern1, ConstructorPattern)
        assert pattern1.constructor == "Some"

    def test_wildcard_pattern(self):
        """Wildcard pattern."""
        source = """
        func foo(x: Int) -> Int
            example 5 -> 0
            match x with
                | _ -> return 0
            end match
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        match_stmt = func.body[0]
        assert isinstance(match_stmt, MatchStatement)
        pattern = match_stmt.arms[0].pattern
        assert isinstance(pattern, WildcardPattern)


class TestParserSpecs:
    """Specification block parsing tests."""

    def test_requires(self):
        """Requires clause."""
        source = """
        func foo(x: Int) -> Int
            requires x > 0
            example 5 -> 5
            return x
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        assert len(func.specs.requires) == 1

    def test_ensures(self):
        """Ensures clause."""
        source = """
        func foo(x: Int) -> Int
            ensures result > 0
            example 5 -> 5
            return x
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        assert len(func.specs.ensures) == 1

    def test_example(self):
        """Example clause."""
        source = """
        func foo(x: Int) -> Int
            example 5 -> 5
            example 0 -> 0
            return x
        end func
        """
        program = parse_source(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        assert len(func.specs.examples) == 2


class TestParserTypeDefinitions:
    """Type definition parsing tests."""

    def test_simple_type_def(self):
        """Simple type definition."""
        source = """
        type Color = Red | Green | Blue
        """
        program = parse_source(source)
        assert len(program.definitions) == 1
        type_def = program.definitions[0]
        assert isinstance(type_def, TypeDef)
        assert type_def.name == "Color"
        assert len(type_def.variants) == 3

    def test_type_with_fields(self):
        """Type definition with fields."""
        source = """
        type Point = Point(x: Int, y: Int)
        """
        program = parse_source(source)
        type_def = program.definitions[0]
        assert isinstance(type_def, TypeDef)
        variant = type_def.variants[0]
        assert len(variant.fields) == 2
        assert variant.fields[0][0] == "x"
        assert variant.fields[1][0] == "y"

    def test_generic_type_def(self):
        """Generic type definition."""
        source = """
        type Option[T] = Some(value: T) | None
        """
        program = parse_source(source)
        type_def = program.definitions[0]
        assert isinstance(type_def, TypeDef)
        assert len(type_def.type_params) == 1
        assert type_def.type_params[0] == "T"


class TestParserErrors:
    """Parser error handling tests."""

    def test_missing_end_func(self):
        """Missing end func raises error."""
        source = """
        func foo() -> Int
            return 0
        """
        with pytest.raises(ParseError):
            parse_source(source)

    def test_mismatched_func_name(self):
        """Mismatched function name raises error."""
        source = """
        func foo() -> Int
            example () -> 0
            return 0
        end func bar
        """
        with pytest.raises(ParseError):
            parse_source(source)

    def test_missing_type_annotation(self):
        """Missing type annotation raises error."""
        source = """
        func foo(x) -> Int
            return 0
        end func
        """
        with pytest.raises((ParseError, ParseErrors)):
            parse_source(source)

    def test_invalid_expression(self):
        """Invalid expression raises error."""
        source = """
        func foo() -> Int
            return + +
        end func
        """
        with pytest.raises((ParseError, ParseErrors)):
            parse_source(source)

    def test_chained_comparison_reports_targeted_error(self):
        """Comparison chaining must be written explicitly with logical operators."""
        source = """
        func foo() -> Bool
            return 1 < 2 < 3
        end func
        """
        with pytest.raises(
            (ParseError, ParseErrors), match="Comparison chaining is not supported"
        ):
            parse_source(source)

    def test_expression_lambda_requires_arrow(self):
        """Expression lambda without -> arrow raises a parse error.

        Regression test for issue #667 (audit F-0016): the parser previously
        accepted `fn(x: Int) x + 1` even though the grammar requires
        `fn(params) -> expr`.
        """
        source = """
        func main() -> Int
            example () -> 3
            let f: (Int) -> Int = fn(x: Int) x + 1
            return f(2)
        end func
        """
        with pytest.raises((ParseError, ParseErrors)):
            parse_source(source)

    def test_expression_lambda_with_arrow_parses(self):
        """Expression lambda with the required -> arrow parses successfully."""
        source = """
        func main() -> Int
            example () -> 3
            let f: (Int) -> Int = fn(x: Int) -> x + 1
            return f(2)
        end func
        """
        program = parse_source(source)
        assert len(program.definitions) == 1


class TestParserErrorRecovery:
    """Tests for parser error recovery."""

    def test_multiple_errors_collected(self):
        """Parser collects multiple errors across definitions."""
        source = """
        func first(x) -> Int
            return x
        end func

        func second(y) -> Int
            return y
        end func
        """
        with pytest.raises(ParseErrors) as exc_info:
            parse_source(source)
        # Should collect errors from both functions
        assert len(exc_info.value.errors) >= 2

    def test_recovery_to_next_definition(self):
        """Parser recovers to next function after error."""
        source = """
        func broken(
            return 0
        end func

        func good() -> Int
            return 42
        end func
        """
        with pytest.raises((ParseError, ParseErrors)):
            parse_source(source)

    def test_error_message_includes_location(self):
        """Error messages include source location."""
        source = """
        func foo() ->
        end func
        """
        with pytest.raises((ParseError, ParseErrors)) as exc_info:
            parse_source(source)
        # Error message should include line/column info
        error_str = str(exc_info.value)
        assert "<stdin>:" in error_str

    def test_recovery_resumes_at_test_block(self):
        """Parser synchronizes at ``test`` blocks after a preceding error.

        F-0017 / #662: ``_synchronize_to_definition``'s token set did
        not include ``TEST``, so any ``test`` block appearing after a
        parse error was silently skipped instead of being parsed.  The
        partial-program attached to the raised ``ParseErrors`` must now
        include the ``TestBlock`` so ``geno test`` can still discover it.
        """
        from geno.ast_nodes import TestBlock

        source = """
        import 123badmodule

        test "still reachable"
            let x: Int = 1
        end test
        """
        with pytest.raises((ParseError, ParseErrors)) as exc_info:
            parse_source(source)

        partial = getattr(exc_info.value, "partial_program", None)
        assert partial is not None, (
            "Parser should attach a partial program when it recovers"
        )
        test_blocks = [d for d in partial.definitions if isinstance(d, TestBlock)]
        assert len(test_blocks) == 1
        assert test_blocks[0].name == "still reachable"


class TestImportParsing:
    """Test import statement parsing."""

    def test_parse_import(self):
        source = """
        import Utils

        func main() -> Int
            return 0
        end func
        """
        program = parse_source(source)
        assert len(program.definitions) == 2
        imp = program.definitions[0]
        assert isinstance(imp, ImportStatement)
        assert imp.module_name == "Utils"

    def test_parse_multiple_imports(self):
        source = """
        import Utils
        import Math

        func main() -> Int
            return 0
        end func
        """
        program = parse_source(source)
        assert len(program.definitions) == 3
        assert isinstance(program.definitions[0], ImportStatement)
        assert program.definitions[0].module_name == "Utils"
        assert isinstance(program.definitions[1], ImportStatement)
        assert program.definitions[1].module_name == "Math"

    def test_import_requires_pascal_case(self):
        source = "import utils"
        with pytest.raises((ParseError, ParseErrors)):
            parse_source(source)


class TestParserNestingDepth:
    """Test that deeply nested expressions raise ParseError, not RecursionError."""

    def _assert_parse_error_contains(self, source: str, text: str):
        with pytest.raises((ParseError, ParseErrors)) as exc_info:
            parse_source(source)
        assert text in str(exc_info.value)

    def test_deeply_nested_parentheses(self):
        depth = 60
        source = (
            "func main() -> Int\n"
            "    return " + "(" * depth + "1" + ")" * depth + "\n"
            "end func\n"
        )
        with pytest.raises((ParseError, ParseErrors)):
            parse_source(source)

    def test_deeply_nested_unary_minus(self):
        depth = 60
        source = "func main() -> Int\n    return " + "-" * depth + "1\nend func\n"
        with pytest.raises((ParseError, ParseErrors)):
            parse_source(source)

    def test_deeply_nested_list_literals(self):
        depth = 60
        source = (
            "func main() -> Int\n"
            "    return " + "[" * depth + "1" + "]" * depth + "\n"
            "end func\n"
        )
        with pytest.raises((ParseError, ParseErrors)):
            parse_source(source)

    def test_deeply_nested_not_operators(self):
        depth = 60
        source = (
            "func main() -> Bool\n    return " + "not " * depth + "true\nend func\n"
        )
        with pytest.raises((ParseError, ParseErrors)):
            parse_source(source)

    def test_deep_right_associative_power_chain(self):
        depth = 60
        source = (
            "func main() -> Int\n"
            "    return " + " ** ".join(["1"] * depth) + "\n"
            "end func\n"
        )
        self._assert_parse_error_contains(source, "Expression nesting too deep")

    def test_normal_nesting_allowed(self):
        """Reasonable nesting depth should parse fine."""
        source = """
        func main() -> Int
            return ((((1 + 2) * 3) - 4) + 5)
        end func
        """
        program = parse_source(source)
        assert len(program.definitions) == 1

    def test_deeply_nested_generic_type_annotations(self):
        typ = "Int"
        for _ in range(60):
            typ = f"List[{typ}]"
        source = f"func f(x: {typ}) -> Int\n    return 0\nend func\n"

        self._assert_parse_error_contains(source, "Type annotation nesting too deep")

    def test_deeply_nested_function_type_annotations(self):
        typ = "Int"
        for _ in range(60):
            typ = f"(Int) -> {typ}"
        source = f"func f(x: {typ}) -> Int\n    return 0\nend func\n"

        self._assert_parse_error_contains(source, "Type annotation nesting too deep")

    def test_deeply_nested_constructor_patterns(self):
        pattern = "x"
        for _ in range(60):
            pattern = f"Some({pattern})"
        source = f"""
        func f(x: Int) -> Int
            match x with
                | {pattern} -> return 1
            end match
            return 0
        end func
        """

        self._assert_parse_error_contains(source, "Pattern nesting too deep")

    def test_deeply_nested_list_patterns(self):
        pattern = "x"
        for _ in range(60):
            pattern = f"[{pattern}]"
        source = f"""
        func f(x: Int) -> Int
            match x with
                | {pattern} -> return 1
            end match
            return 0
        end func
        """

        self._assert_parse_error_contains(source, "Pattern nesting too deep")


class TestEmptyMatchArms:
    """Tests for empty match arm validation."""

    def test_empty_match_statement_error(self):
        """Match statement with no arms should raise a parse error."""
        source = """
        func main() -> Int
            let x: Int = 1
            match x with
            end match
            return 0
        end func
        """
        with pytest.raises((ParseError, ParseErrors)):
            parse_source(source)

    def test_empty_match_expression_error(self):
        """Match expression with no arms should raise a parse error."""
        source = """
        func main() -> Int
            let x: Int = match 1 with end match
            return x
        end func
        """
        with pytest.raises((ParseError, ParseErrors)):
            parse_source(source)


class TestElseBranchMultipleStatements:
    """Regression test: multiple statements in else branch (selfhost bug)."""

    def test_same_line_else_if_chain_uses_single_end_if(self):
        """Documented else-if chains parse with one final terminator."""
        source = (
            "func sign(x: Int) -> String\n"
            '    example 2 -> "positive"\n'
            '    example 0 -> "zero"\n'
            "    if x > 0 then\n"
            '        return "positive"\n'
            "    else if x == 0 then\n"
            '        return "zero"\n'
            "    else\n"
            '        return "negative"\n'
            "    end if\n"
            "end func\n"
        )

        program = parse(source)
        func = program.definitions[0]
        assert isinstance(func, FunctionDef)
        outer = func.body[0]
        assert isinstance(outer, IfStatement)
        assert len(outer.else_body) == 1
        inner = outer.else_body[0]
        assert isinstance(inner, IfStatement)
        assert len(inner.else_body) == 1
        assert isinstance(inner.else_body[0], ReturnStatement)

    def test_multiple_if_in_else(self):
        """Two if/end if blocks inside an else branch must parse."""
        source = (
            "func foo(x: Int) -> Int\n"
            "    example 1 -> 1\n"
            "    if x > 0 then\n"
            "        return 1\n"
            "    else\n"
            "        if x < -5 then\n"
            "            return -5\n"
            "        end if\n"
            "        if x < -10 then\n"
            "            return -10\n"
            "        end if\n"
            "    end if\n"
            "    return x\n"
            "end func\n"
        )
        program = parse(source)
        assert len(program.definitions) == 1

    def test_else_if_chain_still_works(self):
        """else followed by if still works as a nested if."""
        source = (
            "func bar(x: Int) -> Int\n"
            "    example 1 -> 1\n"
            "    if x > 0 then\n"
            "        return 1\n"
            "    else\n"
            "        if x < 0 then\n"
            "            return -1\n"
            "        else\n"
            "            return 0\n"
            "        end if\n"
            "    end if\n"
            "end func\n"
        )
        program = parse(source)
        assert len(program.definitions) == 1

    def test_statements_after_if_in_else(self):
        """Statements after an if block inside else must parse."""
        source = (
            "func baz(x: Int) -> Int\n"
            "    example 1 -> 1\n"
            "    if x > 0 then\n"
            "        return 1\n"
            "    else\n"
            "        if x < 0 then\n"
            "            return -1\n"
            "        end if\n"
            "        return 0\n"
            "    end if\n"
            "end func\n"
        )
        program = parse(source)
        assert len(program.definitions) == 1


class TestParserReviewFollowups:
    """Regression tests for parser audit findings (HIGH-13/14, MED-08/09)."""

    def test_empty_match_arm_body_rejected(self):
        """HIGH-13: spec §14 requires each match arm to have >=1 statement."""
        source = (
            "func f(x: Int) -> Int\n"
            "    example 1 -> 1\n"
            "    match x with\n"
            "        | 1 ->\n"
            "        | _ -> return 0\n"
            "    end match\n"
            "end func\n"
        )
        with pytest.raises((ParseError, ParseErrors)):
            parse_source(source)

    def test_multiple_rest_patterns_rejected(self):
        """HIGH-14: spec §8.6 — rest pattern may appear at most once."""
        source = (
            "func f(xs: List[Int]) -> Int\n"
            "    example [1] -> 1\n"
            "    match xs with\n"
            "        | [...a, ...b] -> return 1\n"
            "        | _ -> return 0\n"
            "    end match\n"
            "end func\n"
        )
        with pytest.raises((ParseError, ParseErrors)):
            parse_source(source)

    def test_bare_return_parses(self):
        """MED-08: spec §14 allows bare `return` (expr is optional)."""
        source = "func noop() -> Unit\n    example () -> ()\n    return\nend func\n"
        program = parse_source(source)
        assert len(program.definitions) == 1

    def test_single_element_tuple_literal_parses(self):
        """MED-09: single-element tuple (x,) must be representable."""
        source = (
            "func main() -> Unit\n    let t: (Int) = (5,)\n    return ()\nend func\n"
        )
        program = parse_source(source)
        assert len(program.definitions) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
