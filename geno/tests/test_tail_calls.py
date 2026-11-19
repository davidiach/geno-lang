"""Self tail calls run in constant stack on every backend (proposal 0022).

A function's call to itself in tail position replaces the running call, so a
tail-recursive function is as deep as a loop is long. Everything a call does
is kept: argument order, defaults, named arguments, `requires` and `ensures`
for every logical call, and the bindings a closure captured.
"""

import os
import subprocess
import sys
import tempfile

import pytest

import geno
from geno.ast_nodes import FunctionDef
from geno.compiler import compile_to_python
from geno.interpreter import Interpreter
from geno.js_compiler import compile_to_js
from geno.parser import parse
from geno.sandbox import SandboxConfig
from geno.tail_calls import plan_tail_calls
from geno.tests._script_runner import GENERATED_PROGRAM_TIMEOUT
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _compiled_js_output,
    _compiled_js_project_output,
    _compiled_python_output,
    _compiled_python_project_output,
    _interpreter_project_output,
)
from geno.typechecker import TypeChecker
from geno.values import (
    ArrayValue,
    ConstructorValue,
    ContractViolationError,
    RuntimeLimitError,
    VecValue,
)


def _interpreter_output(source: str) -> str:
    """Run *source* on the interpreter, with room for the deep programs here."""
    result = geno.run(
        source,
        config=geno.RunConfig(timeout=120.0, max_steps=10**9, capabilities={"print"}),
    )
    assert result.ok, [diagnostic.message for diagnostic in result.diagnostics]
    return str(result.output)


BACKENDS = [
    _interpreter_output,
    _compiled_python_output,
    pytest.param(
        _compiled_js_output,
        marks=pytest.mark.skipif(not HAS_NODE, reason="Node.js not available"),
    ),
]

# Past each backend's stack: the interpreter allows 10,000 calls, compiled
# Python about 60,000 host frames, and Node far fewer. The interpreter gets
# the smaller depth because it is the slow one, most of all under coverage.
DEEP = 70_000
INTERPRETER_DEEP = 10_500


def _depth(backend) -> int:
    interpreted = (_interpreter_output, _interpreter_project_output)
    return INTERPRETER_DEEP if backend in interpreted else DEEP


def _at(depth: int, template: str) -> str:
    """*template* with its `{DEEP}` placeholders set to *depth*."""
    return template.replace("{DEEP}", str(depth))


POSITIONS = """
func by_return(n: Int, acc: Int) -> Int
    example (3, 0) -> 3
    if n == 0 then
        return acc
    end if
    return by_return(n - 1, acc + 1)
end func

func by_else(n: Int, acc: Int) -> Int
    example (3, 0) -> 3
    if n == 0 then
        return acc
    else
        return by_else(n - 1, acc + 1)
    end if
end func

func by_match(xs: List[Int], n: Int, acc: Int) -> Int
    example ([1], 2, 0) -> 2
    match n with
        | 0 -> return acc
        | _ -> return by_match(xs: xs, n: n - 1, acc: acc + length(xs))
    end match
end func

func by_if_expr(n: Int, acc: Float) -> Float
    example (2, 0.0) -> 2.0
    return if n == 0 then acc else by_if_expr(n - 1, acc + 1) end if
end func

func by_match_expr(n: Int, acc: Int) -> Int
    example (2, 0) -> 2
    return match n with
        | 0 -> acc
        | _ -> by_match_expr(n - 1, acc + 1)
    end match
end func

func by_if_let(left: Option[Int], acc: Int) -> Int
    example (Some(2), 0) -> 2
    if let Some(n) = left then
        return by_if_let(if n == 1 then None else Some(n - 1) end if, acc + 1)
    end if
    return acc
end func

func in_loop(n: Int, acc: Int) -> Int
    example (2, 0) -> 2
    for step in [1, 2] do
        if n == 0 then
            return acc
        end if
        if step == 2 then
            return in_loop(n - 1, acc + 1)
        end if
    end for
    return -1
end func

func last_statement(n: Int, seen: Vec[Int]) -> Unit
    example (0, vec_new()) -> ()
    if n > 0 then
        if n == 1 then
            vec_push(seen, n)
        end if
        last_statement(n - 1, seen)
    end if
end func

func with_defaults(n: Int, acc: Int = 0, step: Int = 1) -> Int
    example (2) -> 2
    if n == 0 then
        return acc
    end if
    return with_defaults(n - 1, acc + step)
end func

func out_of_order(n: Int, acc: Int, step: Int) -> Int
    example (2, 0, 1) -> 2
    if n == 0 then
        return acc
    end if
    return out_of_order(step: step, acc: acc + step, n: n - 1)
end func

func generic[T](n: Int, value: T) -> T
    example (2, "x") -> "x"
    if n == 0 then
        return value
    end if
    return generic(n - 1, value)
end func

func main() -> Unit with io
    print(by_return({DEEP}, 0))
    print(by_else({DEEP}, 0))
    print(by_match(xs: [7, 8], n: {DEEP}, acc: 0))
    print(by_if_expr({DEEP}, 0.0))
    print(by_match_expr({DEEP}, 0))
    print(by_if_let(Some({DEEP}), 0))
    print(in_loop({DEEP}, 0))
    let seen: Vec[Int] = vec_new()
    last_statement({DEEP}, seen)
    print(vec_length(seen))
    print(with_defaults({DEEP}))
    print(out_of_order(n: {DEEP}, acc: 0, step: 2))
    print(generic({DEEP}, "kept"))
    print(generic({DEEP}, 2.0))
end func
"""


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_tail_position_runs_deep(backend):
    deep = _depth(backend)
    expected = [
        deep,
        deep,
        2 * deep,
        f"{deep}.0",
        deep,
        deep,
        deep,
        1,
        deep,
        2 * deep,
        "kept",
        "2.0",
    ]
    assert backend(_at(deep, POSITIONS)) == "".join(f"{line}\n" for line in expected)


@pytest.mark.parametrize("backend", BACKENDS)
def test_unit_expression_statement_keeps_the_selected_tail_call(backend):
    source = """
func tick(n: Int) -> Unit with io
    example 0 -> ()
    if n >= 0 then
        print(n)
    end if
    (if n > 0 then tick(n - 1) else () end if)
end func

func main() -> Unit with io
    tick(2)
end func
"""
    assert backend(source) == "2\n1\n0\n"


def test_unit_alias_accepts_an_implicit_tail_call():
    source = """
type Nothing = Unit
func down(n: Int) -> Nothing
    example 0 -> ()
    if n > 0 then
        down(n - 1)
    end if
end func
func main() -> Unit
    down(100)
end func
"""
    result = geno.run(source, config=geno.RunConfig(max_recursion_depth=20))
    assert result.ok, result.diagnostics


SEMANTICS = """
func note(label: String, value: Int) -> Int with io
    example ("a", 1) -> 1
    print(label)
    return value
end func

func ordered(n: Int, a: Int, b: Int) -> Int with io
    example (0, 1, 2) -> 3
    if n == 0 then
        return a + b
    end if
    return ordered(b: note("b", b + 1), n: note("n", n - 1), a: note("a", a + 1))
end func

func swap(n: Int, a: Int, b: Int) -> Int
    example (1, 1, 2) -> 2
    if n == 0 then
        return a
    end if
    return swap(n: n - 1, a: b, b: a)
end func

func collect(n: Int, made: List[() -> Int]) -> List[() -> Int]
    example (0, []) -> []
    if n == 0 then
        return made
    end if
    let doubled = n * 2
    return collect(n - 1, append(made, fn() -> n + doubled))
end func

func counters(n: Int, made: List[() -> Int with mutation]) -> List[() -> Int with mutation]
    example (0, []) -> []
    if n == 0 then
        return made
    end if
    var count = n * 10
    let bump = fn() do
        count = count + 1
        return count
    end fn
    return counters(n - 1, append(made, bump))
end func

func rebinds(n: Int, acc: Int) -> Int
    example (2, 0) -> 3
    if n == 0 then
        return acc
    end if
    let acc = acc + n
    let n = n - 1
    return rebinds(n, acc)
end func

func shadows(n: Int, acc: Option[Int]) -> Int
    example (0, None) -> 0
    if n > 0 then
        let n = n - 1
        match acc with
            | Some(n) -> return shadows(0, Some(n + 100))
            | None -> return shadows(n, Some(n))
        end match
    end if
    return unwrap_or(acc, 0)
end func

func said(value: Int) -> Bool with io
    example (1) -> true
    print(f"checked {value}")
    return true
end func

func checked(n: Int) -> Int
    ensures said(result)
    example (0) -> 7
    if n == 0 then
        return 7
    end if
    return checked(n - 1)
end func

func main() -> Unit with io, mutation
    print(ordered(n: 2, a: 0, b: 0))
    print(swap(n: 3, a: 1, b: 2))
    for made in collect(3, []) do
        print(made())
    end for
    let bumps = counters(2, [])
    print(bumps[0]())
    print(bumps[0]())
    print(bumps[1]())
    print(rebinds(4, 0))
    print(shadows(3, None))
    print(checked(2))
end func
"""

SEMANTICS_OUTPUT = """b
n
a
b
n
a
4
2
9
6
3
21
22
11
10
102
checked 7
checked 7
checked 7
7
"""


@pytest.mark.parametrize("backend", BACKENDS)
def test_argument_order_swaps_and_closures_are_kept(backend):
    """Arguments are evaluated as written and assigned together, and a closure
    keeps the bindings of the logical call that made it."""
    assert backend(SEMANTICS) == SEMANTICS_OUTPUT


def _run_python(source: str) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8", newline="\n"
    ) as handle:
        handle.write(compile_to_python(source))
        path = handle.name
    try:
        return subprocess.run(
            [sys.executable, path, "--cap", "print"],
            capture_output=True,
            text=True,
            timeout=GENERATED_PROGRAM_TIMEOUT,
        )
    finally:
        os.unlink(path)


def _run_js(source: str) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".js", delete=False, encoding="utf-8", newline="\n"
    ) as handle:
        handle.write(compile_to_js(source))
        path = handle.name
    try:
        return subprocess.run(
            ["node", path, "--cap", "print"],
            capture_output=True,
            text=True,
            timeout=GENERATED_PROGRAM_TIMEOUT,
        )
    finally:
        os.unlink(path)


def _failure(backend: str, source: str) -> str:
    """The text of the failure *source* ends with on *backend*."""
    if backend == "interpreter":
        result = geno.run(
            source, config=geno.RunConfig(timeout=30.0, capabilities={"print"})
        )
        assert not result.ok
        return "\n".join(diagnostic.message for diagnostic in result.diagnostics)
    process = _run_python(source) if backend == "python" else _run_js(source)
    assert process.returncode != 0, process.stdout
    return process.stderr


FAILURE_BACKENDS = [
    "interpreter",
    "python",
    pytest.param(
        "js", marks=pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
    ),
]

REQUIRES = """
func halve(n: Int, steps: Int) -> Int
    requires n != 3
    example (4, 0) -> 2
    if n <= 1 then
        return steps
    end if
    return halve(n / 2, steps + 1)
end func

func main() -> Unit with io
    print(halve(8, 0))
    print(halve(12, 0))
end func
"""


@pytest.mark.parametrize("backend", FAILURE_BACKENDS)
def test_requires_is_checked_for_every_logical_call(backend):
    """12 -> 6 -> 3: the third call's arguments break the clause."""
    message = _failure(backend, REQUIRES)
    assert "requires" in message
    assert "n != 3" in message
    assert "halve(n: 3, steps: 2)" in message


ENSURES = """
func climb(n: Int, top: Int) -> Int
    ensures result - n != 2
    example (5, 5) -> 5
    if n == top then
        return n
    end if
    return climb(n + 1, top)
end func

func main() -> Unit with io
    print(climb(4, 5))
    print(climb(0, 5))
end func
"""


@pytest.mark.parametrize("backend", FAILURE_BACKENDS)
def test_ensures_is_checked_for_every_logical_call_innermost_first(backend):
    """Every call returns 5; the call with n = 3 is the one that breaks the
    clause, though only the last call computes the result."""
    message = _failure(backend, ENSURES)
    assert "ensures" in message
    assert "climb(n: 3, top: 5)" in message
    assert "result was 5" in message


ENSURES_ORDER = """
func fall(n: Int) -> Int
    ensures result + n < 3
    example (0) -> 0
    if n == 0 then
        return 0
    end if
    return fall(n - 1)
end func

func main() -> Unit with io
    print(fall(9))
end func
"""


@pytest.mark.parametrize("backend", FAILURE_BACKENDS)
def test_the_innermost_failing_ensures_is_the_one_reported(backend):
    """Calls with n = 3..9 all break the clause; n = 3 returns first."""
    message = _failure(backend, ENSURES_ORDER)
    assert "fall(n: 3)" in message


ENSURES_PASSING = """
func total(n: Int, acc: Int) -> Int
    ensures result >= acc
    ensures result >= 0
    example (2, 0) -> 3
    if n == 0 then
        return acc
    end if
    return total(n - 1, acc + n)
end func

func positive(n: Int, acc: Int) -> Int
    ensures result >= 0
    example (2, 0) -> 3
    if n == 0 then
        return acc
    end if
    return positive(n - 1, acc + n)
end func

func main() -> Unit with io
    print(total({DEEP}, 0))
    print(positive({DEEP}, 0))
end func
"""


@pytest.mark.parametrize("backend", BACKENDS)
def test_deep_tail_recursion_with_ensures(backend):
    deep = _depth(backend)
    expected = deep * (deep + 1) // 2
    assert backend(_at(deep, ENSURES_PASSING)) == f"{expected}\n{expected}\n"


def _plan(source: str, name: str = "f"):
    program = parse(source)
    for definition in program.definitions:
        if isinstance(definition, FunctionDef) and definition.name == name:
            return plan_tail_calls(definition)
    raise AssertionError(f"no function {name}")


def _function(body: str, header: str = "func f(n: Int) -> Int") -> str:
    return f"{header}\n    example (0) -> 0\n{body}\nend func\n"


@pytest.mark.parametrize(
    "body",
    [
        "    return 1 + f(n - 1)",
        "    let rest = f(n - 1)\n    return rest",
        "    return g(n - 1)",
        "    let again = fn() -> f(n - 1)\n    return again()",
        "    return n |> f",
        "    f(n - 1)\n    return 0",
        "    let f = fn(m: Int) -> m\n    return f(n - 1)",
        "    for f in [fn(m: Int) -> m] do\n        return f(n - 1)\n    end for\n"
        "    return 0",
    ],
)
def test_calls_that_are_not_self_tail_calls(body):
    assert _plan(_function(body)) is None


def test_a_propagated_call_is_not_a_tail_call():
    source = _function(
        "    return Ok(f(n - 1)?)", "func f(n: Int) -> Result[Int, String]"
    )
    assert _plan(source) is None


def test_an_async_function_has_no_tail_calls():
    source = _function("    return f(n - 1)", "async func f(n: Int) -> Int")
    assert _plan(source) is None


@pytest.mark.parametrize(
    ("body", "form", "snapshots"),
    [
        ("    return f(n - 1)", "loop", False),
        (
            "    if n > 0 then\n        return f(n - 1)\n    end if\n    return 0",
            "loop",
            False,
        ),
        ("    return if n > 0 then f(n - 1) else 0 end if", "trampoline", False),
        (
            "    while n > 0 do\n        return f(n - 1)\n    end while\n    return 0",
            "trampoline",
            False,
        ),
        ("    let k = fn() -> n\n    return f(k() - 1)", "trampoline", False),
    ],
)
def test_the_form_a_function_takes(body, form, snapshots):
    plan = _plan(_function(body))
    assert plan is not None
    assert (plan.form, plan.snapshots, len(plan.calls)) == (form, snapshots, 1)


def test_ensures_takes_the_trampoline_and_snapshots_only_for_parameters():
    reads = _plan(
        "func f(n: Int) -> Int\n    ensures result <= n\n    example (0) -> 0\n"
        "    return if n > 0 then f(n - 1) else 0 end if\nend func\n"
    )
    result_only = _plan(
        "func f(n: Int) -> Int\n    ensures result == 0\n    example (0) -> 0\n"
        "    return if n > 0 then f(n - 1) else 0 end if\nend func\n"
    )
    trivial = _plan(
        "func f(n: Int) -> Int\n    ensures true\n    example (0) -> 0\n"
        "    if n > 0 then\n        return f(n - 1)\n    end if\n    return 0\nend func\n"
    )
    calls = _plan(
        "func f(n: Int) -> Int\n    ensures abs(result) == 0\n    example (0) -> 0\n"
        "    return if n > 0 then f(n - 1) else 0 end if\nend func\n"
    )
    assert reads is not None and (reads.form, reads.snapshots) == ("trampoline", True)
    # A clause that calls a function may have an effect: every check runs.
    assert calls is not None and calls.snapshots
    assert result_only is not None
    assert (result_only.form, result_only.snapshots) == ("trampoline", False)
    assert trivial is not None and trivial.form == "loop"


def test_a_unit_function_ends_with_its_tail_call():
    source = (
        "func f(n: Int) -> Unit\n    example (0) -> ()\n"
        "    if n > 0 then\n        f(n - 1)\n    end if\nend func\n"
    )
    plan = _plan(source)
    assert plan is not None and plan.form == "loop"
    # The same call in a function with a result is followed by a `return`.
    assert (
        _plan(
            _function("    if n > 0 then\n        f(n - 1)\n    end if\n    return 0")
        )
        is None
    )


NOT_TAIL = """
func depth(n: Int) -> Int
    example (0) -> 0
    if n == 0 then
        return 0
    end if
    return 1 + depth(n - 1)
end func

func main() -> Unit with io
    print(depth(200))
end func
"""

TAIL = """
func depth(n: Int, acc: Int) -> Int
    example (0, 0) -> 0
    if n == 0 then
        return acc
    end if
    return depth(n - 1, acc + 1)
end func

func main() -> Unit with io
    print(depth(200, 0))
end func
"""


def test_only_tail_calls_escape_the_recursion_limit():
    config = geno.RunConfig(max_recursion_depth=50, capabilities={"print"})

    ordinary = geno.run(NOT_TAIL, config=config)
    tail = geno.run(TAIL, config=config)

    assert not ordinary.ok
    assert "recursion depth" in ordinary.diagnostics[0].message.lower()
    assert tail.ok, tail.diagnostics
    assert tail.output == "200\n"


FOREVER = """
func spin(n: Int) -> Int
    example (-1) -> 0
    if n < 0 then
        return 0
    end if
    return spin(n + 1)
end func

func main() -> Unit with io
    print(spin(0))
end func
"""


def test_a_tail_loop_still_meets_the_step_limit_and_the_timeout():
    stepped = geno.run(
        FOREVER, config=geno.RunConfig(max_steps=50_000, capabilities={"print"})
    )
    timed = geno.run(
        FOREVER,
        config=geno.RunConfig(timeout=0.2, max_steps=10**12, capabilities={"print"}),
    )

    assert not stepped.ok and "step" in stepped.diagnostics[0].message.lower()
    assert not timed.ok and "timed out" in timed.diagnostics[0].message.lower()


STACK = """
func drop(n: Int, xs: List[Int]) -> Int
    example (0, [1, 2, 3, 4, 5, 6]) -> 6
    if n == 0 then
        return xs[5]
    end if
    return drop(n - 1, xs)
end func

func main() -> Unit with io
    print(drop(40, [1]))
end func
"""


@pytest.mark.parametrize("backend", FAILURE_BACKENDS)
def test_a_failure_reports_one_frame_for_the_replaced_calls(backend, tmp_path):
    """The forty calls a tail call replaced leave no frames behind."""
    if backend == "interpreter":
        result = geno.run(STACK, config=geno.RunConfig(capabilities={"print"}))
        assert not result.ok
        stack = result.diagnostics[0].stack
        assert stack is not None
        assert [frame.function for frame in stack.frames] == ["main", "drop"]
        return
    report = _failure(backend, STACK)
    assert report.count(" in drop") == 1
    assert report.count(" in main") == 1
    assert "omitted" not in report


MODULE = """
export func count(n: Int, acc: Int) -> Int
    example (3, 0) -> 3
    if n == 0 then
        return acc
    end if
    return count(n - 1, acc + 1)
end func

export func total(n: Int, acc: Int = 0) -> Int
    ensures result >= acc
    example (2) -> 3
    return if n == 0 then acc else total(n - 1, acc + n) end if
end func
"""

IMPORTER = """
import Deep

func main() -> Unit with io
    print(Deep.count({DEEP}, 0))
    print(Deep.total({DEEP}))
end func
"""


@pytest.mark.parametrize(
    "backend",
    [
        _interpreter_project_output,
        _compiled_python_project_output,
        pytest.param(
            _compiled_js_project_output,
            marks=pytest.mark.skipif(not HAS_NODE, reason="Node.js not available"),
        ),
    ],
)
def test_a_function_of_an_imported_module_runs_deep(backend):
    deep = _depth(backend)
    expected = f"{deep}\n{deep * (deep + 1) // 2}\n"
    assert backend(_at(deep, IMPORTER), {"Deep": MODULE}) == expected


def _violation(source: str, name: str, arguments: list[int]) -> ContractViolationError:
    # Without `main`, which fails itself.
    program = parse(source.split("func main")[0])
    TypeChecker().check_program(program)
    interpreter = Interpreter(check_examples=False)
    interpreter.run(program)
    with pytest.raises(ContractViolationError) as raised:
        interpreter._call_function(interpreter.global_env.lookup(name), arguments)
    return raised.value


def test_a_violation_in_a_replaced_call_is_not_taken_for_the_first_calls():
    """An example or a generated input is rejected only by its own call's
    `requires` (depth 1). A tail call does not deepen the stack, so a later
    logical call's violation counts the calls it replaced."""
    first = _violation(REQUIRES, "halve", [3, 0])
    later = _violation(REQUIRES, "halve", [12, 0])
    first_ensures = _violation(ENSURES, "climb", [3, 5])
    later_ensures = _violation(ENSURES, "climb", [0, 5])

    assert first.call_depth == 1
    assert later.call_depth == 3  # 12 -> 6 -> 3
    assert first_ensures.call_depth == 1
    assert later_ensures.call_depth == 4  # the call with n = 3, after 0, 1, 2


DEFAULTS = """
func fresh() -> Int with io
    example () -> 0
    print("default")
    return 0
end func

func pick(n: Int, acc: Int = fresh()) -> Int with io
    example (0, 5) -> 5
    if n == 0 then
        return acc
    end if
    if n == 2 then
        return pick(n - 1, acc + 10)
    end if
    return pick(n - 1)
end func

func main() -> Unit with io
    print(pick(3))
end func
"""


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_default_is_evaluated_for_each_logical_call_that_omits_it(backend):
    """Calls with n = 3, 2 and 0 take the default; the one with n = 1 is
    given its argument."""
    assert backend(DEFAULTS) == "default\ndefault\ndefault\n0\n"


MIXED = """
func check(n: Int) -> Result[Int, String]
    example (1) -> Ok(1)
    if n == 13 then
        return Err("unlucky")
    end if
    return Ok(1)
end func

func tally(n: Int, acc: Int) -> Result[Int, String]
    example (0, 0) -> Ok(0)
    if n == 0 then
        return Ok(acc)
    end if
    let v = check(n)?
    return tally(n - 1, acc + v)
end func

func build(n: Int, acc: List[Int]) -> List[Int]
    example (0, []) -> []
    return if n == 0 then acc else build(n - 1, append(acc, n)) end if
end func

func text(n: Int, acc: String) -> String
    example (0, "") -> ""
    return match n with
        | 0 -> acc
        | _ -> text(n - 1, acc + "x")
    end match
end func

func widen(n: Int, acc: Float) -> Float
    example (0, 1.0) -> 1.0
    if n == 0 then
        return acc
    end if
    return widen(n - 1, n)
end func

func unit_closure(n: Int, out: Vec[() -> Int]) -> Unit
    example (0, vec_new()) -> ()
    if n > 0 then
        vec_push(out, fn() -> n)
        unit_closure(n - 1, out)
    end if
end func

func loops(n: Int, acc: Int) -> Int
    example (0, 0) -> 3
    var total = acc
    var i = 0
    while i < 3 do
        total = total + 1
        i = i + 1
    end while
    if n == 0 then
        return total
    end if
    return loops(n - 1, total)
end func

func main() -> Unit with io, mutation
    print(tally({DEEP}, 0))
    print(tally(5, 0))
    print(tally(20, 0))
    print(length(build(300, [])))
    print(build(3, []))
    print(length(text(300, "")))
    print(widen({DEEP}, 0.5))
    let out: Vec[() -> Int] = vec_new()
    unit_closure(3, out)
    print(vec_get(out, 0)() + vec_get(out, 2)())
    print(loops({DEEP}, 0))
end func
"""

MIXED_OUTPUT = """Err(error: "unlucky")
Ok(value: 5)
Err(error: "unlucky")
300
[3, 2, 1]
300
1.0
4
{TOTAL}
"""


@pytest.mark.parametrize("backend", BACKENDS)
def test_tail_calls_beside_propagation_collections_widening_and_loops(backend):
    """`?` in the body, a List or String result through an expression, an Int
    argument for a Float parameter, a `Unit` function that makes closures,
    and a body with its own loop."""
    deep = _depth(backend)
    expected = MIXED_OUTPUT.replace("{TOTAL}", str(3 * deep + 3))
    assert backend(_at(deep, MIXED)) == expected


# A call's binding walks its arguments for the collection limits. A call that
# passes on an argument of the call it is made from, a self tail call above
# all, does not walk that argument again: it passed the limits when that call
# was bound, and every write into it checks what it adds.

LONG_THREADED_LIST = """
func sum_from(xs: List[Int], i: Int, acc: Int) -> Int
    example ([1, 2], 1, 0) -> 2
    if i == 0 then
        return acc
    end if
    return sum_from(xs: xs, i: i - 1, acc: acc + xs[i])
end func

func main() -> Unit with io
    let xs = range(0, 20001)
    print(sum_from(xs: xs, i: 20000, acc: 0))
end func
"""


def test_a_tail_call_threading_a_long_list_runs_well_inside_the_timeout():
    """Each logical call walked the whole list again, so this took about
    forty seconds on the interpreter; it takes well under one now, and
    compiled Python a tenth of one. The test's own timeout leaves room for
    a slow, instrumented run."""
    result = geno.run(
        LONG_THREADED_LIST,
        config=geno.RunConfig(timeout=10.0, capabilities={"print"}),
    )

    assert result.ok, [diagnostic.message for diagnostic in result.diagnostics]
    assert result.output == "200010000\n"


THREADED = """
func sum_from(xs: List[Int], i: Int, acc: Int) -> Int
    example ([1, 2], 1, 0) -> 2
    if i == 0 then
        return acc
    end if
    return sum_from(xs: xs, i: i - 1, acc: acc + xs[i])
end func

func drain(n: Int, seen: Vec[Int]) -> Unit
    example (0, vec_new()) -> ()
    if n > 0 then
        vec_push(seen, n)
        drain(n - 1, seen)
    end if
end func

func at(xs: List[Int], i: Int) -> Int
    example ([1, 2], 0) -> 1
    return xs[i]
end func

func total(xs: List[Int], n: Int) -> Int
    example ([1, 2], 2) -> 3
    var acc = 0
    var i = 0
    while i < n do
        acc = acc + at(xs, i)
        i = i + 1
    end while
    return acc
end func

func count_down(xs: List[Int], i: Int) -> Int
    example ([1], 0) -> 1
    if i < 0 then
        return 0
    end if
    return 1 + count_down(xs, i - 1)
end func

func main() -> Unit with io, mutation
    let xs = range(0, {N})
    print(sum_from(xs: xs, i: {N} - 1, acc: 0))
    let seen: Vec[Int] = vec_new()
    drain({N}, seen)
    print(vec_length(seen))
    print(total(xs, {N}))
    print(count_down(xs, {N} - 1))
end func
"""


def test_a_call_does_not_walk_a_collection_it_passes_on_again(monkeypatch):
    """Self tail calls (`sum_from`, `drain`), calls from a loop (`at`) and an
    ordinary recursion (`count_down`) pass on the collection they were given.
    Each of their bindings walked it again, about 3.5 walks of a large
    collection per element; now only the calls from `main` walk it."""
    n = 2_000
    walked: list[str] = []
    check = Interpreter._check_collection_size

    def counting(self, kind, size, location):
        if size >= n // 2:  # the threaded collections, not the examples'
            walked.append(kind)
        check(self, kind, size, location)

    monkeypatch.setattr(Interpreter, "_check_collection_size", counting)
    output = _interpreter_output(THREADED.replace("{N}", str(n)))

    total = n * (n - 1) // 2
    assert output == f"{total}\n{n}\n{total}\n{n}\n"
    # `range`'s result, the calls from `main` that pass `xs`, and
    # `vec_length`'s argument before and after the call.
    assert len(walked) <= 10, walked


GROWING = """
func drain(n: Int, seen: Vec[Int]) -> Unit
    example (0, vec_new()) -> ()
    if n > 0 then
        vec_push(seen, n)
        drain(n - 1, seen)
    end if
end func

func fill(n: Int, seen: Set[Int]) -> Int
    example (0, set_new()) -> 0
    if n == 0 then
        return 0
    end if
    set_add(seen, n)
    return fill(n - 1, seen)
end func

func index(n: Int, m: MutableMap[Int, Int]) -> Int
    example (0, mutable_map_new()) -> 0
    if n == 0 then
        return 0
    end if
    mutable_map_set(map: m, key: n, value: n)
    return index(n - 1, m)
end func

func nested(n: Int, boxes: List[Vec[Int]]) -> Int
    example (0, [vec_new()]) -> 0
    if n == 0 then
        return 0
    end if
    vec_push(boxes[0], n)
    return nested(n - 1, boxes)
end func

func deepen(n: Int, seen: Vec[Int]) -> Int
    example (0, vec_new()) -> 0
    if n == 0 then
        return 0
    end if
    vec_push(seen, n)
    return 1 + deepen(n - 1, seen)
end func

func main() -> Unit with io, mutation
    {CALL}
end func
"""


@pytest.mark.parametrize(
    "call, message",
    [
        ("drain(150, vec_new())", "Vec size exceeds limit (101 > 100)"),
        ("print(fill(150, set_new()))", "Set size exceeds limit (101 > 100)"),
        (
            "print(index(150, mutable_map_new()))",
            "MutableMap size exceeds limit (101 > 100)",
        ),
        ("print(nested(150, [vec_new()]))", "Vec size exceeds limit (101 > 100)"),
        ("print(deepen(150, vec_new()))", "Vec size exceeds limit (101 > 100)"),
    ],
)
def test_a_collection_that_grows_across_calls_still_meets_the_limit(call, message):
    """A collection a call passes on unchanged is not walked again, but the
    write that grows it checks the size it grows to."""
    result = geno.run(
        GROWING.replace("{CALL}", call),
        config=geno.RunConfig(max_collection_size=100, capabilities={"print"}),
    )

    assert not result.ok
    assert result.diagnostics[0].message == message


FIELD_WRITE = """
type Box = Box(items: List[Int])

func fill(n: Int, boxes: Array[Box]) -> Int
    if n == 0 then
        return 0
    end if
    boxes[0].items = host_items
    return fill(n - 1, boxes)
end func
"""


def test_a_field_write_into_a_threaded_collection_checks_what_it_adds():
    """A value no walk has seen, here one the host bound, written into the
    array a tail call passes on was caught when the next call walked the
    array. The array is not walked again, so the write checks the value."""
    interpreter = Interpreter(
        check_examples=False, sandbox_config=SandboxConfig(max_collection_size=5)
    )
    interpreter.run(parse(FIELD_WRITE), execute_main=False)
    interpreter.global_env.bind("host_items", list(range(10)))
    boxes = ArrayValue([ConstructorValue("Box", {"items": []})])
    fill = interpreter.global_env.lookup("fill")

    with pytest.raises(RuntimeLimitError, match=r"List size exceeds limit \(10 > 5\)"):
        interpreter._call_function(fill, [3, boxes])


def test_a_known_value_is_skipped_only_as_a_root():
    """A new value that holds a known one is walked whole, so a mutable part
    still keeps the constructor around it from being marked as checked."""
    interpreter = Interpreter(sandbox_config=SandboxConfig(max_collection_size=5))
    big = list(range(10))
    seen = VecValue()
    box = ConstructorValue("Box", {"seen": seen})

    interpreter._check_collection_limits([big], None, known=[big])
    with pytest.raises(RuntimeLimitError, match="List size exceeds limit"):
        interpreter._check_collection_limits([[big]], None, known=[big])
    interpreter._check_collection_limits([box, seen], None, known=[seen])
    assert box._limits_checked is None
