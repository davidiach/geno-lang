"""``geno test --backend interpreter|python|js|all`` (proposal 0024).

The compiled backends run every example and test block in compiled code, in a
subprocess, and report them in the interpreter runner's terms. ``all`` runs
the three and reports the cases whose outcome differs.
"""

import json
import shutil

import pytest

from geno.cli.test import run_tests
from geno.compiler import compile_to_python
from geno.harness import CaseOutcome, HarnessResult
from geno.js_compiler import compile_to_js
from geno.test_runner import (
    BackendComparison,
    FileTestResult,
    SuiteResult,
    default_test_sandbox_config,
    find_divergences,
    run_project_test_suite,
    run_test_suite,
)
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)

needs_node = pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
BACKENDS = ["interpreter", "python", pytest.param("js", marks=needs_node)]
COMPILED = ["python", pytest.param("js", marks=needs_node)]

MIXED = """
type Shape = Circle(r: Float) | Square(side: Int)

trait Area
    func area(self: Self) -> Float
end trait

impl Area for Shape
    func area(self: Shape) -> Float
        example Circle(1.0) -> 3.14159
        example Square(2) -> 4.0
        match self with
            | Circle(r) -> return 3.14159 * r * r
            | Square(s) -> return s * s
        end match
    end func
end impl

func checked(n: Int) -> Int
    requires n >= 0
    ensures result != 7
    example 3 -> 3
    example -1 panics "Precondition"
    example 0 panics "zero"
    example 5 panics
    example 0 panics "other words"
    example -2 -> 0
    example 7 -> 7
    if n == 0 then
        panic("zero is not allowed")
    end if
    return n
end func

func third(xs: List[Int]) -> Int
    example [1, 2, 3] -> 3
    example [1] -> 0
    example [1, 2, 9] -> 3
    return xs[2]
end func

func average(xs: List[Float]) -> Float
    example [0.1, 0.2] -> 0.15
    example [1, 2] -> 1.5
    return sum(xs) / length(xs)
end func

func greet(name: String = "you") -> String
    example () -> "hi you"
    example "Al" -> "hi Al"
    return "hi " + name
end func

func pair(p: (Int, String)) -> String
    example (1, "a") -> "1a"
    return to_string(p[0]) + p[1]
end func

func add3(a: Int, b: Int, c: Float) -> Float
    example (1, 2, 3) -> 6.0
    return a + b + c
end func

func shout(s: String) -> Unit with io
    example "x" -> ()
    print(s)
end func

func describe(s: Shape) -> List[Option[String]]
    example Square(1) -> [Some("square"), None]
    example Circle(2) -> [None]
    match s with
        | Square(_) -> return [Some("square"), None]
        | Circle(_) -> return [Some("circle")]
    end match
end func

test "shapes"
    assert area(Square(3)) == 9.0
    assert checked(2) == 2
end test

test "fails"
    assert third([1, 2, 4]) == 3
end test
"""

# (kind, function, message) of each failure, in order. Messages the backends
# word differently are matched by the part they share.
MIXED_FAILURES = [
    ("example", "checked", "expected a panic, but it returned 5"),
    (
        "example",
        "checked",
        'expected a panic containing "other words", but it panicked with: '
        "zero is not allowed",
    ),
    ("requires", "checked", "Runtime error: Precondition failed: checked(n: -2)"),
    ("ensures", "checked", "Runtime error: Postcondition failed: checked(n: 7)"),
    ("example", "third", "Runtime error: Index 2 out of bounds"),
    ("example", "third", "expected 3, got 9"),
    ("example", "describe", 'expected [None], got [Some(value: "circle")]'),
    ("test", "fails", "Runtime Error: Assertion failed"),
]


def _write(tmp_path, source: str, name: str = "sample.geno"):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def _suite(path, backend: str, **options) -> SuiteResult:
    return run_test_suite([path], backend=backend, **options)


@pytest.mark.parametrize("backend", BACKENDS)
def test_every_backend_reports_the_same_passes_and_failures(tmp_path, backend):
    suite = _suite(_write(tmp_path, MIXED), backend)

    [result] = suite.file_results
    assert result.error is None
    harness = result.harness_result
    assert (harness.total, harness.passed, harness.failed) == (23, 15, 8)
    assert suite.backend == backend
    for violation, (kind, function, message) in zip(
        harness.violations, MIXED_FAILURES, strict=True
    ):
        assert (violation.kind, violation.function) == (kind, function)
        assert violation.message.startswith(message), violation.message


@needs_node
def test_all_backends_agree_on_the_mixed_file(tmp_path):
    path = _write(tmp_path, MIXED)
    suites = {name: _suite(path, name) for name in ("interpreter", "python", "js")}

    assert find_divergences(suites) == []
    outcomes = [
        [
            (outcome.kind, outcome.function, outcome.ordinal, outcome.passed)
            for outcome in suite.file_results[0].harness_result.outcomes
        ]
        for suite in suites.values()
    ]
    assert outcomes[0] == outcomes[1] == outcomes[2]


SLOW = """
func stuck(n: Int) -> Int
    example 1 -> 1
    example 0 -> 0
    example 2 -> 2
    var i = n
    while i == 0 do
        i = 0
    end while
    return n
end func

func after(n: Int) -> Int
    example 4 -> 4
    return n
end func
"""


@pytest.mark.parametrize("backend", COMPILED)
def test_a_case_past_its_timeout_is_killed_and_the_rest_still_run(tmp_path, backend):
    suite = _suite(
        _write(tmp_path, SLOW),
        backend,
        sandbox_config=default_test_sandbox_config(timeout=1.0),
    )

    harness = suite.file_results[0].harness_result
    assert (harness.total, harness.passed, harness.failed) == (4, 3, 1)
    [violation] = harness.violations
    assert violation.function == "stuck"
    assert violation.message == "Runtime error: Execution timed out after 1.0 seconds"


LIMITS = """
func deep(n: Int) -> Int
    example 100000 panics
    if n == 0 then
        return 0
    end if
    return 1 + deep(n - 1)
end func

func secret(name: String) -> String with env
    example "HOME" panics
    return env_get_or(name, "none")
end func
"""


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_limit_or_a_refusal_never_satisfies_a_panics_example(tmp_path, backend):
    """The compiled run is granted what `geno test` grants: no `env`."""
    harness = _suite(_write(tmp_path, LIMITS), backend).file_results[0].harness_result

    assert harness.failed == 2
    deep, secret = harness.violations
    assert "a resource limit is not a panic" in deep.message
    assert secret.message.startswith("expected a panic, but the call was refused")
    assert "env" in secret.message


FILTERED = """
type Box = Box(n: Int)

trait Size
    func size(self: Self) -> Int
end trait

impl Size for Box
    func size(self: Box) -> Int
        example Box(2) -> 2
        return self.n
    end func
end impl

func one(n: Int) -> Int
    example 1 -> 1
    return n
end func

func two(n: Int) -> Int
    example 2 -> 2
    example 3 -> 3
    return n
end func

test "one works"
    assert one(1) == 1
end test
"""


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(
    ("pattern", "total"),
    [
        ("two", 2),
        ("o*", 2),
        ("size", 1),
        ("Box.size", 1),
        ("Box.Size.size", 1),
        ("z", 0),
    ],
)
def test_filter_selects_the_same_cases_on_every_backend(
    tmp_path, backend, pattern, total
):
    suite = _suite(_write(tmp_path, FILTERED), backend, filter_pattern=pattern)

    assert suite.total == total
    assert suite.passed == total


def _project(tmp_path):
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "Main"\nfiles = ["Maths", "Main"]\n', encoding="utf-8"
    )
    _write(
        tmp_path,
        """
func hidden(n: Int) -> Int
    example 2 -> 4
    return n * 2
end func

export func double(n: Int) -> Int
    example 3 -> 6
    example 4 -> 9
    return hidden(n)
end func

test "private helper"
    assert hidden(5) == 10
end test
""",
        "Maths.geno",
    )
    _write(
        tmp_path,
        """
import Maths

func twice(n: Int) -> Int
    example 1 -> 4
    return Maths.double(Maths.double(n))
end func

func main() -> Unit with io
    print("main ran")
    panic("main must not run under geno test")
end func
""",
        "Main.geno",
    )
    return tmp_path


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_project_is_tested_module_by_module_and_main_does_not_run(tmp_path, backend):
    suite = run_project_test_suite(_project(tmp_path), backend=backend)

    assert [result.error for result in suite.file_results] == [None, None]
    by_name = {
        result.path.rsplit("/", 1)[-1]: result.harness_result
        for result in suite.file_results
    }
    maths, main = by_name["Maths.geno"], by_name["Main.geno"]
    assert (maths.total, maths.passed) == (4, 3)
    assert [v.message for v in maths.violations] == ["expected 9, got 8"]
    assert (main.total, main.passed) == (1, 1)


@needs_node
def test_all_reports_an_example_that_only_some_backends_pass(tmp_path, capsys):
    """Stack depth is the host's: 20,000 nested calls pass the interpreter's
    limit and Node's stack but fit compiled Python's."""
    path = _write(
        tmp_path,
        """
func depth(n: Int) -> Int
    example 3 -> 3
    example 20000 -> 20000
    if n == 0 then
        return 0
    end if
    return 1 + depth(n - 1)
end func
""",
    )

    with pytest.raises(SystemExit) as exit_info:
        run_tests(str(path), backend="all", json_output=True)

    payload = json.loads(capsys.readouterr().out)
    assert exit_info.value.code == 1
    assert payload["backend"] == "all"
    assert list(payload["backends"]) == ["interpreter", "python", "js"]
    assert [suite["backend"] for suite in payload["backends"].values()] == [
        "interpreter",
        "python",
        "js",
    ]
    assert payload["backends"]["python"]["files"][0]["backend"] == "python"
    assert payload["success"] is False
    [divergence] = payload["divergences"]
    assert (divergence["function"], divergence["ordinal"]) == ("depth", 1)
    assert divergence["outcomes"]["python"] == "passed"
    assert divergence["outcomes"]["interpreter"].startswith("failed: Runtime error:")
    assert divergence["outcomes"]["js"].startswith("failed: Runtime error:")


def _suite_of(backend: str, *outcomes: CaseOutcome) -> SuiteResult:
    harness = HarnessResult(outcomes=list(outcomes))
    return SuiteResult(
        backend=backend,
        file_results=[FileTestResult(path="m.geno", harness_result=harness)],
    )


def test_divergences_are_differing_verdicts_or_differing_wrong_values():
    passed = CaseOutcome("example", "f", 0, True)
    wrong_4 = CaseOutcome("example", "f", 0, False, "4", "expected 3, got 4")
    wrong_5 = CaseOutcome("example", "f", 0, False, "5", "expected 3, got 5")
    threw_a = CaseOutcome("example", "f", 0, False, None, "Runtime error: Index 2")
    threw_b = CaseOutcome("example", "f", 0, False, None, "Runtime error: index two")

    def differ(a: CaseOutcome, b: CaseOutcome) -> bool:
        suites = {"interpreter": _suite_of("interpreter", a), "js": _suite_of("js", b)}
        return bool(find_divergences(suites))

    assert differ(passed, wrong_4)
    assert differ(wrong_4, wrong_5)
    assert not differ(passed, passed)
    assert not differ(wrong_4, wrong_4)
    # The backends word some runtime errors differently: both still failed.
    assert not differ(threw_a, threw_b)


def test_same_named_test_blocks_are_compared_in_order():
    first = CaseOutcome("test", "t", 0, True)
    second = CaseOutcome("test", "t", 0, False, None, "Runtime Error: Assertion failed")
    suites = {
        "interpreter": _suite_of("interpreter", first, second),
        "python": _suite_of("python", first, first),
    }

    [divergence] = find_divergences(suites)
    assert (divergence.kind, divergence.function, divergence.ordinal) == (
        "test",
        "t",
        1,
    )


def test_a_file_one_backend_could_not_load_is_an_error_not_a_divergence():
    loaded = _suite_of("interpreter", CaseOutcome("example", "f", 0, True))
    broken = SuiteResult(
        backend="js",
        file_results=[FileTestResult(path="m.geno", error="Compile Error")],
    )
    comparison = BackendComparison(
        {"interpreter": loaded, "js": broken},
        find_divergences({"interpreter": loaded, "js": broken}),
    )

    assert comparison.divergences == []
    assert comparison.errors == 1
    assert comparison.success is False


def test_js_without_node_is_a_clear_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    path = _write(tmp_path, FILTERED)

    with pytest.raises(SystemExit) as exit_info:
        run_tests(str(path), backend="js")

    assert exit_info.value.code == 1
    output = capsys.readouterr().out
    assert "Backend Error: --backend js runs the compiled tests with Node.js" in output
    assert "`node` was not found on PATH" in output


@pytest.mark.parametrize("backend", ["python", "js", "all"])
def test_properties_run_on_the_interpreter_only(tmp_path, backend, capsys):
    path = _write(tmp_path, FILTERED)

    with pytest.raises(SystemExit) as exit_info:
        run_tests(str(path), backend=backend, properties=True)

    assert exit_info.value.code == 1
    assert "--properties runs on the interpreter only" in capsys.readouterr().err


def test_text_report_labels_a_compiled_backend(tmp_path, capsys):
    path = _write(tmp_path, FILTERED)

    with pytest.raises(SystemExit) as exit_info:
        run_tests(str(path), backend="python")

    lines = capsys.readouterr().out.splitlines()
    assert exit_info.value.code == 0
    assert lines[0] == "Backend: python"
    assert "(5/5)" in lines[1]


def test_a_plain_run_keeps_its_payload_and_a_compiled_one_names_its_backend(
    tmp_path, capsys
):
    path = _write(tmp_path, FILTERED)

    with pytest.raises(SystemExit):
        run_tests(str(path), json_output=True)
    plain = json.loads(capsys.readouterr().out)
    with pytest.raises(SystemExit):
        run_tests(str(path), json_output=True, backend="python")
    compiled = json.loads(capsys.readouterr().out)

    assert "backend" not in plain and "backend" not in plain["files"][0]
    assert (plain["total"], plain["passed"]) == (5, 5)
    assert compiled["backend"] == "python"
    assert compiled["files"][0]["backend"] == "python"
    assert (compiled["total"], compiled["passed"]) == (5, 5)


@pytest.mark.parametrize("backend", COMPILED)
def test_a_compile_error_is_reported_for_the_file(tmp_path, backend):
    """A local named after a runtime helper is refused before any case runs."""
    path = _write(
        tmp_path,
        "func f(n: Int) -> Int\n    example 1 -> 1\n    let _safe_index = n\n"
        "    return _safe_index\nend func\n",
    )

    [result] = _suite(path, backend).file_results

    assert result.harness_result is None
    assert result.error is not None and result.error.startswith(
        ("Compile Error:", "Type Error")
    )


@pytest.mark.parametrize("backend", COMPILED)
def test_a_program_that_fails_while_loading_is_one_error(
    tmp_path, backend, monkeypatch
):
    """A module constant is a literal, so a program cannot fail this way
    itself; the runner still has to say so if the host does."""
    from geno import compiled_tests

    real_build = compiled_tests._build
    broken = {
        "python": "raise RuntimeError('broke while loading')\n",
        "js": "throw new Error('broke while loading');\n",
    }

    def build(dep_graph, name):
        built = real_build(dep_graph, name)
        return compiled_tests._Build(broken[name] + built.code, built.cases)

    monkeypatch.setattr(compiled_tests, "_build", build)

    [result] = _suite(_write(tmp_path, FILTERED), backend).file_results

    assert result.harness_result is None
    assert result.error is not None and result.error.startswith("Runtime Error:")
    assert "broke while loading" in result.error


WITH_TEST_BLOCK = """
func one(n: Int) -> Int
    example 1 -> 1
    return n
end func

test "one works"
    assert one(1) == 1
end test

func main() -> Unit with io
    print(one(1))
end func
"""


def test_an_ordinary_build_carries_no_test_harness():
    """Examples and test blocks are compiled only for `geno test`."""
    python = compile_to_python(WITH_TEST_BLOCK)
    javascript = compile_to_js(WITH_TEST_BLOCK)

    assert "_geno_tests" not in python.split("# Generated Code Follows")[-1]
    assert "_geno_tests" not in javascript
    assert "Assertion failed" not in python.split("# Generated Code Follows")[-1]
    assert _compiled_python_output(WITH_TEST_BLOCK) == "1\n"


UNIT = """
func nothing(n: Int) -> Unit
    example 0 -> ()
    if n > 0 then
        return ()
    end if
end func

func main() -> Unit with io
    let fell_off = nothing(0)
    let returned = nothing(1)
    print(fell_off == ())
    print(fell_off == returned)
    print([fell_off] == [returned])
end func
"""


@pytest.mark.parametrize(
    "backend",
    [
        _interpreter_output,
        _compiled_python_output,
        pytest.param(_compiled_js_output, marks=needs_node),
    ],
)
def test_unit_is_one_value_however_the_function_ended(backend):
    """Found by `--backend all`: on JavaScript a function that ended without a
    `return` yielded `undefined`, which did not equal `()`."""
    assert backend(UNIT) == "true\ntrue\ntrue\n"
