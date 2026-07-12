"""
Tests for Geno AST Nodes (geno/ast_nodes.py)
=============================================

Unit tests for AST node construction, __str__ representations,
the visitor pattern, and the ASTVisitor base class.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.ast_nodes import (
    AssignStatement,
    ASTVisitor,
    BinaryOp,
    BooleanLiteral,
    BreakStatement,
    CallArg,
    ConstructorCall,
    ConstructorPattern,
    ContinueStatement,
    EnsuresClause,
    ExampleClause,
    ExpressionStatement,
    FieldAccess,
    FloatLiteral,
    ForStatement,
    FunctionCall,
    FunctionDef,
    FunctionType,
    Identifier,
    IfStatement,
    ImportStatement,
    IndexAccess,
    IntegerLiteral,
    LambdaExpr,
    LetStatement,
    ListLiteral,
    ListPattern,
    LiteralPattern,
    MatchArm,
    MatchExpr,
    MatchStatement,
    Parameter,
    Pipeline,
    PipelineStage,
    PlaceholderExpr,
    Program,
    RequiresClause,
    ReturnStatement,
    SimpleType,
    SpecBlock,
    StringLiteral,
    TupleExpr,
    TypeDef,
    TypedHole,
    TypeIdentifier,
    TypeVariant,
    UnaryOp,
    VariablePattern,
    VarStatement,
    WhileStatement,
    WildcardPattern,
    WithExpr,
)
from geno.tokens import SourceLocation

LOC = SourceLocation(1, 1, "<test>")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def int_lit(v: int = 0) -> IntegerLiteral:
    return IntegerLiteral(location=LOC, value=v)


def str_lit(v: str = "x") -> StringLiteral:
    return StringLiteral(location=LOC, value=v)


def bool_lit(v: bool = True) -> BooleanLiteral:
    return BooleanLiteral(location=LOC, value=v)


def float_lit(v: float = 1.0) -> FloatLiteral:
    return FloatLiteral(location=LOC, value=v)


def ident(name: str = "x") -> Identifier:
    return Identifier(location=LOC, name=name)


def simple_type(name: str = "Int") -> SimpleType:
    return SimpleType(location=LOC, name=name)


# ---------------------------------------------------------------------------
# Type Annotations
# ---------------------------------------------------------------------------


class TestSimpleType:
    def test_str_no_params(self):
        t = SimpleType(location=LOC, name="Int")
        assert str(t) == "Int"

    def test_str_with_one_param(self):
        inner = SimpleType(location=LOC, name="Int")
        t = SimpleType(location=LOC, name="List", type_params=[inner])
        assert str(t) == "List[Int]"

    def test_str_with_multiple_params(self):
        k = SimpleType(location=LOC, name="String")
        v = SimpleType(location=LOC, name="Int")
        t = SimpleType(location=LOC, name="Map", type_params=[k, v])
        assert str(t) == "Map[String, Int]"

    def test_accept_calls_visitor(self):
        visited = []

        class V(ASTVisitor):
            def visit_simple_type(self, node):
                visited.append(node)

        t = SimpleType(location=LOC, name="Bool")
        t.accept(V())
        assert len(visited) == 1

    def test_default_type_params_empty(self):
        t = SimpleType(location=LOC, name="Int")
        assert t.type_params == []


class TestFunctionType:
    def test_str_no_params(self):
        t = FunctionType(location=LOC, param_types=[], return_type=simple_type("Int"))
        assert str(t) == "() -> Int"

    def test_str_one_param(self):
        t = FunctionType(
            location=LOC,
            param_types=[simple_type("String")],
            return_type=simple_type("Bool"),
        )
        assert str(t) == "(String) -> Bool"

    def test_str_multiple_params(self):
        t = FunctionType(
            location=LOC,
            param_types=[simple_type("Int"), simple_type("Float")],
            return_type=simple_type("String"),
        )
        assert str(t) == "(Int, Float) -> String"

    def test_accept_calls_visitor(self):
        visited = []

        class V(ASTVisitor):
            def visit_function_type(self, node):
                visited.append(node)

        t = FunctionType(location=LOC, param_types=[], return_type=simple_type("Int"))
        t.accept(V())
        assert len(visited) == 1


# ---------------------------------------------------------------------------
# Literals
# ---------------------------------------------------------------------------


class TestLiterals:
    def test_integer_literal_value(self):
        node = int_lit(42)
        assert node.value == 42

    def test_float_literal_value(self):
        node = float_lit(3.14)
        assert abs(node.value - 3.14) < 1e-9

    def test_string_literal_value(self):
        node = str_lit("hello")
        assert node.value == "hello"

    def test_boolean_literal_true(self):
        node = bool_lit(True)
        assert node.value is True

    def test_boolean_literal_false(self):
        node = bool_lit(False)
        assert node.value is False

    def test_integer_visitor(self):
        visited = []

        class V(ASTVisitor):
            def visit_integer_literal(self, n):
                visited.append(n)

        int_lit(1).accept(V())
        assert len(visited) == 1

    def test_float_visitor(self):
        visited = []

        class V(ASTVisitor):
            def visit_float_literal(self, n):
                visited.append(n)

        float_lit(1.0).accept(V())
        assert len(visited) == 1

    def test_string_visitor(self):
        visited = []

        class V(ASTVisitor):
            def visit_string_literal(self, n):
                visited.append(n)

        str_lit("x").accept(V())
        assert len(visited) == 1

    def test_boolean_visitor(self):
        visited = []

        class V(ASTVisitor):
            def visit_boolean_literal(self, n):
                visited.append(n)

        bool_lit().accept(V())
        assert len(visited) == 1


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


class TestIdentifier:
    def test_name_stored(self):
        node = ident("foo")
        assert node.name == "foo"

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_identifier(self, n):
                visited.append(n.name)

        ident("bar").accept(V())
        assert visited == ["bar"]


class TestTypeIdentifier:
    def test_name_stored(self):
        node = TypeIdentifier(location=LOC, name="Some")
        assert node.name == "Some"

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_type_identifier(self, n):
                visited.append(n)

        TypeIdentifier(location=LOC, name="None").accept(V())
        assert len(visited) == 1


class TestBinaryOp:
    def test_fields(self):
        node = BinaryOp(location=LOC, operator="+", left=int_lit(1), right=int_lit(2))
        assert node.operator == "+"
        assert isinstance(node.left, IntegerLiteral)
        assert node.left.value == 1
        assert isinstance(node.right, IntegerLiteral)
        assert node.right.value == 2

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_binary_op(self, n):
                visited.append(n.operator)

        BinaryOp(location=LOC, operator="-", left=int_lit(), right=int_lit()).accept(
            V()
        )
        assert visited == ["-"]


class TestUnaryOp:
    def test_fields(self):
        node = UnaryOp(location=LOC, operator="-", operand=int_lit(5))
        assert node.operator == "-"
        assert isinstance(node.operand, IntegerLiteral)
        assert node.operand.value == 5

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_unary_op(self, n):
                visited.append(n)

        UnaryOp(location=LOC, operator="not", operand=bool_lit()).accept(V())
        assert len(visited) == 1


class TestListLiteral:
    def test_empty(self):
        node = ListLiteral(location=LOC, elements=[])
        assert node.elements == []

    def test_with_elements(self):
        node = ListLiteral(location=LOC, elements=[int_lit(1), int_lit(2)])
        assert len(node.elements) == 2

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_list_literal(self, n):
                visited.append(n)

        ListLiteral(location=LOC, elements=[]).accept(V())
        assert len(visited) == 1


class TestCallArg:
    def test_positional(self):
        arg = CallArg(value=int_lit(1))
        assert arg.name is None

    def test_named(self):
        arg = CallArg(value=int_lit(1), name="x")
        assert arg.name == "x"


class TestFunctionCall:
    def test_fields(self):
        func = ident("foo")
        args = [CallArg(value=int_lit(1))]
        node = FunctionCall(location=LOC, function=func, arguments=args)
        assert isinstance(node.function, Identifier)
        assert node.function.name == "foo"
        assert len(node.arguments) == 1

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_function_call(self, n):
                visited.append(n)

        FunctionCall(location=LOC, function=ident(), arguments=[]).accept(V())
        assert len(visited) == 1


class TestIndexAccess:
    def test_fields(self):
        node = IndexAccess(location=LOC, target=ident("arr"), index=int_lit(0))
        assert isinstance(node.index, IntegerLiteral)
        assert node.index.value == 0

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_index_access(self, n):
                visited.append(n)

        IndexAccess(location=LOC, target=ident(), index=int_lit()).accept(V())
        assert len(visited) == 1


class TestFieldAccess:
    def test_fields(self):
        node = FieldAccess(location=LOC, target=ident("obj"), field_name="x")
        assert node.field_name == "x"

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_field_access(self, n):
                visited.append(n.field_name)

        FieldAccess(location=LOC, target=ident(), field_name="y").accept(V())
        assert visited == ["y"]


class TestTupleExpr:
    def test_fields(self):
        node = TupleExpr(location=LOC, elements=[int_lit(1), str_lit("a")])
        assert len(node.elements) == 2

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_tuple_expr(self, n):
                visited.append(n)

        TupleExpr(location=LOC, elements=[]).accept(V())
        assert len(visited) == 1


class TestConstructorCall:
    def test_fields(self):
        node = ConstructorCall(location=LOC, constructor="Some", arguments=[int_lit(5)])
        assert node.constructor == "Some"
        assert len(node.arguments) == 1

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_constructor_call(self, n):
                visited.append(n.constructor)

        ConstructorCall(location=LOC, constructor="None", arguments=[]).accept(V())
        assert visited == ["None"]


class TestPlaceholderExpr:
    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_placeholder(self, n):
                visited.append(n)

        PlaceholderExpr(location=LOC).accept(V())
        assert len(visited) == 1


class TestTypedHole:
    def test_fields(self):
        node = TypedHole(location=LOC, name="h", hole_type=simple_type("Int"))
        assert node.name == "h"
        assert node.constraint is None

    def test_with_constraint(self):
        node = TypedHole(
            location=LOC,
            name="h",
            hole_type=simple_type("Int"),
            constraint=bool_lit(True),
        )
        assert node.constraint is not None


class TestWithExpr:
    def test_fields(self):
        node = WithExpr(
            location=LOC,
            target=ident("obj"),
            updates=[("x", int_lit(1))],
        )
        assert len(node.updates) == 1
        assert node.updates[0][0] == "x"

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_with_expr(self, n):
                visited.append(n)

        WithExpr(location=LOC, target=ident(), updates=[]).accept(V())
        assert len(visited) == 1


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------


class TestPatterns:
    def test_wildcard_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_wildcard_pattern(self, n):
                visited.append(n)

        WildcardPattern(location=LOC).accept(V())
        assert len(visited) == 1

    def test_variable_pattern_name(self):
        p = VariablePattern(location=LOC, name="x")
        assert p.name == "x"

    def test_variable_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_variable_pattern(self, n):
                visited.append(n.name)

        VariablePattern(location=LOC, name="y").accept(V())
        assert visited == ["y"]

    def test_constructor_pattern_fields(self):
        inner = VariablePattern(location=LOC, name="v")
        p = ConstructorPattern(location=LOC, constructor="Some", subpatterns=[inner])
        assert p.constructor == "Some"
        assert len(p.subpatterns) == 1

    def test_constructor_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_constructor_pattern(self, n):
                visited.append(n.constructor)

        ConstructorPattern(location=LOC, constructor="None", subpatterns=[]).accept(V())
        assert visited == ["None"]

    def test_literal_pattern_value(self):
        p = LiteralPattern(location=LOC, value=42)
        assert p.value == 42

    def test_literal_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_literal_pattern(self, n):
                visited.append(n)

        LiteralPattern(location=LOC, value=0).accept(V())
        assert len(visited) == 1

    def test_list_pattern_empty(self):
        p = ListPattern(location=LOC, elements=[])
        assert p.elements == []

    def test_list_pattern_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_list_pattern(self, n):
                visited.append(n)

        ListPattern(location=LOC, elements=[]).accept(V())
        assert len(visited) == 1


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


class TestLetStatement:
    def test_fields(self):
        node = LetStatement(
            location=LOC, name="x", type_annotation=simple_type(), value=int_lit(5)
        )
        assert node.name == "x"
        assert isinstance(node.value, IntegerLiteral)
        assert node.value.value == 5

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_let_statement(self, n):
                visited.append(n.name)

        LetStatement(
            location=LOC, name="y", type_annotation=simple_type(), value=int_lit()
        ).accept(V())
        assert visited == ["y"]


class TestVarStatement:
    def test_fields(self):
        node = VarStatement(
            location=LOC,
            name="counter",
            type_annotation=simple_type(),
            value=int_lit(0),
        )
        assert node.name == "counter"

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_var_statement(self, n):
                visited.append(n)

        VarStatement(
            location=LOC, name="x", type_annotation=simple_type(), value=int_lit()
        ).accept(V())
        assert len(visited) == 1


class TestAssignStatement:
    def test_fields(self):
        node = AssignStatement(location=LOC, target="x", value=int_lit(10))
        assert node.target == "x"
        assert isinstance(node.value, IntegerLiteral)
        assert node.value.value == 10

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_assign_statement(self, n):
                visited.append(n.target)

        AssignStatement(location=LOC, target="y", value=int_lit()).accept(V())
        assert visited == ["y"]


class TestIfStatement:
    def test_fields(self):
        node = IfStatement(
            location=LOC,
            condition=bool_lit(True),
            then_body=[],
            else_body=[],
        )
        assert node.label is None

    def test_with_label(self):
        node = IfStatement(
            location=LOC,
            condition=bool_lit(),
            then_body=[],
            else_body=[],
            label="mylabel",
        )
        assert node.label == "mylabel"

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_if_statement(self, n):
                visited.append(n)

        IfStatement(
            location=LOC, condition=bool_lit(), then_body=[], else_body=[]
        ).accept(V())
        assert len(visited) == 1


class TestWhileStatement:
    def test_fields(self):
        node = WhileStatement(location=LOC, condition=bool_lit(), body=[])
        assert node.label is None
        assert node.body == []

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_while_statement(self, n):
                visited.append(n)

        WhileStatement(location=LOC, condition=bool_lit(), body=[]).accept(V())
        assert len(visited) == 1


class TestForStatement:
    def test_fields(self):
        node = ForStatement(
            location=LOC,
            variable="x",
            var_type=simple_type("Int"),
            iterable=ident("items"),
            body=[],
        )
        assert node.variable == "x"
        assert node.label is None

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_for_statement(self, n):
                visited.append(n.variable)

        ForStatement(
            location=LOC,
            variable="i",
            var_type=simple_type(),
            iterable=ident(),
            body=[],
        ).accept(V())
        assert visited == ["i"]


class TestReturnStatement:
    def test_fields(self):
        node = ReturnStatement(location=LOC, value=int_lit(42))
        assert isinstance(node.value, IntegerLiteral)
        assert node.value.value == 42

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_return_statement(self, n):
                visited.append(n)

        ReturnStatement(location=LOC, value=int_lit()).accept(V())
        assert len(visited) == 1


class TestBreakContinue:
    def test_break_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_break_statement(self, n):
                visited.append(n)

        BreakStatement(location=LOC).accept(V())
        assert len(visited) == 1

    def test_continue_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_continue_statement(self, n):
                visited.append(n)

        ContinueStatement(location=LOC).accept(V())
        assert len(visited) == 1


class TestExpressionStatement:
    def test_fields(self):
        node = ExpressionStatement(location=LOC, expression=int_lit(5))
        assert isinstance(node.expression, IntegerLiteral)
        assert node.expression.value == 5

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_expression_statement(self, n):
                visited.append(n)

        ExpressionStatement(location=LOC, expression=int_lit()).accept(V())
        assert len(visited) == 1


class TestMatchStatement:
    def test_fields(self):
        arm = MatchArm(pattern=WildcardPattern(location=LOC), body=[], location=LOC)
        node = MatchStatement(location=LOC, scrutinee=ident("x"), arms=[arm])
        assert len(node.arms) == 1
        assert node.label is None

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_match_statement(self, n):
                visited.append(n)

        MatchStatement(location=LOC, scrutinee=ident(), arms=[]).accept(V())
        assert len(visited) == 1


# ---------------------------------------------------------------------------
# Specifications
# ---------------------------------------------------------------------------


class TestSpecNodes:
    def test_requires_clause(self):
        node = RequiresClause(location=LOC, condition=bool_lit())
        visited = []

        class V(ASTVisitor):
            def visit_requires_clause(self, n):
                visited.append(n)

        node.accept(V())
        assert len(visited) == 1

    def test_ensures_clause(self):
        node = EnsuresClause(location=LOC, condition=bool_lit())
        visited = []

        class V(ASTVisitor):
            def visit_ensures_clause(self, n):
                visited.append(n)

        node.accept(V())
        assert len(visited) == 1

    def test_example_clause(self):
        node = ExampleClause(
            location=LOC, input_expr=int_lit(1), output_expr=int_lit(2)
        )
        assert isinstance(node.input_expr, IntegerLiteral)
        assert node.input_expr.value == 1
        assert isinstance(node.output_expr, IntegerLiteral)
        assert node.output_expr.value == 2
        visited = []

        class V(ASTVisitor):
            def visit_example_clause(self, n):
                visited.append(n)

        node.accept(V())
        assert len(visited) == 1

    def test_spec_block_defaults(self):
        spec = SpecBlock()
        assert spec.requires == []
        assert spec.ensures == []
        assert spec.examples == []


# ---------------------------------------------------------------------------
# Definitions
# ---------------------------------------------------------------------------


class TestFunctionDef:
    def test_fields(self):
        param = Parameter(name="x", param_type=simple_type("Int"), location=LOC)
        node = FunctionDef(
            location=LOC,
            name="add",
            params=[param],
            return_type=simple_type("Int"),
            specs=SpecBlock(),
            body=[ReturnStatement(location=LOC, value=int_lit(0))],
        )
        assert node.name == "add"
        assert len(node.params) == 1
        assert node.closing_name is None

    def test_closing_name(self):
        node = FunctionDef(
            location=LOC,
            name="foo",
            params=[],
            return_type=simple_type("Int"),
            specs=SpecBlock(),
            body=[],
            closing_name="foo",
        )
        assert node.closing_name == "foo"

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_function_def(self, n):
                visited.append(n.name)

        FunctionDef(
            location=LOC,
            name="bar",
            params=[],
            return_type=simple_type(),
            specs=SpecBlock(),
            body=[],
        ).accept(V())
        assert visited == ["bar"]


class TestTypeDef:
    def test_fields(self):
        variant = TypeVariant(
            name="Some", fields=[("value", simple_type("Int"))], location=LOC
        )
        node = TypeDef(
            location=LOC, name="Option", type_params=["T"], variants=[variant]
        )
        assert node.name == "Option"
        assert node.type_params == ["T"]
        assert len(node.variants) == 1

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_type_def(self, n):
                visited.append(n.name)

        TypeDef(location=LOC, name="Foo", type_params=[], variants=[]).accept(V())
        assert visited == ["Foo"]


class TestImportStatement:
    def test_fields(self):
        node = ImportStatement(location=LOC, module_name="Math")
        assert node.module_name == "Math"

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_import_statement(self, n):
                visited.append(n.module_name)

        ImportStatement(location=LOC, module_name="Std").accept(V())
        assert visited == ["Std"]


# ---------------------------------------------------------------------------
# Program
# ---------------------------------------------------------------------------


class TestProgram:
    def test_fields(self):
        node = Program(location=LOC, definitions=[])
        assert node.definitions == []

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_program(self, n):
                visited.append(n)

        Program(location=LOC, definitions=[]).accept(V())
        assert len(visited) == 1


# ---------------------------------------------------------------------------
# ASTVisitor base: generic visit dispatch
# ---------------------------------------------------------------------------


class TestASTVisitorDispatch:
    def test_generic_visit_dispatches(self):
        """visitor.visit(node) calls node.accept(visitor)."""
        visited = []

        class V(ASTVisitor):
            def visit_integer_literal(self, n):
                visited.append(n)

        v = V()
        node = int_lit(7)
        v.visit(node)
        assert len(visited) == 1
        assert visited[0].value == 7

    def test_base_visitor_returns_none_by_default(self):
        """All base visitor methods return None (no-op)."""
        v = ASTVisitor()
        # Calling any visit_ method on the base should simply return None
        assert v.visit_integer_literal(int_lit()) is None  # type: ignore[func-returns-value]
        assert (
            v.visit_function_def(  # type: ignore[func-returns-value]
                FunctionDef(
                    location=LOC,
                    name="f",
                    params=[],
                    return_type=simple_type(),
                    specs=SpecBlock(),
                    body=[],
                )
            )
            is None
        )


# ---------------------------------------------------------------------------
# MatchExpr (expression form)
# ---------------------------------------------------------------------------


class TestMatchExpr:
    def test_fields(self):
        arm = MatchArm(pattern=WildcardPattern(location=LOC), body=[], location=LOC)
        node = MatchExpr(location=LOC, scrutinee=ident("v"), arms=[arm])
        assert isinstance(node.scrutinee, Identifier)
        assert node.scrutinee.name == "v"
        assert len(node.arms) == 1

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_match_expr(self, n):
                visited.append(n)

        MatchExpr(location=LOC, scrutinee=ident(), arms=[]).accept(V())
        assert len(visited) == 1


# ---------------------------------------------------------------------------
# LambdaExpr
# ---------------------------------------------------------------------------


class TestLambdaExpr:
    def test_fields(self):
        param = Parameter(name="x", param_type=simple_type("Int"), location=LOC)
        node = LambdaExpr(
            location=LOC,
            params=[param],
            return_type=simple_type("Int"),
            body=ident("x"),
        )
        assert len(node.params) == 1
        assert node.return_type is not None

    def test_no_return_type(self):
        node = LambdaExpr(location=LOC, params=[], return_type=None, body=int_lit(0))
        assert node.return_type is None

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_lambda(self, n):
                visited.append(n)

        LambdaExpr(location=LOC, params=[], return_type=None, body=int_lit()).accept(
            V()
        )
        assert len(visited) == 1


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class TestPipeline:
    def test_fields(self):
        stage = PipelineStage(function=ident("f"), arguments=[], location=LOC)
        node = Pipeline(location=LOC, initial=int_lit(1), stages=[stage])
        assert len(node.stages) == 1

    def test_accept(self):
        visited = []

        class V(ASTVisitor):
            def visit_pipeline(self, n):
                visited.append(n)

        Pipeline(location=LOC, initial=int_lit(), stages=[]).accept(V())
        assert len(visited) == 1
