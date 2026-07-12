"""
Geno Abstract Syntax Tree
=========================

Defines all AST node types for the Geno language.
Uses dataclasses for clean, immutable node definitions.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Union

from .tokens import SourceLocation

# =============================================================================
# Base Classes
# =============================================================================


@dataclass
class ASTNode(ABC):
    """Base class for all AST nodes."""

    location: SourceLocation

    @abstractmethod
    def accept(self, visitor: "ASTVisitor") -> Any:
        """Accept a visitor for tree traversal."""
        pass


@dataclass
class Expression(ASTNode):
    """Base class for all expressions."""

    # Set by the typechecker; used by the compiler for optimization decisions.
    _resolved_type: Any | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _expected_runtime_type: Any | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _resolved_builtin_name: str | None = field(
        default=None, init=False, repr=False, compare=False
    )


@dataclass
class Statement(ASTNode):
    """Base class for all statements."""

    # Set by the typechecker when a runtime boundary needs a known expected type.
    _expected_runtime_type: Any | None = field(
        default=None, init=False, repr=False, compare=False
    )
    _resolved_builtin_name: str | None = field(
        default=None, init=False, repr=False, compare=False
    )


@dataclass
class Definition(ASTNode):
    """Base class for top-level definitions."""

    pass


# =============================================================================
# Type Annotations
# =============================================================================


@dataclass
class TypeAnnotation(ASTNode):
    """Base class for type annotations."""

    pass


@dataclass
class SimpleType(TypeAnnotation):
    """
    A simple type name, possibly with type parameters.
    Examples: Int, String, List[Int], Map[String, Int]
    """

    name: str
    type_params: list["TypeAnnotation"] = field(default_factory=list)

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_simple_type(self)

    def __str__(self) -> str:
        if self.type_params:
            params = ", ".join(str(p) for p in self.type_params)
            return f"{self.name}[{params}]"
        return self.name


@dataclass
class FunctionType(TypeAnnotation):
    """
    A function type.
    Example: (Int, Int) -> Int
    Example with effects: (Int, Int) -> Int with fs
    """

    param_types: list[TypeAnnotation]
    return_type: TypeAnnotation
    effects: list[str] = field(default_factory=list)

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_function_type(self)

    def __str__(self) -> str:
        params = ", ".join(str(p) for p in self.param_types)
        base = f"({params}) -> {self.return_type}"
        if self.effects:
            return f"{base} with {', '.join(sorted(self.effects))}"
        return base


# =============================================================================
# Expressions
# =============================================================================


@dataclass
class IntegerLiteral(Expression):
    """Integer literal: 42"""

    value: int

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_integer_literal(self)


@dataclass
class FloatLiteral(Expression):
    """Float literal: 3.14"""

    value: float

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_float_literal(self)


@dataclass
class StringLiteral(Expression):
    """String literal: "hello" """

    value: str

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_string_literal(self)


@dataclass
class FStringExpr(Expression):
    """F-string interpolation: f"Hello {name}, age {age}" """

    parts: list[Union[str, Expression]]

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_fstring_expr(self)


@dataclass
class BooleanLiteral(Expression):
    """Boolean literal: true, false"""

    value: bool

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_boolean_literal(self)


@dataclass
class Identifier(Expression):
    """Variable or function reference: x, foo"""

    name: str

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_identifier(self)


@dataclass
class TypeIdentifier(Expression):
    """Type or constructor reference: Int, Some, None"""

    name: str

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_type_identifier(self)


@dataclass
class ListLiteral(Expression):
    """List literal: [1, 2, 3]"""

    elements: list[Expression]

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_list_literal(self)


@dataclass
class ListComprehension(Expression):
    """List comprehension: [expr for var: Type in iterable if cond]"""

    element_expr: Expression
    variable: str
    var_type: TypeAnnotation
    iterable: Expression
    condition: Expression | None = None

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_list_comprehension(self)


@dataclass
class ThrowExpression(Expression):
    """throw expr — throws a typed error value"""

    value: Expression

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_throw_expression(self)


@dataclass
class AwaitExpr(Expression):
    """await expr — await an async value"""

    expr: Expression

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_await_expr(self)


@dataclass
class BinaryOp(Expression):
    """Binary operation: a + b, x and y"""

    operator: str
    left: Expression
    right: Expression

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_binary_op(self)


@dataclass
class UnaryOp(Expression):
    """Unary operation: -x, not y"""

    operator: str
    operand: Expression

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_unary_op(self)


@dataclass
class CallArg:
    """A function call argument, optionally named."""

    value: Expression
    name: str | None = None  # None for positional args


@dataclass
class FunctionCall(Expression):
    """Function call: f(x, y), foo(a: 1, b: 2)"""

    function: Expression
    arguments: list[CallArg]
    _resolved_builtin_name: str | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_function_call(self)


@dataclass
class IndexAccess(Expression):
    """Index access: arr[0]"""

    target: Expression
    index: Expression

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_index_access(self)


@dataclass
class FieldAccess(Expression):
    """Field access: obj.field"""

    target: Expression
    field_name: str

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_field_access(self)


@dataclass
class Pipeline(Expression):
    """Pipeline expression: x |> f |> g"""

    initial: Expression
    stages: list["PipelineStage"]

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_pipeline(self)


@dataclass
class PipelineStage:
    """A single stage in a pipeline."""

    function: Expression
    arguments: list[Expression]  # May contain PlaceholderExpr
    location: SourceLocation


@dataclass
class PlaceholderExpr(Expression):
    """Placeholder _ in pipeline stages."""

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_placeholder(self)


@dataclass
class LambdaExpr(Expression):
    """Anonymous function.

    Expression form: fn(x: Int) -> x + 1
    Block form:      fn(a: Int, b: Int) do ... end fn
    """

    params: list["Parameter"]
    return_type: TypeAnnotation | None
    body: Expression | None = None
    block_body: list["Statement"] | None = None

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_lambda(self)


@dataclass
class MatchExpr(Expression):
    """
    Match expression (when used as expression).
    match x with | Some(v) -> v | None -> 0 end match
    """

    scrutinee: Expression
    arms: list["MatchArm"]

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_match_expr(self)


@dataclass
class ConstructorCall(Expression):
    """Constructor call: Some(5), Cons(1, Nil)"""

    constructor: str
    arguments: list[Expression]

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_constructor_call(self)


@dataclass
class TupleExpr(Expression):
    """Tuple expression: (1, "hello", true)"""

    elements: list[Expression]

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_tuple_expr(self)


@dataclass
class TypedHole(Expression):
    """Typed hole: ?name: Type where constraint"""

    name: str
    hole_type: TypeAnnotation
    constraint: Expression | None = None

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_typed_hole(self)


@dataclass
class PropagateExpr(Expression):
    """Propagation operator: expr?

    Unwraps Option/Result values, early-returning on None/Err.
    """

    operand: Expression

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_propagate_expr(self)


@dataclass
class WithExpr(Expression):
    """Functional record update: expr with (field1: val1, field2: val2)"""

    target: Expression
    updates: list[tuple[str, Expression]]  # [(field_name, new_value)]

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_with_expr(self)


# =============================================================================
# Patterns (for pattern matching)
# =============================================================================


@dataclass
class Pattern(ASTNode):
    """Base class for patterns."""

    pass


@dataclass
class WildcardPattern(Pattern):
    """Wildcard pattern: _"""

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_wildcard_pattern(self)


@dataclass
class VariablePattern(Pattern):
    """Variable binding pattern: x"""

    name: str

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_variable_pattern(self)


@dataclass
class ConstructorPattern(Pattern):
    """Constructor pattern: Some(x), Cons(h, t)"""

    constructor: str
    subpatterns: list[Pattern]

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_constructor_pattern(self)


@dataclass
class LiteralPattern(Pattern):
    """Literal pattern: 0, "hello", true"""

    value: Union[int, float, str, bool]

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_literal_pattern(self)


@dataclass
class ListPattern(Pattern):
    """List pattern: [], [x], [x, y, z]"""

    elements: list[Pattern]

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_list_pattern(self)


@dataclass
class RestPattern(Pattern):
    """Rest/spread pattern in list matching: ...rest or ..."""

    name: str | None = None  # None for anonymous ..., "rest" for ...rest

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_rest_pattern(self)


# =============================================================================
# Statements
# =============================================================================


@dataclass
class LetStatement(Statement):
    """Immutable binding: let x: Int = 5  or  let x = 5 (inferred)"""

    name: str
    type_annotation: TypeAnnotation | None
    value: Expression

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_let_statement(self)


@dataclass
class VarStatement(Statement):
    """Mutable binding: var x: Int = 5  or  var x = 5 (inferred)"""

    name: str
    type_annotation: TypeAnnotation | None
    value: Expression

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_var_statement(self)


@dataclass
class TupleDestructureStatement(Statement):
    """Tuple destructuring: let (x, y): (Int, Int) = expr"""

    names: list[str]
    type_annotation: TypeAnnotation
    value: Expression
    mutable: bool

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_tuple_destructure(self)


@dataclass
class AssignStatement(Statement):
    """Assignment: x = 10"""

    target: str
    value: Expression

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_assign_statement(self)


@dataclass
class IndexAssignStatement(Statement):
    """Index assignment: arr[i] = value"""

    target: Expression
    index: Expression
    value: Expression

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_index_assign_statement(self)


@dataclass
class FieldAssignStatement(Statement):
    """Field assignment: obj.field = value"""

    target: Expression
    field_name: str
    value: Expression

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_field_assign_statement(self)


@dataclass
class IfStatement(Statement):
    """Conditional: if cond then ... else ... end if"""

    condition: Expression
    then_body: list[Statement]
    else_body: list[Statement]
    label: str | None = None  # Optional closing label

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_if_statement(self)


@dataclass
class WhileStatement(Statement):
    """While loop: while cond do ... end while"""

    condition: Expression
    body: list[Statement]
    label: str | None = None

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_while_statement(self)


@dataclass
class ForStatement(Statement):
    """For loop: for x: T in items do ... end for"""

    variable: str
    var_type: TypeAnnotation
    iterable: Expression
    body: list[Statement]
    label: str | None = None

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_for_statement(self)


@dataclass
class MatchArm:
    """A single arm in a match expression/statement."""

    pattern: Pattern
    body: list[Statement]
    location: SourceLocation
    guard: Expression | None = None


@dataclass
class MatchStatement(Statement):
    """Match statement: match x with | ... end match"""

    scrutinee: Expression
    arms: list[MatchArm]
    label: str | None = None

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_match_statement(self)


@dataclass
class ReturnStatement(Statement):
    """Return statement: return x"""

    value: Expression

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_return_statement(self)


@dataclass
class BreakStatement(Statement):
    """Break statement: break"""

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_break_statement(self)


@dataclass
class ContinueStatement(Statement):
    """Continue statement: continue"""

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_continue_statement(self)


@dataclass
class CatchClause:
    """Catch clause: catch e: String"""

    variable: str
    type_annotation: TypeAnnotation
    body: list[Statement]
    location: SourceLocation


@dataclass
class TryStatement(Statement):
    """Try/catch statement: try ... catch e: String ... end try"""

    try_body: list[Statement]
    catch_clause: CatchClause

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_try_statement(self)


@dataclass
class ExpressionStatement(Statement):
    """Expression as statement: f(x)"""

    expression: Expression

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_expression_statement(self)


@dataclass
class AssertStatement(Statement):
    """Assert statement inside test blocks: assert expr, assert a == b"""

    expression: Expression

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_assert_statement(self)


# =============================================================================
# Specifications
# =============================================================================


@dataclass
class RequiresClause(ASTNode):
    """Precondition: requires P"""

    condition: Expression

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_requires_clause(self)


@dataclass
class EnsuresClause(ASTNode):
    """Postcondition: ensures Q"""

    condition: Expression

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_ensures_clause(self)


@dataclass
class ExampleClause(ASTNode):
    """Example: example input -> output"""

    input_expr: Expression
    output_expr: Expression

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_example_clause(self)


@dataclass
class SpecBlock:
    """Collection of specification clauses."""

    requires: list[RequiresClause] = field(default_factory=list)
    ensures: list[EnsuresClause] = field(default_factory=list)
    examples: list[ExampleClause] = field(default_factory=list)


# =============================================================================
# Definitions
# =============================================================================


@dataclass
class Parameter:
    """Function parameter: x: Int or x: Int = default_value"""

    name: str
    param_type: TypeAnnotation
    location: SourceLocation
    default_value: Expression | None = None


@dataclass
class FunctionDef(Definition):
    """
    Function definition:
    func name(params) -> ReturnType
        specs
        body
    end func name
    """

    name: str
    params: list[Parameter]
    return_type: TypeAnnotation
    specs: SpecBlock
    body: list[Statement]
    closing_name: str | None = None  # For verification
    is_async: bool = False
    untested_reason: str | None = None  # @untested("reason") annotation
    exported: bool = False  # True when declared with `export func`
    effects: list[str] = field(default_factory=list)  # Effect annotations: with fs, io

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_function_def(self)


@dataclass
class ImportStatement(Definition):
    """Import statement: import ModuleName [as Alias]"""

    module_name: str
    alias: str | None = None  # import Foo as Bar → alias="Bar"

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_import_statement(self)


@dataclass
class TypeVariant:
    """A variant in a type definition: Some(value: T)"""

    name: str
    fields: list[tuple[str, TypeAnnotation]]  # (field_name, field_type)
    location: SourceLocation


@dataclass
class TypeDef(Definition):
    """
    Type definition:
    type Option[T] = Some(value: T) | None
    """

    name: str
    type_params: list[str]
    variants: list[TypeVariant]
    exported: bool = False  # True when declared with `export type`

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_type_def(self)


@dataclass
class TypeAlias(Definition):
    """
    Type alias:
    type Coordinate = Tuple[Int, Int]
    type Predicate = (Int) -> Bool
    """

    name: str
    type_params: list[str]
    target_type: TypeAnnotation
    exported: bool = False  # True when declared with `export type`

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_type_alias(self)


@dataclass
class TraitMethodSig:
    """A method signature inside a trait definition."""

    name: str
    params: list[Parameter]
    return_type: TypeAnnotation
    location: SourceLocation


@dataclass
class TraitDef(Definition):
    """
    Trait definition:
    trait Describable
        func describe(self: Self) -> String
    end trait
    """

    name: str
    methods: list[TraitMethodSig]

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_trait_def(self)


@dataclass
class ImplDef(Definition):
    """
    Impl block:
    impl Describable for Circle
        func describe(self: Circle) -> String
            ...
        end func
    end impl
    """

    trait_name: str
    target_type: str
    methods: list[FunctionDef]

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_impl_def(self)


# =============================================================================
# Test Block
# =============================================================================


@dataclass
class TestBlock(Definition):
    """Module-level test block: test "name" ... end test"""

    __test__ = False  # prevent pytest from collecting this dataclass

    name: str
    body: list[Statement]

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_test_block(self)


# =============================================================================
# Program
# =============================================================================


@dataclass
class Program(ASTNode):
    """A complete Geno program."""

    definitions: list[Definition]

    def accept(self, visitor: "ASTVisitor") -> Any:
        return visitor.visit_program(self)


# =============================================================================
# Visitor Pattern
# =============================================================================


class ASTVisitor(ABC):
    """
    Base visitor class for AST traversal.
    Subclass and override methods to implement tree processing.
    """

    def visit(self, node: ASTNode) -> Any:
        """Generic visit method."""
        return node.accept(self)

    # Types
    def visit_simple_type(self, node: SimpleType) -> None:
        pass

    def visit_function_type(self, node: FunctionType) -> None:
        pass

    # Expressions
    def visit_integer_literal(self, node: IntegerLiteral) -> None:
        pass

    def visit_float_literal(self, node: FloatLiteral) -> None:
        pass

    def visit_string_literal(self, node: StringLiteral) -> None:
        pass

    def visit_fstring_expr(self, node: "FStringExpr") -> None:
        pass

    def visit_boolean_literal(self, node: BooleanLiteral) -> None:
        pass

    def visit_identifier(self, node: Identifier) -> None:
        pass

    def visit_type_identifier(self, node: TypeIdentifier) -> None:
        pass

    def visit_list_literal(self, node: ListLiteral) -> None:
        pass

    def visit_list_comprehension(self, node: ListComprehension) -> None:
        pass

    def visit_throw_expression(self, node: ThrowExpression) -> None:
        pass

    def visit_await_expr(self, node: AwaitExpr) -> None:
        pass

    def visit_binary_op(self, node: BinaryOp) -> None:
        pass

    def visit_unary_op(self, node: UnaryOp) -> None:
        pass

    def visit_function_call(self, node: FunctionCall) -> None:
        pass

    def visit_index_access(self, node: IndexAccess) -> None:
        pass

    def visit_field_access(self, node: FieldAccess) -> None:
        pass

    def visit_pipeline(self, node: Pipeline) -> None:
        pass

    def visit_placeholder(self, node: PlaceholderExpr) -> None:
        pass

    def visit_lambda(self, node: LambdaExpr) -> None:
        pass

    def visit_match_expr(self, node: MatchExpr) -> None:
        pass

    def visit_constructor_call(self, node: ConstructorCall) -> None:
        pass

    def visit_tuple_expr(self, node: TupleExpr) -> None:
        pass

    def visit_typed_hole(self, node: TypedHole) -> None:
        pass

    def visit_propagate_expr(self, node: "PropagateExpr") -> None:
        pass

    def visit_with_expr(self, node: "WithExpr") -> None:
        pass

    # Patterns
    def visit_wildcard_pattern(self, node: WildcardPattern) -> None:
        pass

    def visit_variable_pattern(self, node: VariablePattern) -> None:
        pass

    def visit_constructor_pattern(self, node: ConstructorPattern) -> None:
        pass

    def visit_literal_pattern(self, node: LiteralPattern) -> None:
        pass

    def visit_list_pattern(self, node: ListPattern) -> None:
        pass

    def visit_rest_pattern(self, node: "RestPattern") -> None:
        pass

    # Statements
    def visit_let_statement(self, node: LetStatement) -> None:
        pass

    def visit_var_statement(self, node: VarStatement) -> None:
        pass

    def visit_tuple_destructure(self, node: "TupleDestructureStatement") -> None:
        pass

    def visit_assign_statement(self, node: AssignStatement) -> None:
        pass

    def visit_index_assign_statement(self, node: IndexAssignStatement) -> None:
        pass

    def visit_field_assign_statement(self, node: FieldAssignStatement) -> None:
        pass

    def visit_if_statement(self, node: IfStatement) -> None:
        pass

    def visit_while_statement(self, node: WhileStatement) -> None:
        pass

    def visit_for_statement(self, node: ForStatement) -> None:
        pass

    def visit_match_statement(self, node: MatchStatement) -> None:
        pass

    def visit_return_statement(self, node: ReturnStatement) -> None:
        pass

    def visit_break_statement(self, node: "BreakStatement") -> None:
        pass

    def visit_continue_statement(self, node: "ContinueStatement") -> None:
        pass

    def visit_try_statement(self, node: "TryStatement") -> None:
        pass

    def visit_expression_statement(self, node: ExpressionStatement) -> None:
        pass

    # Specifications
    def visit_requires_clause(self, node: RequiresClause) -> None:
        pass

    def visit_ensures_clause(self, node: EnsuresClause) -> None:
        pass

    def visit_example_clause(self, node: ExampleClause) -> None:
        pass

    # Definitions
    def visit_function_def(self, node: FunctionDef) -> None:
        pass

    def visit_type_def(self, node: TypeDef) -> None:
        pass

    def visit_type_alias(self, node: "TypeAlias") -> None:
        pass

    def visit_import_statement(self, node: ImportStatement) -> None:
        pass

    def visit_trait_def(self, node: "TraitDef") -> None:
        pass

    def visit_impl_def(self, node: "ImplDef") -> None:
        pass

    def visit_test_block(self, node: "TestBlock") -> None:
        pass

    def visit_assert_statement(self, node: "AssertStatement") -> None:
        pass

    # Program
    def visit_program(self, node: Program) -> None:
        pass
