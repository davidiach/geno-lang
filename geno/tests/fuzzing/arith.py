"""
Arithmetic helpers for program generation.

Extracted from test_backend_parity.py so they can be shared by
the differential fuzzing harness and the existing parity tests.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ArithmeticExpr:
    """One generated arithmetic expression plus its reference value."""

    source: str
    value: int


@dataclass(frozen=True)
class ParityPrintStep:
    """One generated statement plus its expected printed values."""

    source: str
    outputs: tuple[int, ...]


@dataclass(frozen=True)
class LetBinding:
    """One generated immutable binding plus its resolved value."""

    name: str
    expr: ArithmeticExpr


@dataclass(frozen=True)
class IntTransformSpec:
    """One small unary Int transform used in generated list lambdas."""

    operator: str
    constant: int


@dataclass(frozen=True)
class IntPredicateSpec:
    """One small Int predicate used in generated list lambdas."""

    operator: str
    constant: int


def geno_int_literal(n: int) -> str:
    """Render an integer literal without relying on unary-minus parsing."""
    return str(n) if n >= 0 else f"(0 - {abs(n)})"


def lit(n: int) -> ArithmeticExpr:
    """Build a literal expression."""
    return ArithmeticExpr(geno_int_literal(n), n)


def trunc_div(a: int, b: int) -> int:
    """Integer division with truncation toward zero."""
    quotient = abs(a) // abs(b)
    if (a < 0) ^ (b < 0):
        quotient = -quotient
    return quotient


def trunc_mod(a: int, b: int) -> int:
    """Modulo paired with truncating integer division."""
    return a - (b * trunc_div(a, b))


def combine(op: str, left: ArithmeticExpr, right: ArithmeticExpr) -> ArithmeticExpr:
    """Combine two generated expressions under one arithmetic operator."""
    if op == "+":
        value = left.value + right.value
    elif op == "-":
        value = left.value - right.value
    elif op == "*":
        value = left.value * right.value
    elif op == "/":
        value = trunc_div(left.value, right.value)
    elif op == "%":
        value = trunc_mod(left.value, right.value)
    else:
        raise ValueError(f"unknown op: {op}")
    return ArithmeticExpr(f"({left.source} {op} {right.source})", value)


def compare(op: str, left: ArithmeticExpr, right: ArithmeticExpr) -> tuple[str, bool]:
    """Build a comparison expression and its boolean value."""
    if op == "==":
        value = left.value == right.value
    elif op == "!=":
        value = left.value != right.value
    elif op == "<":
        value = left.value < right.value
    elif op == "<=":
        value = left.value <= right.value
    elif op == ">":
        value = left.value > right.value
    elif op == ">=":
        value = left.value >= right.value
    else:
        raise ValueError(f"unknown comparison op: {op}")
    return f"({left.source} {op} {right.source})", value


def print_step(expr: ArithmeticExpr) -> ParityPrintStep:
    """Build a single print step."""
    return ParityPrintStep(f"    print({expr.source})", (expr.value,))


def if_print_step(
    op: str,
    left: ArithmeticExpr,
    right: ArithmeticExpr,
    then_expr: ArithmeticExpr,
    else_expr: ArithmeticExpr,
) -> ParityPrintStep:
    """Build a small if/else print step and its expected output."""
    condition_source, condition_value = compare(op, left, right)
    chosen = then_expr if condition_value else else_expr
    source = (
        f"    if {condition_source} then\n"
        f"        print({then_expr.source})\n"
        f"    else\n"
        f"        print({else_expr.source})\n"
        f"    end if"
    )
    return ParityPrintStep(source, (chosen.value,))


def binding_ref(binding: LetBinding) -> ArithmeticExpr:
    """Treat one binding name as an expression with the bound value."""
    return ArithmeticExpr(binding.name, binding.expr.value)


def apply_transform(spec: IntTransformSpec, value: int) -> int:
    """Apply one generated unary Int transform."""
    if spec.operator == "+":
        return value + spec.constant
    if spec.operator == "-":
        return value - spec.constant
    if spec.operator == "*":
        return value * spec.constant
    raise ValueError(f"unknown transform op: {spec.operator}")


def test_predicate(spec: IntPredicateSpec, value: int) -> bool:
    """Evaluate one generated Int predicate."""
    if spec.operator == "==":
        return value == spec.constant
    if spec.operator == "!=":
        return value != spec.constant
    if spec.operator == "<":
        return value < spec.constant
    if spec.operator == "<=":
        return value <= spec.constant
    if spec.operator == ">":
        return value > spec.constant
    if spec.operator == ">=":
        return value >= spec.constant
    raise ValueError(f"unknown predicate op: {spec.operator}")


def transform_source(spec: IntTransformSpec, var_name: str) -> str:
    """Render one unary Int transform as Geno source."""
    return f"({var_name} {spec.operator} {geno_int_literal(spec.constant)})"


def predicate_source(spec: IntPredicateSpec, var_name: str) -> str:
    """Render one Int predicate as Geno source."""
    return f"({var_name} {spec.operator} {geno_int_literal(spec.constant)})"
