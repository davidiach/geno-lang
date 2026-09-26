"""Behavioral regressions for lexical binding and effect-soundness review findings."""

import pytest

from geno import RunConfig, check, run
from geno.parser import parse
from geno.typechecker import TypeChecker
from geno.types import FuncType, IntType, TypeEnv
from geno.values import Environment


def _reject(source: str, message: str) -> None:
    result = check(source)
    assert not result.ok, source
    assert any(message in diagnostic.message for diagnostic in result.diagnostics), (
        result.diagnostics
    )


def _value(source: str, expected: object) -> None:
    result = run(source)
    assert result.ok, result.diagnostics
    assert result.value == expected


@pytest.mark.parametrize("keyword", ["let", "var"])
@pytest.mark.parametrize("annotated", [False, True])
def test_live_cell_rebinding_cannot_change_captured_type(
    keyword: str, annotated: bool
) -> None:
    first_type = ": Int" if annotated else ""
    next_type = ": String" if annotated else ""
    _reject(
        f"""
func main() -> Int
    {keyword} x{first_type} = 1
    let f = fn() -> x
    {keyword} x{next_type} = "oops"
    return f()
end func
""",
        "Cannot rebind 'x'",
    )


@pytest.mark.parametrize("keyword", ["let", "var"])
def test_tuple_rebinding_cannot_change_captured_type(keyword: str) -> None:
    _reject(
        f"""
func main() -> Int
    let x = 1
    let f = fn() -> x
    {keyword} (x, unused): (String, Int) = ("oops", 0)
    return f()
end func
""",
        "Cannot rebind 'x'",
    )


@pytest.mark.parametrize("types", [("Int", "Float"), ("Float", "Int")])
def test_rebinding_keeps_numeric_cells_invariant(types: tuple[str, str]) -> None:
    first, second = types
    _reject(
        f"""
func main() -> Int
    let x: {first} = 1
    let x: {second} = 2
    return 0
end func
""",
        "Cannot rebind 'x'",
    )


@pytest.mark.parametrize(
    "scrutinee, pattern",
    [
        ("Some(1)", "Some(x)"),
        ("(1, 2)", "(x, y)"),
        ("[1]", "[x]"),
        ("1", "x"),
    ],
)
def test_pattern_cells_cannot_change_type_after_capture(
    scrutinee: str, pattern: str
) -> None:
    _reject(
        f"""
func main() -> Int
    match {scrutinee} with
    | {pattern} ->
        let f = fn() -> x
        let x = "oops"
        return f()
    | _ -> return 0
    end match
end func
""",
        "Cannot rebind 'x'",
    )


@pytest.mark.parametrize(
    "scrutinee, pattern",
    [('(1, "oops")', "(x, x)"), ("[1, 2]", "[x, ...x]")],
)
def test_pattern_cannot_overwrite_a_binding_with_an_incompatible_type(
    scrutinee: str, pattern: str
) -> None:
    _reject(
        f"""
func main() -> Int
    match {scrutinee} with
    | {pattern} -> return 0
    | _ -> return 1
    end match
end func
""",
        "Cannot rebind 'x'",
    )


def test_rebinding_cannot_change_a_captured_function_effect_contract() -> None:
    _reject(
        """
func noisy() -> Int with io
    example () -> 1
    print("side effect")
    return 1
end func
func main() -> Int
    let f = fn() -> 1
    let captured = fn() -> f()
    let f = noisy
    return captured()
end func
""",
        "existing bindings must retain their type and effects",
    )


@pytest.mark.parametrize("keyword", ["let", "var"])
def test_same_type_live_cell_rebinding_remains_observable(keyword: str) -> None:
    _value(
        f"""
func main() -> Int
    {keyword} x = 1
    let captured = fn() -> x
    {keyword} x = 2
    return captured()
end func
""",
        2,
    )


def test_nested_shadowing_can_use_a_different_type() -> None:
    _value(
        """
func main() -> Int
    let x = 1
    let captured = fn() -> x
    if true then
        let x = "separate cell"
        let size = length(x)
    end if
    return captured()
end func
""",
        1,
    )


@pytest.mark.parametrize("annotated", [False, True])
def test_late_first_shadow_cannot_change_an_existing_closures_type(
    annotated: bool,
) -> None:
    annotation = ": String" if annotated else ""
    _reject(
        f"""
let x = 1
func main() -> Int
    let captured = fn() -> x
    let x{annotation} = "oops"
    return captured()
end func
""",
        "Cannot rebind 'x'",
    )


def test_late_first_shadow_preserves_same_type_live_capture() -> None:
    _value(
        """
let x = 1
func main() -> Int
    let captured = fn() -> x
    let x = 2
    return captured()
end func
""",
        2,
    )


@pytest.mark.parametrize("call", ["f()", "f(x: 1)", "f(1)"])
def test_late_global_callable_shadow_cannot_replace_captured_metadata(
    call: str,
) -> None:
    _reject(
        f"""
func f(x: Int = 1) -> Int
    example () -> 1
    return x
end func
func main() -> Int
    let captured = fn() -> {call}
    let f = fn(y: Int) -> y
    return captured()
end func
""",
        "Cannot shadow global callable 'f' after a closure captured it",
    )


def test_same_type_local_function_cells_can_still_be_rebound() -> None:
    _value(
        """
func main() -> Int
    let f = fn(x: Int) -> x
    let captured = fn() -> f(1)
    let f = fn(y: Int) -> y + 1
    return captured()
end func
""",
        2,
    )


@pytest.mark.parametrize(
    "replacement",
    ["let x = 2", "let x: Int = 2", "let (x, y): (Int, Int) = (2, 3)"],
)
def test_immutable_rebinding_clears_prior_mutability(replacement: str) -> None:
    _reject(
        f"""
func main() -> Int
    var x = 1
    {replacement}
    x = 3
    return x
end func
""",
        "Cannot assign to immutable variable: x",
    )


def test_runtime_and_type_environments_reset_mutability_of_the_same_cell() -> None:
    static = TypeEnv()
    runtime = Environment()
    static.bind("x", IntType(), mutable=True)
    runtime.bind("x", 1, mutable=True)
    static.bind("x", IntType())
    runtime.bind("x", 2)
    assert not static.is_mutable("x")
    assert not runtime.child().assign("x", 3)
    assert runtime.lookup("x") == 2
    static.bind("x", IntType(), mutable=True)
    runtime.bind("x", 4, mutable=True)
    assert static.is_mutable("x")
    assert runtime.child().assign("x", 5)
    assert runtime.lookup("x") == 5


@pytest.mark.parametrize(
    "initial, assignment, replacement",
    [
        ("var x = 1", "x = 2", "let x = 3"),
        ("var x = 1", "x = 2", "let (x, y): (Int, Int) = (3, 4)"),
        ("var x = array_new(1, 0)", "x[0] = 2", "let x = array_new(1, 3)"),
        ("var x = Box(1)", "x.value = 2", "let x = Box(3)"),
    ],
)
def test_immutable_rebinding_cannot_invalidate_an_existing_captured_writer(
    initial: str, assignment: str, replacement: str
) -> None:
    source = f"""
type Box = Box(value: Int)
func main() -> Int
    {initial}
    let setter = fn() do
        {assignment}
        return ()
    end fn
    {replacement}
    setter()
    return 0
end func
"""
    _reject(
        source, "Cannot make 'x' immutable after a closure captured it for mutation"
    )
    _value(source.replace(replacement, ""), 0)


def test_read_only_capture_allows_mutability_reset() -> None:
    _value(
        """
func main() -> Int
    var x = 1
    let reader = fn() -> x
    let x = 2
    return reader()
end func
""",
        2,
    )


@pytest.mark.parametrize("keyword", ["let", "var"])
def test_local_function_values_do_not_inherit_global_named_arguments(
    keyword: str,
) -> None:
    _reject(
        f"""
func f(x: Int, y: String) -> Int
    example (1, "ok") -> 1
    return x
end func
func main() -> Int
    {keyword} f = fn(y: Int, x: String) -> y
    return f(x: 1, y: "ok")
end func
""",
        "not for lambda expressions or function values",
    )


@pytest.mark.parametrize(
    "binding",
    [
        "let f = fn(x: Int) -> x",
        "let (f, n): ((Int) -> Int, Int) = (fn(x: Int) -> x, 0)",
    ],
)
def test_local_function_values_do_not_inherit_global_defaults(binding: str) -> None:
    _reject(
        f"""
func f(x: Int = 1) -> Int
    example () -> 1
    return x
end func
func main() -> Int
    {binding}
    return f()
end func
""",
        "expects 1 argument(s), but 0 were provided",
    )


def test_function_parameters_shadow_global_call_metadata() -> None:
    _reject(
        """
func f(x: Int = 1) -> Int
    example () -> 1
    return x
end func
func apply(f: (Int) -> Int) -> Int
    example (fn(x: Int) -> x) -> 1
    return f()
end func
func main() -> Int
    return apply(fn(x: Int) -> x)
end func
""",
        "expects 1 argument(s), but 0 were provided",
    )


def test_direct_call_metadata_and_positional_function_values_still_work() -> None:
    _value(
        """
func f(x: Int = 1, y: Int = 2) -> Int
    example (1, 2) -> 3
    return x + y
end func
func main() -> Int
    let before = f(y: 4)
    let f = fn(y: Int, x: Int) -> y - x
    return before + f(9, 2)
end func
""",
        12,
    )


@pytest.mark.parametrize("annotated", [False, True])
@pytest.mark.parametrize("block", [False, True])
@pytest.mark.parametrize("container", ["Option[Int]", "Result[Int, String]"])
def test_lambda_propagation_cannot_borrow_enclosing_return_type(
    annotated: bool, block: bool, container: str
) -> None:
    annotation = f": ({container}) -> Int" if annotated else ""
    body = "do\n        return x?\n    end fn" if block else "-> x?"
    returned = "Some(1)" if container.startswith("Option") else "Ok(1)"
    _reject(
        f"""
func main() -> {container}
    let f{annotation} = fn(x: {container}) {body}
    return {returned}
end func
""",
        "requires enclosing lambda to return",
    )


@pytest.mark.parametrize("annotated", [False, True])
@pytest.mark.parametrize("block", [False, True])
@pytest.mark.parametrize(
    "container, wrapper, argument, expected",
    [
        (
            "Option[Int]",
            "Some",
            "Some(7)",
            {"_constructor": "Some", "fields": {"value": 7}},
        ),
        ("Option[Int]", "Some", "None", {"_constructor": "None", "fields": {}}),
        (
            "Result[Int, String]",
            "Ok",
            "Ok(7)",
            {"_constructor": "Ok", "fields": {"value": 7}},
        ),
        (
            "Result[Int, String]",
            "Ok",
            'Err("bad")',
            {"_constructor": "Err", "fields": {"error": "bad"}},
        ),
    ],
)
def test_lambda_infers_its_own_valid_propagation_contract(
    annotated: bool,
    block: bool,
    container: str,
    wrapper: str,
    argument: str,
    expected: object,
) -> None:
    annotation = f": ({container}) -> {container}" if annotated else ""
    body = (
        f"do\n        let y = x?\n        return {wrapper}(y)\n    end fn"
        if block
        else f"-> {wrapper}(x?)"
    )
    # The enclosing function has no propagation-compatible return contract.
    # Success therefore requires inference owned by the lambda itself.
    source = f"""
func main() -> Int
    let f{annotation} = fn(x: {container}) {body}
    let value = f({argument})
    return 0
end func
"""
    _value(source, 0)
    _value(
        source.replace("main() -> Int", f"main() -> {container}").replace(
            "return 0", "return value"
        ),
        expected,
    )


def test_lambda_result_propagation_preserves_its_error_type() -> None:
    _reject(
        """
func main() -> Int
    let f: (Result[Int, String]) -> Result[Int, Int] = fn(x: Result[Int, String]) do
        let value = x?
        return Err(1)
    end fn
    return 0
end func
""",
        "Error type mismatch in '?': lambda returns",
    )


def test_lambda_cannot_mix_option_and_result_propagation() -> None:
    _reject(
        """
func main() -> Int
    let f = fn(x: Option[Int], y: Result[Int, String]) -> Some(x? + y?)
    return 0
end func
""",
        "requires enclosing lambda to return Result type",
    )


@pytest.mark.parametrize("annotated", [False, True])
@pytest.mark.parametrize("control", ["break", "continue"])
def test_lambda_cannot_use_an_enclosing_loop(annotated: bool, control: str) -> None:
    annotation = ": () -> Unit" if annotated else ""
    _reject(
        f"""
func main() -> Int
    while true do
        let f{annotation} = fn() do
            {control}
        end fn
        return 1
    end while
    return 0
end func
""",
        f"'{control}' outside of loop",
    )


@pytest.mark.parametrize("annotated", [False, True])
@pytest.mark.parametrize("enclosing", ["func main", "async func caller"])
def test_lambda_cannot_use_enclosing_async_permission(
    annotated: bool, enclosing: str
) -> None:
    annotation = ": () -> Int" if annotated else ""
    _reject(
        f"""
async func value() -> Int
    return 1
end func
{enclosing}() -> Int
    let f{annotation} = fn() -> await value()
    return 1
end func
""",
        "'await' can only be used inside an async function or main",
    )


def test_lambda_context_restoration_preserves_its_parent_and_own_loops() -> None:
    _value(
        """
async func value() -> Int
    return 7
end func
func main() -> Option[Int]
    while true do
        let f = fn() do
            while true do
                break
            end while
            return 1
        end fn
        let nested = fn() do
            let inner = fn() -> "unrelated return type"
            return f()
        end fn
        let value = nested()
        break
    end while
    let x = await value()
    let y = Some(x)?
    return Some(y)
end func
""",
        {"_constructor": "Some", "fields": {"value": 7}},
    )


@pytest.mark.parametrize("second_type", ["Int", "String"])
def test_duplicate_constructor_fields_are_rejected(second_type: str) -> None:
    _reject(
        f"""
type Pair = Pair(x: Int, x: {second_type})
func main() -> Int
    return 0
end func
""",
        "Duplicate field name 'x' in constructor 'Pair'",
    )


def test_unique_fields_and_fields_shared_across_variants_remain_valid() -> None:
    _value(
        """
type Pair = Pair(x: Int, y: String)
type Choice = First(x: Int) | Second(x: Int)
func main() -> Int
    let pair = Pair(1, "ok")
    let choice: Choice = Second(2)
    return pair.x + choice.x
end func
""",
        3,
    )


@pytest.mark.parametrize("second_type", ["Int", "String"])
def test_lambda_parameter_names_are_unique(second_type: str) -> None:
    _reject(
        f"""
func main() -> Int
    let f = fn(x: Int, x: {second_type}) -> x
    return 0
end func
""",
        "Duplicate parameter name 'x'",
    )


_EFFECT_HELPERS = """
type Box = Box(value: Int)
func noisy() -> Int with io
    example () -> 0
    print("side effect")
    return 0
end func
func source() -> Array[Int] with io
    example () -> array_new(1, 0)
    print("side effect")
    return array_new(1, 0)
end func
func box() -> Box with io
    example () -> Box(0)
    print("side effect")
    return Box(0)
end func
func pipe(x: Int) -> Int with io
    example 0 -> 0
    print("side effect")
    return x
end func
func apply(f: () -> Int with io) -> Int with io
    example noisy -> 0
    return f()
end func
"""


@pytest.mark.parametrize(
    "body",
    [
        "let x = noisy()",
        "if noisy() == 0 then\n        let x = 1\n    end if",
        "var xs = array_new(1, 0)\n    xs[noisy()] = 1",
        "var sources = [source]\n    sources[0]()[0] = 1",
        "var boxes = [box]\n    boxes[0]().value = 1",
        "let b = Box(noisy())",
        "let x = 0 |> pipe",
        "let x = apply(fn() -> noisy())",
        "let xs = [noisy()]",
        "let xs = (noisy(), 0)",
        'let x = f"{noisy()}"',
        "let x = -noisy()",
        "let x = [0][noisy()]",
        "let x = box().value",
        "let b = Box(0) with (value: noisy())",
        "let xs = [x for x: Int in [0] if noisy() == 0]",
        "match 0 with\n    | x when noisy() == 0 -> let y = x\n    | _ -> let y = 0\n    end match",
    ],
)
def test_effects_follow_evaluated_expression_positions(body: str) -> None:
    source = (
        _EFFECT_HELPERS
        + f"""
func main() -> Int with mutation
    {body}
    return 0
end func
"""
    )
    _reject(source, "performs undeclared effects: io")
    allowed = check(
        source.replace("main() -> Int with mutation", "main() -> Int with mutation, io")
    )
    assert allowed.ok, allowed.diagnostics


def test_pure_assignment_positions_remain_pure_and_mutation_is_recorded() -> None:
    source = """
func offset() -> Int
    example () -> 0
    return 0
end func
func main() -> Int with mutation
    var (x, y): (Int, Int) = (0, 1)
    var xs = array_new(1, 0)
    xs[offset()] = y
    return xs[x]
end func
"""
    _value(source, 1)
    _reject(source.replace("with mutation", "with io"), "undeclared effects: mutation")


@pytest.mark.parametrize("explicit_default", [False, True])
def test_default_expression_effects_are_part_of_function_contract(
    explicit_default: bool,
) -> None:
    call = "f(2)" if explicit_default else "f()"
    source = (
        _EFFECT_HELPERS
        + f"""
func f(x: Int = noisy()) -> Int
    example () -> 0
    return x
end func
func main() -> Int with mutation
    return {call}
end func
"""
    )
    _reject(source, "performs undeclared effects: io")
    checker = TypeChecker()
    checker.check_program(parse(source.replace("with mutation", "with mutation, io")))
    function_type = checker.global_env.lookup("f")
    assert isinstance(function_type, FuncType)
    assert function_type.effects == frozenset({"io"})


def test_effectful_assignment_still_requires_runtime_capability() -> None:
    source = (
        _EFFECT_HELPERS
        + """
func main() -> Int with mutation, io
    var xs = array_new(1, 0)
    xs[noisy()] = 42
    return xs[0]
end func
"""
    )
    denied = run(source, config=RunConfig(check_examples=False))
    assert not denied.ok
    allowed = run(
        source, config=RunConfig(capabilities={"print"}, check_examples=False)
    )
    assert allowed.ok, allowed.diagnostics
    assert allowed.value == 42
    assert allowed.output == "side effect\n"


_TRAIT_HELPERS = """
type Item = Item(n: Int)
trait Describe
    func describe(self: Self, prefix: String) -> String
end trait
impl Describe for Item
    func describe(self: Item, prefix: String) -> String with io
        example (Item(1), "a") -> "a"
        print("side effect")
        return prefix
    end func
end impl
"""


@pytest.mark.parametrize(
    "arguments",
    [
        'Item(1), "hello"',
        'self: Item(1), prefix: "hello"',
        'prefix: "hello", self: Item(1)',
    ],
)
def test_trait_effect_dispatch_is_independent_of_named_argument_order(
    arguments: str,
) -> None:
    source = (
        _TRAIT_HELPERS
        + f"""
func main() -> String with mutation
    return describe({arguments})
end func
"""
    )
    _reject(source, "performs undeclared effects: io")
    allowed = source.replace("with mutation", "with mutation, io")
    result = run(
        allowed, config=RunConfig(capabilities={"print"}, check_examples=False)
    )
    assert result.ok, result.diagnostics
    assert result.value == "hello"
    assert result.output == "side effect\n"


def test_lexical_function_shadowing_does_not_invoke_trait_dispatch() -> None:
    _value(
        _TRAIT_HELPERS
        + """
func main() -> Int with mutation
    let describe = fn(x: Int) -> x + 1
    return describe(2)
end func
""",
        3,
    )
