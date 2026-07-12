"""
Hypothesis strategies for generating valid Geno programs.

Each strategy produces a (source, expected_stdout) pair so that the
differential runner can compare backend outputs against a reference oracle.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from hypothesis import strategies as st

from .arith import (
    ArithmeticExpr,
    IntPredicateSpec,
    IntTransformSpec,
    LetBinding,
    ParityPrintStep,
    apply_transform,
    binding_ref,
    combine,
    geno_int_literal,
    if_print_step,
    lit,
    predicate_source,
    print_step,
    test_predicate,
    transform_source,
)

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GeneratedProgram:
    """A generated Geno program with its expected stdout."""

    source: str
    expected_output: str


# ---------------------------------------------------------------------------
# Leaf strategies
# ---------------------------------------------------------------------------

LEAF_EXPR = st.integers(min_value=-9, max_value=9).map(lit)

INT_TRANSFORM = st.builds(
    IntTransformSpec,
    operator=st.sampled_from(["+", "-", "*"]),
    constant=st.integers(min_value=-4, max_value=4),
)

INT_PREDICATE = st.builds(
    IntPredicateSpec,
    operator=st.sampled_from(["==", "!=", "<", "<=", ">", ">="]),
    constant=st.integers(min_value=-6, max_value=6),
)


# ---------------------------------------------------------------------------
# Expression strategies
# ---------------------------------------------------------------------------


def expr_strategy(
    extra_terms: list[ArithmeticExpr] | None = None,
    max_leaves: int = 8,
) -> st.SearchStrategy[ArithmeticExpr]:
    """Build an expression strategy with optional bound-name terminals."""
    leaf = LEAF_EXPR
    if extra_terms:
        leaf = st.one_of(leaf, st.sampled_from(extra_terms))
    return st.recursive(
        leaf,
        lambda inner: st.one_of(
            st.builds(combine, st.just("+"), inner, inner),
            st.builds(combine, st.just("-"), inner, inner),
            st.builds(combine, st.just("*"), inner, inner),
            st.builds(
                combine,
                st.just("/"),
                inner,
                inner.filter(lambda e: e.value != 0),
            ),
            st.builds(
                combine,
                st.just("%"),
                inner,
                inner.filter(lambda e: e.value != 0),
            ),
        ),
        max_leaves=max_leaves,
    )


# ---------------------------------------------------------------------------
# Statement / print-step strategies
# ---------------------------------------------------------------------------


def step_strategy(
    extra_terms: list[ArithmeticExpr] | None = None,
) -> st.SearchStrategy[ParityPrintStep]:
    """Build a parity-step strategy using the provided bound expressions."""
    exprs = expr_strategy(extra_terms)
    return st.one_of(
        exprs.map(print_step),
        st.builds(
            if_print_step,
            st.sampled_from(["==", "!=", "<", "<=", ">", ">="]),
            exprs,
            exprs,
            exprs,
            exprs,
        ),
    )


# ---------------------------------------------------------------------------
# Program builders
# ---------------------------------------------------------------------------


def _build_arith_program(
    bindings: list[LetBinding], steps: list[ParityPrintStep]
) -> GeneratedProgram:
    """Build a tiny Geno program with optional let bindings and print steps."""
    binding_lines = [f"    let {b.name}: Int = {b.expr.source}" for b in bindings]
    step_lines = [s.source for s in steps]
    body = "\n".join(binding_lines + step_lines)
    expected = "".join(f"{v}\n" for s in steps for v in s.outputs)
    source = f"func main() -> Unit\n{body}\n    return ()\nend func\n"
    return GeneratedProgram(source=source, expected_output=expected)


def _build_loop_program(xs: list[int], transform: IntTransformSpec) -> GeneratedProgram:
    """Build a program with a for loop and accumulation."""
    xs_source = ", ".join(geno_int_literal(x) for x in xs)
    t_src = transform_source(transform, "item")
    total = 0
    for x in xs:
        total += apply_transform(transform, x)
    source = f"""\
func main() -> Unit
    var total: Int = 0
    for item: Int in [{xs_source}] do
        total = total + {t_src}
    end for
    print(total)
    print({geno_int_literal(len(xs))})
    return ()
end func
"""
    expected = f"{total}\n{len(xs)}\n"
    return GeneratedProgram(source=source, expected_output=expected)


def _build_while_program(limit: int) -> GeneratedProgram:
    """Build a program with a while loop counting down."""
    total = 0
    i = limit
    while i > 0:
        total += i
        i -= 1
    source = f"""\
func main() -> Unit
    var total: Int = 0
    var i: Int = {geno_int_literal(limit)}
    while i > 0 do
        total = total + i
        i = i - 1
    end while
    print(total)
    return ()
end func
"""
    expected = f"{total}\n"
    return GeneratedProgram(source=source, expected_output=expected)


def _build_match_program(value: int, arms: list[tuple[int, int]]) -> GeneratedProgram:
    """Build a program with integer match/with."""
    arm_lines = "\n".join(
        f"        | {pat} -> print({geno_int_literal(result)})" for pat, result in arms
    )
    # Determine expected output
    matched = False
    for pat, result in arms:
        if pat == value:
            expected = f"{result}\n"
            matched = True
            break
    if not matched:
        expected = "-1\n"
    source = f"""\
func main() -> Unit
    let x: Int = {geno_int_literal(value)}
    match x with
{arm_lines}
        | _ -> print({geno_int_literal(-1)})
    end match
    return ()
end func
"""
    return GeneratedProgram(source=source, expected_output=expected)


def _build_adt_program(tag: str, left: int, right: int) -> GeneratedProgram:
    """Build a program with a custom ADT and pattern matching."""
    if tag == "Wrap":
        ctor = f"Wrap({geno_int_literal(left)})"
        score = left + 1
        detail = left
    elif tag == "Pair":
        ctor = f"Pair({geno_int_literal(left)}, {geno_int_literal(right)})"
        score = left + right
        detail = left - right
    else:
        ctor = "Empty"
        score = 0
        detail = 0
    source = f"""\
type Payload = Wrap(value: Int) | Pair(a: Int, b: Int) | Empty

@untested("generated")
func payload_score(p: Payload) -> Int
    match p with
        | Wrap(v) -> return v + 1
        | Pair(a, b) -> return a + b
        | Empty -> return 0
    end match
end func

func main() -> Unit
    let p: Payload = {ctor}
    print(payload_score(p))
    match p with
        | Wrap(v) -> print(v)
        | Pair(a, b) -> print(a - b)
        | Empty -> print(0)
    end match
    return ()
end func
"""
    expected = f"{score}\n{detail}\n"
    return GeneratedProgram(source=source, expected_output=expected)


def _build_option_result_program(
    opt_tag: str,
    opt_val: int,
    res_tag: str,
    res_val: int,
) -> GeneratedProgram:
    """Build a program exercising Option and Result."""
    if opt_tag == "some":
        opt_src = f"Some({geno_int_literal(opt_val)})"
        opt_score = opt_val + 5
    else:
        opt_src = "None"
        opt_score = -5
    if res_tag == "ok":
        res_src = f"Ok({geno_int_literal(res_val)})"
        res_score = res_val * 2
    else:
        res_src = f"Err({geno_int_literal(res_val)})"
        res_score = 0 - res_val
    source = f"""\
func main() -> Unit
    let maybe: Option[Int] = {opt_src}
    match maybe with
        | Some(v) -> print(v + 5)
        | None -> print(0 - 5)
    end match
    let outcome: Result[Int, Int] = {res_src}
    match outcome with
        | Ok(v) -> print(v * 2)
        | Err(e) -> print(0 - e)
    end match
    return ()
end func
"""
    expected = f"{opt_score}\n{res_score}\n"
    return GeneratedProgram(source=source, expected_output=expected)


def _build_closure_program(
    xs: list[int], transform: IntTransformSpec, predicate: IntPredicateSpec
) -> GeneratedProgram:
    """Build a program with map/filter/fold and closures."""
    xs_source = ", ".join(geno_int_literal(x) for x in xs)
    t_src = transform_source(transform, "x")
    p_src = predicate_source(predicate, "x")
    mapped = [apply_transform(transform, x) for x in xs]
    filtered = [x for x in xs if test_predicate(predicate, x)]
    folded = 0
    for x in xs:
        folded += apply_transform(transform, x)
    source = f"""\
func main() -> Unit
    let xs: List[Int] = [{xs_source}]
    let mapped: List[Int] = map(xs, fn(x: Int) -> {t_src})
    let filtered: List[Int] = filter(xs, fn(x: Int) -> {p_src})
    let folded: Int = fold(list: xs, initial: 0, reducer: fn(acc: Int, x: Int) -> acc + {t_src})
    print(length(mapped))
    print(length(filtered))
    print(folded)
    if length(mapped) > 0 then
        print(head(mapped))
    else
        print(0)
    end if
    return ()
end func
"""
    expected_values = [
        len(mapped),
        len(filtered),
        folded,
        mapped[0] if mapped else 0,
    ]
    expected = "".join(f"{v}\n" for v in expected_values)
    return GeneratedProgram(source=source, expected_output=expected)


def _build_recursive_program(n: int) -> GeneratedProgram:
    """Build a program with a recursive function (factorial-style)."""

    def fact(k: int) -> int:
        if k <= 1:
            return 1
        return k * fact(k - 1)

    result = fact(n)
    source = f"""\
@untested("generated")
func factorial(n: Int) -> Int
    if n <= 1 then
        return 1
    end if
    return n * factorial(n - 1)
end func

func main() -> Unit
    print(factorial({geno_int_literal(n)}))
    return ()
end func
"""
    expected = f"{result}\n"
    return GeneratedProgram(source=source, expected_output=expected)


def _build_nested_if_program(depth: int, base_val: int) -> GeneratedProgram:
    """Build a program with deeply nested if/else.

    Uses a helper function so the nested if/else can use ``return``
    statements — wrapping ``return`` inside ``print()`` is invalid Geno.
    """
    # Build nested if/else body for the helper function.
    # Each level: if <level> < <level+1> then ... else return <level>
    # The condition is always true, so the expected value is always base_val.
    body = f"return {geno_int_literal(base_val)}"
    expected_val = base_val
    for i in range(depth):
        # Condition is always true: i < i+1
        body = (
            f"if {i} < {i + 1} then\n"
            f"            {body}\n"
            f"        else\n"
            f"            return {geno_int_literal(i)}\n"
            f"        end if"
        )
    source = f"""\
@untested("generated")
func nested_val() -> Int
    {body}
end func

func main() -> Unit
    print(nested_val())
    return ()
end func
"""
    expected = f"{expected_val}\n"
    return GeneratedProgram(source=source, expected_output=expected)


def _build_closure_capture_program(outer_val: int, inner_add: int) -> GeneratedProgram:
    """Build a program testing closure capture of outer variables."""
    result = outer_val + inner_add
    source = f"""\
@untested("generated")
func make_adder(n: Int) -> (Int) -> Int
    return fn(x: Int) -> x + n
end func

func main() -> Unit
    let add_n: (Int) -> Int = make_adder({geno_int_literal(outer_val)})
    print(add_n({geno_int_literal(inner_add)}))
    return ()
end func
"""
    expected = f"{result}\n"
    return GeneratedProgram(source=source, expected_output=expected)


# ---------------------------------------------------------------------------
# Composite strategies
# ---------------------------------------------------------------------------


@st.composite
def arith_program(draw: Any) -> GeneratedProgram:
    """Generate an arithmetic program with optional let bindings."""
    binding_count = draw(st.integers(min_value=0, max_value=3))
    bindings: list[LetBinding] = []
    bound_terms: list[ArithmeticExpr] = []

    for index in range(binding_count):
        expr = draw(expr_strategy(bound_terms))
        binding = LetBinding(name=f"x{index}", expr=expr)
        bindings.append(binding)
        bound_terms.append(binding_ref(binding))

    s = step_strategy(bound_terms)
    steps = draw(st.lists(s, min_size=0, max_size=3))
    if bound_terms:
        forced_expr = draw(st.sampled_from(bound_terms))
        steps = [print_step(forced_expr)] + steps
    elif not steps:
        steps = [draw(step_strategy())]

    return _build_arith_program(bindings, steps)


@st.composite
def loop_program(draw: Any) -> GeneratedProgram:
    """Generate a for-loop program."""
    xs = draw(st.lists(st.integers(min_value=-6, max_value=6), min_size=0, max_size=6))
    transform = draw(INT_TRANSFORM)
    return _build_loop_program(xs, transform)


@st.composite
def while_program(draw: Any) -> GeneratedProgram:
    """Generate a while-loop countdown program."""
    limit = draw(st.integers(min_value=0, max_value=20))
    return _build_while_program(limit)


@st.composite
def match_program(draw: Any) -> GeneratedProgram:
    """Generate a match expression program.

    The value range (0..12) intentionally exceeds the arm range (0..10)
    so that ~17% of cases exercise the wildcard ``_ ->`` fallback arm.
    """
    arm_values = draw(
        st.lists(
            st.integers(min_value=0, max_value=10), min_size=1, max_size=5, unique=True
        )
    )
    arms = [(v, v * 10) for v in arm_values]
    value = draw(st.integers(min_value=0, max_value=12))
    return _build_match_program(value, arms)


@st.composite
def adt_program(draw: Any) -> GeneratedProgram:
    """Generate an ADT program."""
    tag = draw(st.sampled_from(["Wrap", "Pair", "Empty"]))
    left = draw(st.integers(min_value=-6, max_value=6))
    right = draw(st.integers(min_value=-6, max_value=6))
    return _build_adt_program(tag, left, right)


@st.composite
def option_result_program(draw: Any) -> GeneratedProgram:
    """Generate an Option/Result program."""
    opt_tag = draw(st.sampled_from(["some", "none"]))
    opt_val = draw(st.integers(min_value=-8, max_value=8))
    res_tag = draw(st.sampled_from(["ok", "err"]))
    res_val = draw(st.integers(min_value=-8, max_value=8))
    return _build_option_result_program(opt_tag, opt_val, res_tag, res_val)


@st.composite
def closure_program(draw: Any) -> GeneratedProgram:
    """Generate a closure program with map/filter/fold."""
    xs = draw(st.lists(st.integers(min_value=-6, max_value=6), min_size=0, max_size=5))
    transform = draw(INT_TRANSFORM)
    predicate = draw(INT_PREDICATE)
    return _build_closure_program(xs, transform, predicate)


@st.composite
def recursive_program(draw: Any) -> GeneratedProgram:
    """Generate a recursive factorial program."""
    n = draw(st.integers(min_value=0, max_value=10))
    return _build_recursive_program(n)


@st.composite
def nested_if_program(draw: Any) -> GeneratedProgram:
    """Generate a deeply-nested if/else program."""
    depth = draw(st.integers(min_value=1, max_value=8))
    base_val = draw(st.integers(min_value=-20, max_value=20))
    return _build_nested_if_program(depth, base_val)


@st.composite
def closure_capture_program(draw: Any) -> GeneratedProgram:
    """Generate a closure-capture program."""
    outer = draw(st.integers(min_value=-10, max_value=10))
    inner = draw(st.integers(min_value=-10, max_value=10))
    return _build_closure_capture_program(outer, inner)


# ---------------------------------------------------------------------------
# Top-level strategy: mix of all program types
# ---------------------------------------------------------------------------


def geno_program() -> st.SearchStrategy[GeneratedProgram]:
    """Top-level strategy that draws from all program generators.

    Weights bias toward edge-case-heavy generators (ADTs, closures,
    nested constructs) while keeping simpler arithmetic as a baseline.
    """
    return st.one_of(
        arith_program(),  # arithmetic + let bindings + if/else
        loop_program(),  # for loops
        while_program(),  # while loops
        match_program(),  # match/with on integers
        adt_program(),  # custom ADTs
        option_result_program(),  # Option/Result
        closure_program(),  # map/filter/fold + closures
        recursive_program(),  # recursion
        nested_if_program(),  # deep nesting
        closure_capture_program(),  # closure capture
    )


def _random_transform(rng: random.Random) -> IntTransformSpec:
    """Draw an arithmetic transform using a seeded Python RNG."""
    return IntTransformSpec(
        operator=rng.choice(["+", "-", "*"]),
        constant=rng.randint(-4, 4),
    )


def _random_predicate(rng: random.Random) -> IntPredicateSpec:
    """Draw a predicate using a seeded Python RNG."""
    return IntPredicateSpec(
        operator=rng.choice(["==", "!=", "<", "<=", ">", ">="]),
        constant=rng.randint(-6, 6),
    )


def _random_expr(
    rng: random.Random,
    extra_terms: list[ArithmeticExpr] | None = None,
    *,
    depth: int = 0,
    max_depth: int = 3,
) -> ArithmeticExpr:
    """Draw an arithmetic expression using a seeded Python RNG."""
    extra_terms = extra_terms or []
    leaves = [lit(rng.randint(-9, 9)), *extra_terms]
    if depth >= max_depth or (leaves and rng.random() < 0.45):
        return rng.choice(leaves)

    left = _random_expr(rng, extra_terms, depth=depth + 1, max_depth=max_depth)
    operator = rng.choice(["+", "-", "*", "/", "%"])
    right = _random_expr(rng, extra_terms, depth=depth + 1, max_depth=max_depth)
    while operator in {"/", "%"} and right.value == 0:
        right = _random_expr(rng, extra_terms, depth=depth + 1, max_depth=max_depth)
    return combine(operator, left, right)


def _random_step(
    rng: random.Random, extra_terms: list[ArithmeticExpr] | None = None
) -> ParityPrintStep:
    """Draw a print or if/else print step using a seeded Python RNG."""
    if rng.random() < 0.6:
        return print_step(_random_expr(rng, extra_terms))
    return if_print_step(
        rng.choice(["==", "!=", "<", "<=", ">", ">="]),
        _random_expr(rng, extra_terms),
        _random_expr(rng, extra_terms),
        _random_expr(rng, extra_terms),
        _random_expr(rng, extra_terms),
    )


def draw_program(rng: random.Random) -> GeneratedProgram:
    """Draw one generated program deterministically from a seeded RNG.

    The pytest property tests still use Hypothesis strategies directly.
    This helper exists for the CLI path, where reproducibility matters.
    """
    builders = [
        "arith",
        "loop",
        "while",
        "match",
        "adt",
        "option_result",
        "closure",
        "recursive",
        "nested_if",
        "closure_capture",
    ]
    builder = rng.choice(builders)

    if builder == "arith":
        binding_count = rng.randint(0, 3)
        bindings: list[LetBinding] = []
        bound_terms: list[ArithmeticExpr] = []
        for index in range(binding_count):
            expr = _random_expr(rng, bound_terms)
            binding = LetBinding(name=f"x{index}", expr=expr)
            bindings.append(binding)
            bound_terms.append(binding_ref(binding))

        step_count = rng.randint(0, 3)
        steps = [_random_step(rng, bound_terms) for _ in range(step_count)]
        if bound_terms:
            steps = [print_step(rng.choice(bound_terms)), *steps]
        elif not steps:
            steps = [_random_step(rng)]
        return _build_arith_program(bindings, steps)

    if builder == "loop":
        xs = [rng.randint(-6, 6) for _ in range(rng.randint(0, 6))]
        return _build_loop_program(xs, _random_transform(rng))

    if builder == "while":
        return _build_while_program(rng.randint(0, 20))

    if builder == "match":
        arm_count = rng.randint(1, 5)
        arm_values = rng.sample(list(range(0, 11)), k=arm_count)
        arms = [(v, v * 10) for v in arm_values]
        return _build_match_program(rng.randint(0, 12), arms)

    if builder == "adt":
        return _build_adt_program(
            rng.choice(["Wrap", "Pair", "Empty"]),
            rng.randint(-6, 6),
            rng.randint(-6, 6),
        )

    if builder == "option_result":
        return _build_option_result_program(
            rng.choice(["some", "none"]),
            rng.randint(-8, 8),
            rng.choice(["ok", "err"]),
            rng.randint(-8, 8),
        )

    if builder == "closure":
        xs = [rng.randint(-6, 6) for _ in range(rng.randint(0, 5))]
        return _build_closure_program(
            xs, _random_transform(rng), _random_predicate(rng)
        )

    if builder == "recursive":
        return _build_recursive_program(rng.randint(0, 10))

    if builder == "nested_if":
        return _build_nested_if_program(rng.randint(1, 8), rng.randint(-20, 20))

    return _build_closure_capture_program(rng.randint(-10, 10), rng.randint(-10, 10))
