"""Extern functions: typed, capability-gated host interop (proposal 0025).

An ``extern func`` declares a Geno signature and the host function behind it
for each target. Each test that runs code runs it on the interpreter,
compiled Python and compiled JavaScript, against host helpers written next to
the program, so the backends convert, validate and fail identically.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from geno import api
from geno.compiler import compile_to_python
from geno.diagnostics import ErrorCode
from geno.formatter import format_source
from geno.js_compiler import compile_to_js
from geno.parser import ParseError, parse
from geno.tests.test_backend_parity import HAS_NODE

needs_node = pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
BACKENDS = ["interpreter", "python", pytest.param("js", marks=needs_node)]

PY_HOST = """
import os


def echo(value):
    return value


def boom():
    raise ValueError("bad input")


def string_for_int():
    return "x"


def bool_for_int():
    return True


def int_for_float():
    return 3


def nested_bad():
    return [1, "two"]


def short_tuple():
    return (1,)


def int_keys():
    return {1: "a"}


def ordered_map():
    return {"b": 1, "a": 2}


def nothing():
    return None


def cycle():
    items = []
    items.append(items)
    return items


def args_seen(*args):
    return repr(args)


not_callable = 42
"""

JS_HOST = """
"use strict";
module.exports = {
    echo: value => value,
    boom: () => { throw new Error("bad input"); },
    stringForInt: () => "x",
    boolForInt: () => true,
    intForFloat: () => 3,
    nestedBad: () => [1, "two"],
    shortTuple: () => [1],
    intKeys: () => new Map([[1, "a"]]),
    orderedMap: () => ({ b: 1, a: 2 }),
    nothing: () => undefined,
    cycle: () => { const items = []; items.push(items); return items; },
    argsSeen: (...args) => JSON.stringify(args),
    notCallable: 42,
    counter: { value: 5, read() { return this.value; } },
};
"""

# Marker module: importing it leaves a file behind, so a test can tell
# whether a denied call imported host code.
PY_MARKER = """
import pathlib

pathlib.Path(__file__).with_name("imported.txt").write_text("yes")


def run():
    return 1
"""


def _extern(name: str, signature: str, python: str | None, js: str | None) -> str:
    lines = [f"extern func {name}{signature} with ffi"]
    if python is not None:
        lines.append(f'    python "{python}"')
    if js is not None:
        lines.append(f'    js "{js}"')
    lines.append("end func")
    return "\n".join(lines) + "\n"


def _echo(name: str, type_text: str) -> str:
    return _extern(
        name,
        f"(value: {type_text}) -> {type_text}",
        "ffi_host:echo",
        "./ffi_host.js#echo",
    )


@pytest.fixture
def host_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "ffi_host.py").write_text(PY_HOST, encoding="utf-8")
    (tmp_path / "ffi_host.js").write_text(JS_HOST, encoding="utf-8")
    (tmp_path / "ffi_marker.py").write_text(PY_MARKER, encoding="utf-8")
    (tmp_path / "ffi_marker.js").write_text(
        'require("fs").writeFileSync(__dirname + "/imported.txt", "yes");\n'
        "module.exports = { run: () => 1 };\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    # The helper modules have fixed names; drop any earlier test's copy.
    for name in ("ffi_host", "ffi_marker"):
        sys.modules.pop(name, None)
    from geno import _runtime_support

    _runtime_support._GENO_EXTERN_HOSTS.clear()
    return tmp_path


def _failure_line(stderr: str) -> str:
    for line in stderr.splitlines():
        if line.startswith("Runtime Error: "):
            return line[len("Runtime Error: ") :]
    raise AssertionError(f"no runtime error in:\n{stderr}")


def _run(
    backend: str,
    source: str,
    directory: Path,
    capabilities: frozenset[str] = frozenset({"ffi", "print"}),
) -> tuple[bool, str]:
    """Run *source*: (True, stdout) or (False, the failure's message)."""
    if backend == "interpreter":
        result = api.run(
            source,
            config=api.RunConfig(
                capabilities=set(capabilities),
                allow_ffi="ffi" in capabilities,
                timeout=10.0,
            ),
        )
        if result.ok:
            return True, result.output
        return False, result.diagnostics[0].message
    if backend == "python":
        path = directory / "program.py"
        path.write_text(compile_to_python(source), encoding="utf-8")
        command = [sys.executable, str(path)]
    else:
        path = directory / "program.js"
        js = compile_to_js(source)
        assert isinstance(js, str)
        path.write_text(js, encoding="utf-8")
        command = ["node", str(path)]
    if capabilities:
        command += ["--cap", ",".join(sorted(capabilities))]
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=directory,
        env={**os.environ, "PYTHONPATH": str(directory)},
    )
    if proc.returncode == 0:
        return True, proc.stdout
    return False, _failure_line(proc.stderr)


def _check(source: str, **kwargs) -> api.CheckResult:
    return api.check(source, **kwargs)


def _messages(result) -> list[str]:
    return [d.message for d in result.diagnostics]


# ---------------------------------------------------------------------------
# Declaration and parsing


def test_an_extern_parses_to_a_bodiless_function_with_its_bindings() -> None:
    program = parse(
        textwrap.dedent(
            """\
            export extern func host_sqrt(x: Float) -> Float with ffi
                python "math:sqrt"
                js "Math.sqrt"
            end func host_sqrt
            """
        )
    )
    (defn,) = program.definitions
    assert defn.is_extern
    assert defn.exported
    assert defn.extern_bindings == {"python": "math:sqrt", "js": "Math.sqrt"}
    assert defn.effects == ["ffi"]
    assert defn.body == []


def test_extern_python_and_js_stay_ordinary_names() -> None:
    source = textwrap.dedent(
        """\
        func extern(python: Int, js: Int) -> Int
            example (1, 2) -> 3
            return python + js
        end func

        func main() -> Unit with io
            let extern_value = extern(1, 2)
            print(to_string(extern_value))
        end func
        """
    )
    result = api.run(source, config=api.RunConfig(capabilities={"print"}))
    assert result.ok, _messages(result)
    assert result.output == "3\n"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            'extern func f(x: Int) -> Int\n    python "m:f"\nend func',
            "must declare 'with ffi'",
        ),
        (
            'extern func f(x: Int) -> Int with io\n    python "m:f"\nend func',
            "declares exactly 'with ffi'",
        ),
        (
            'extern func f(x: Int) -> Int with ffi, io\n    python "m:f"\nend func',
            "declares exactly 'with ffi'",
        ),
        ("extern func f(x: Int) -> Int with ffi\nend func", "at least one binding"),
        (
            'extern func f(x: Int) -> Int with ffi\n    python "m:f"\n'
            '    python "m:g"\nend func',
            "more than one python binding",
        ),
        (
            'extern func f(x: Int) -> Int with ffi\n    python "math.sqrt"\nend func',
            'a python binding is written "module:function"',
        ),
        (
            'extern func f(x: Int) -> Int with ffi\n    js "a b#c"\nend func',
            "a js binding is written",
        ),
        (
            'extern func f(x: Int) -> Int with ffi\n    js "m#"\nend func',
            "a js binding is written",
        ),
        (
            'extern func f(x: Int) -> Int with ffi\n    python "m:f"\n'
            "    return x\nend func",
            "an extern function has no body",
        ),
        (
            "extern func f(x: Int) -> Int with ffi\n    example (1) -> 1\n"
            '    python "m:f"\nend func',
            "an extern function has no body",
        ),
        (
            'extern func f[T](x: T) -> T with ffi\n    python "m:f"\nend func',
            "cannot have type parameters",
        ),
        (
            'extern func f(x: Int = 1) -> Int with ffi\n    python "m:f"\nend func',
            "cannot give parameter 'x' a default value",
        ),
        (
            'extern func f(x: Int) with ffi\n    python "m:f"\nend func',
            "needs a return type",
        ),
        (
            '@untested("host")\nextern func f(x: Int) -> Int with ffi\n'
            '    python "m:f"\nend func',
            "@untested is not needed on an extern function",
        ),
        (
            'extern func f(x: Int) -> Int with ffi\n    python "m:f"\nend func g',
            "doesn't match",
        ),
    ],
)
def test_malformed_externs_are_parse_errors(body: str, message: str) -> None:
    with pytest.raises(ParseError) as raised:
        parse(body + "\n")
    assert message in str(raised.value)


def test_the_formatter_keeps_an_extern_and_indents_its_bindings() -> None:
    messy = (
        "extern func host_sqrt(x: Float) -> Float with ffi\n"
        'python "math:sqrt"\n'
        '        js   "Math.sqrt"\n'
        "end func\n"
    )
    formatted = format_source(messy)
    assert formatted == (
        "extern func host_sqrt(x: Float) -> Float with ffi\n"
        '    python "math:sqrt"\n'
        '    js "Math.sqrt"\n'
        "end func\n"
    )
    assert format_source(formatted) == formatted


# ---------------------------------------------------------------------------
# Types and effects


@pytest.mark.parametrize(
    ("signature", "message"),
    [
        (
            "(u: User) -> Int",
            "Extern function 'f' cannot take parameter 'u': User cannot cross",
        ),
        ("(xs: Array[Int]) -> Int", "Array[Int] cannot cross"),
        ("(m: Map[Int, String]) -> Int", "a Map's keys must be String"),
        ("(f: (Int) -> Int) -> Int", "cannot take parameter 'f'"),
        ("(x: Int) -> Result[Int, String]", "Extern function 'f' cannot return"),
        ("(x: Int) -> Set[Int]", "Set[Int] cannot cross"),
        ("(xs: List[User]) -> Int", "User cannot cross"),
        ("(x: Int) -> MutableMap[String, Int]", "cannot return"),
    ],
)
def test_a_type_that_cannot_cross_the_boundary_is_e333(
    signature: str, message: str
) -> None:
    source = "type User = User(name: String)\n" + _extern("f", signature, "m:f", None)
    result = _check(source)
    assert not result.ok
    (diagnostic,) = result.diagnostics
    assert diagnostic.code == ErrorCode.TYPE_EXTERN_TYPE
    assert message in diagnostic.message


def test_every_supported_type_is_accepted() -> None:
    source = _extern(
        "f",
        "(a: Int, b: Float, c: String, d: Bool, e: Unit, g: List[Option[Int]], "
        "h: (Int, String), i: Map[String, List[Float]], j: JsonValue) -> "
        "Option[(Bool, JsonValue)]",
        "m:f",
        "m#f",
    )
    result = _check(source)
    assert result.ok, _messages(result)


def test_calling_an_extern_is_the_ffi_effect() -> None:
    source = _extern(
        "host_sqrt", "(x: Float) -> Float", "math:sqrt", "Math.sqrt"
    ) + textwrap.dedent(
        """\
            @untested("host")
            func root(x: Float) -> Float with io
                return host_sqrt(x)
            end func
            """
    )
    result = _check(source)
    assert not result.ok
    assert any(
        "performs undeclared effects: ffi" in message for message in _messages(result)
    )


def test_ffi_is_inferred_and_propagates_to_callers() -> None:
    source = _extern(
        "host_sqrt", "(x: Float) -> Float", "math:sqrt", "Math.sqrt"
    ) + textwrap.dedent(
        """\
            @untested("host")
            func root(x: Float) -> Float
                return host_sqrt(x)
            end func

            @untested("host")
            func twice(x: Float) -> Float with io
                return root(root(x))
            end func
            """
    )
    result = _check(source)
    assert any(
        "Function 'twice' performs undeclared effects: ffi" in message
        for message in _messages(result)
    )


def test_a_function_that_calls_an_extern_still_needs_examples() -> None:
    source = _extern(
        "host_sqrt", "(x: Float) -> Float", "math:sqrt", "Math.sqrt"
    ) + textwrap.dedent(
        """\
        func root(x: Float) -> Float with ffi
            return host_sqrt(x)
        end func
        """
    )
    assert "Function 'root' must have at least one example clause" in _messages(
        _check(source)
    )


# ---------------------------------------------------------------------------
# Targets


MAIN_CALLS = textwrap.dedent(
    """\
    func main() -> Unit with ffi, io
        print(to_string(only_js(2)))
    end func
    """
)


@pytest.mark.parametrize(
    ("target", "ok"),
    [("python-cli", False), ("python-hosted", False), ("node-cli", True)],
)
def test_a_target_rejects_a_use_of_an_extern_with_no_binding_for_it(
    target: str, ok: bool
) -> None:
    source = _extern("only_js", "(x: Int) -> Int", None, "m#f") + MAIN_CALLS
    result = _check(source, target=target)
    assert result.ok is ok, _messages(result)
    if not ok:
        assert (
            f"Extern function 'only_js' is not available on the '{target}' "
            "target: it has no python binding"
        ) in _messages(result)


def test_an_unused_extern_with_no_binding_for_the_target_is_accepted() -> None:
    source = _extern("only_js", "(x: Int) -> Int", None, "m#f") + textwrap.dedent(
        """\
        func main() -> Unit with io
            print("hi")
        end func
        """
    )
    assert _check(source, target="python-cli").ok


def test_a_browser_accepts_only_a_global_js_binding() -> None:
    module_form = _extern("only_js", "(x: Int) -> Int", None, "m#f") + MAIN_CALLS
    result = _check(module_form, target="browser")
    assert not result.ok
    assert any("a browser loads no modules" in message for message in _messages(result))
    global_form = _extern("only_js", "(x: Int) -> Int", None, "Math.abs") + MAIN_CALLS
    assert _check(global_form, target="browser").ok


def test_an_alias_of_an_unavailable_extern_is_rejected() -> None:
    source = _extern("only_js", "(x: Int) -> Int", None, "m#f") + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            let f = only_js
            print(to_string(f(2)))
        end func
        """
    )
    result = _check(source, target="python-cli")
    assert not result.ok
    assert any("'only_js' is not available" in m for m in _messages(result))


@pytest.mark.parametrize("qualified", [False, True])
def test_an_imported_extern_is_rejected_where_it_is_used(qualified: bool) -> None:
    module = "export " + _extern("only_js", "(x: Int) -> Int", None, "m#f")
    call = "Hosts.only_js(2)" if qualified else "only_js(2)"
    main = textwrap.dedent(
        f"""\
        import Hosts

        func main() -> Unit with ffi, io
            print(to_string({call}))
        end func
        """
    )
    result = _check(main, modules={"Hosts": module}, target="python-cli")
    assert not result.ok
    assert any("'only_js' is not available" in m for m in _messages(result))
    assert _check(main, modules={"Hosts": module}, target="node-cli").ok


# ---------------------------------------------------------------------------
# Running: conversion both ways


ROUND_TRIPS = [
    ("Int", "42"),
    ("Int", "-7"),
    ("Float", "1.5"),
    ("String", '"hé\\n"'),
    ("Bool", "true"),
    ("Unit", "()"),
    ("List[Int]", "[1, 2, 3]"),
    ("List[String]", "[]"),
    ("(Int, String)", '(1, "a")'),
    ("Option[Int]", "Some(7)"),
    ("Option[Int]", "None"),
    ("List[Option[String]]", '[Some("a"), None]'),
    ("Map[String, Int]", '{"b": 1, "a": 2}'),
    ("Map[String, List[Float]]", '{"k": [1.5]}'),
    ("JsonValue", 'unwrap(json_parse("{\\"k\\": [1, null, true, \\"s\\", 2.5]}"))'),
]


@pytest.mark.parametrize("backend", BACKENDS)
def test_values_of_every_kind_cross_the_boundary_unchanged(
    backend: str, host_dir: Path
) -> None:
    externs = "".join(
        _echo(f"echo_{index}", type_text)
        for index, (type_text, _) in enumerate(ROUND_TRIPS)
    )
    lines = []
    expected_lines = []
    for index, (type_text, value) in enumerate(ROUND_TRIPS):
        lines.append(f"    let v{index}: {type_text} = {value}")
        lines.append(f"    print(v{index} == echo_{index}(v{index}))")
        lines.append(f"    print(echo_{index}(v{index}))")
        expected_lines.append(f"    let v{index}: {type_text} = {value}")
        expected_lines.append("    print(true)")
        expected_lines.append(f"    print(v{index})")
    source = (
        externs
        + "func main() -> Unit with ffi, io\n"
        + "\n".join(lines)
        + "\nend func\n"
    )
    plain = "func main() -> Unit with io\n" + "\n".join(expected_lines) + "\nend func\n"
    expected = api.run(plain, config=api.RunConfig(capabilities={"print"}))
    assert expected.ok, _messages(expected)
    ok, output = _run(backend, source, host_dir)
    assert ok, output
    assert output == expected.output


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_standard_library_binding_runs(backend: str, host_dir: Path) -> None:
    source = _extern(
        "host_sqrt", "(x: Float) -> Float", "math:sqrt", "Math.sqrt"
    ) + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            print(host_sqrt(16.0))
            print(host_sqrt(2.25) * 2.0)
        end func
        """
    )
    assert _run(backend, source, host_dir) == (True, "4.0\n3.0\n")


@pytest.mark.parametrize("backend", BACKENDS)
def test_arguments_arrive_as_host_values(backend: str, host_dir: Path) -> None:
    source = _extern(
        "args_seen",
        "(a: Option[Int], b: Option[Int], c: (Int, Bool), d: Map[String, Unit]) -> String",
        "ffi_host:args_seen",
        "./ffi_host.js#argsSeen",
    ) + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            print(args_seen(a: Some(1), b: None, c: (2, true), d: {"k": ()}))
        end func
        """
    )
    ok, output = _run(backend, source, host_dir)
    assert ok, output
    if backend == "js":
        assert output == '[1,null,[2,true],{"k":null}]\n'
    else:
        assert output == "(1, None, (2, True), {'k': None})\n"


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_result_is_coerced_where_the_host_cannot_tell(
    backend: str, host_dir: Path
) -> None:
    source = (
        _extern(
            "int_for_float",
            "() -> Float",
            "ffi_host:int_for_float",
            "./ffi_host.js#intForFloat",
        )
        + _extern(
            "nothing", "() -> Option[Int]", "ffi_host:nothing", "./ffi_host.js#nothing"
        )
        + _extern("unit", "() -> Unit", "ffi_host:nothing", "./ffi_host.js#nothing")
        + _extern(
            "ordered_map",
            "() -> Map[String, Int]",
            "ffi_host:ordered_map",
            "./ffi_host.js#orderedMap",
        )
        + textwrap.dedent(
            """\
            func main() -> Unit with ffi, io
                print(int_for_float())
                print(nothing())
                print(unit())
                print(ordered_map())
            end func
            """
        )
    )
    assert _run(backend, source, host_dir) == (
        True,
        '3.0\nNone\n()\n{"b": 1, "a": 2}\n',
    )


@pytest.mark.parametrize("backend", [pytest.param("js", marks=needs_node)])
def test_a_js_method_is_called_on_its_object(host_dir: Path, backend: str) -> None:
    source = _extern(
        "read", "() -> Int", None, "./ffi_host.js#counter.read"
    ) + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            print(read())
        end func
        """
    )
    assert _run(backend, source, host_dir) == (True, "5\n")


# ---------------------------------------------------------------------------
# Running: failures


FAILURES = [
    (
        "() -> Int",
        ("ffi_host:boom", "./ffi_host.js#boom"),
        "Extern function 'f' failed: ValueError: bad input",
        "Extern function 'f' failed: Error: bad input",
    ),
    (
        "() -> Int",
        ("ffi_host:string_for_int", "./ffi_host.js#stringForInt"),
        "Extern function 'f' returned a value that is not Int: expected Int, got str",
        "Extern function 'f' returned a value that is not Int: expected Int, got string",
    ),
    (
        "() -> Int",
        ("ffi_host:bool_for_int", "./ffi_host.js#boolForInt"),
        "Extern function 'f' returned a value that is not Int: expected Int, got bool",
        "Extern function 'f' returned a value that is not Int: expected Int, got boolean",
    ),
    (
        "() -> List[Int]",
        ("ffi_host:nested_bad", "./ffi_host.js#nestedBad"),
        "Extern function 'f' returned a value that is not List[Int]: "
        "$[1]: expected Int, got str",
        "Extern function 'f' returned a value that is not List[Int]: "
        "$[1]: expected Int, got string",
    ),
    (
        "() -> (Int, Int)",
        ("ffi_host:short_tuple", "./ffi_host.js#shortTuple"),
        "Extern function 'f' returned a value that is not (Int, Int): "
        "expected a tuple of 2 items, got 1",
        "Extern function 'f' returned a value that is not (Int, Int): "
        "expected a tuple of 2 items, got 1",
    ),
    (
        "() -> Map[String, String]",
        ("ffi_host:int_keys", "./ffi_host.js#intKeys"),
        "Extern function 'f' returned a value that is not Map[String, String]: "
        "expected a dict with str keys, got int",
        "Extern function 'f' returned a value that is not Map[String, String]: "
        "expected an object with string keys, got number",
    ),
    (
        "() -> List[JsonValue]",
        ("ffi_host:cycle", "./ffi_host.js#cycle"),
        "Extern function 'f' returned a value that is not List[JsonValue]: "
        "the result is nested too deeply",
        "Extern function 'f' returned a value that is not List[JsonValue]: "
        "the result is nested too deeply",
    ),
    (
        "() -> Int",
        ("ffi_host:not_callable", "./ffi_host.js#notCallable"),
        "Extern function 'f' has a python binding 'ffi_host:not_callable' that is "
        "not callable",
        "Extern function 'f' has a js binding './ffi_host.js#notCallable' that is "
        "not callable",
    ),
    (
        "() -> Int",
        ("no_such_module_for_geno:f", "./no_such_module.js#f"),
        "Extern function 'f' could not load its python binding "
        "'no_such_module_for_geno:f': ModuleNotFoundError: No module named "
        "'no_such_module_for_geno'",
        "Extern function 'f' could not load its js binding './no_such_module.js#f': "
        "Error: Cannot find module './no_such_module.js'",
    ),
    (
        "() -> Int",
        ("ffi_host:missing_name", "./ffi_host.js#missing.name"),
        "Extern function 'f' could not load its python binding "
        "'ffi_host:missing_name': AttributeError: module 'ffi_host' has no "
        "attribute 'missing_name'",
        "Extern function 'f' could not load its js binding "
        "'./ffi_host.js#missing.name': Error: 'name' is read from undefined",
    ),
]


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(
    ("signature", "bindings", "python_message", "js_message"), FAILURES
)
def test_each_failure_is_a_panic_naming_the_extern(
    backend: str,
    host_dir: Path,
    signature: str,
    bindings: tuple[str, str],
    python_message: str,
    js_message: str,
) -> None:
    source = _extern("f", signature, *bindings) + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            print(f())
        end func
        """
    )
    ok, message = _run(backend, source, host_dir)
    assert not ok
    expected = js_message if backend == "js" else python_message
    assert message.startswith(expected), message


def test_the_interpreter_panics_at_a_call_with_no_python_binding(
    host_dir: Path,
) -> None:
    source = _extern("f", "(x: Int) -> Int", None, "Math.abs") + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            print(f(2))
        end func
        """
    )
    assert _run("interpreter", source, host_dir) == (
        False,
        "Extern function 'f' has no python binding, so it cannot run on this backend",
    )


@pytest.mark.parametrize(
    ("compile_", "bindings", "message"),
    [
        (
            compile_to_python,
            (None, "Math.abs"),
            "Extern function 'f' is not available on the 'python-cli' target: "
            "it has no python binding",
        ),
        (
            compile_to_js,
            ("math:fabs", None),
            "Extern function 'f' is not available on the 'node-cli' target: "
            "it has no js binding",
        ),
    ],
)
def test_compiling_rejects_a_call_with_no_binding_for_the_backend(
    compile_, bindings: tuple[str | None, str | None], message: str
) -> None:
    source = _extern("f", "(x: Int) -> Int", *bindings) + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            print(f(2))
        end func
        """
    )
    with pytest.raises(Exception, match=message):
        compile_(source)


# ---------------------------------------------------------------------------
# The capability


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_call_without_ffi_is_denied_and_imports_nothing(
    backend: str, host_dir: Path
) -> None:
    source = _extern(
        "marked", "() -> Int", "ffi_marker:run", "./ffi_marker.js#run"
    ) + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            print(marked())
        end func
        """
    )
    ok, message = _run(backend, source, host_dir, capabilities=frozenset({"print"}))
    assert not ok
    assert message.startswith(
        "Capability denied: 'marked' requires the 'ffi' capability"
    ), message
    assert not (host_dir / "imported.txt").exists()

    assert _run(backend, source, host_dir) == (True, "1\n")
    assert (host_dir / "imported.txt").exists()


def test_the_interpreter_denial_carries_its_capability_code() -> None:
    source = _extern("host_sqrt", "(x: Float) -> Float", "math:sqrt", None) + (
        "func main() -> Float with ffi\n    return host_sqrt(4.0)\nend func\n"
    )
    result = api.run(source, config=api.RunConfig(capabilities=set()))
    assert not result.ok
    (diagnostic,) = result.diagnostics
    assert diagnostic.code == ErrorCode.RUNTIME_CAPABILITY_DENIED


def test_the_embedding_api_needs_an_explicit_opt_in_for_ffi() -> None:
    with pytest.raises(ValueError, match="allow_ffi=True"):
        api.RunConfig(capabilities={"ffi"})
    with pytest.raises(ValueError, match="allow_ffi=True"):
        api.RunConfig(capabilities={"ffi"}, allow_ffi=1)  # type: ignore[arg-type]
    api.RunConfig(capabilities={"ffi"}, allow_ffi=True)
    # The opt-in alone grants nothing.
    source = _extern("host_sqrt", "(x: Float) -> Float", "math:sqrt", None) + (
        "func main() -> Float with ffi\n    return host_sqrt(4.0)\nend func\n"
    )
    result = api.run(source, config=api.RunConfig(capabilities=set(), allow_ffi=True))
    assert not result.ok
    result = api.run(source, config=api.RunConfig(capabilities={"ffi"}, allow_ffi=True))
    assert result.ok, _messages(result)
    assert result.value == 2.0


def test_the_hosted_server_refuses_to_allow_ffi() -> None:
    from geno.monitoring import RuntimeMetricsCollector
    from geno.server import create_handler

    with pytest.raises(ValueError, match="cannot grant 'ffi'"):
        create_handler(RuntimeMetricsCollector(), {"ffi", "print"})
    with pytest.raises(ValueError, match="cannot grant 'ffi'"):
        create_handler(
            RuntimeMetricsCollector(),
            {"print"},
            default_request_capabilities={"ffi"},
        )


def test_geno_serve_refuses_ffi_before_starting(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from geno.cli.serve import serve_runtime

    with pytest.raises(SystemExit) as raised:
        serve_runtime(capabilities={"ffi"})
    assert raised.value.code == 2
    assert "the hosted runtime cannot grant 'ffi'" in capsys.readouterr().err


def _geno_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "geno", *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=cwd,
    )


SQRT_PROGRAM = _extern(
    "host_sqrt", "(x: Float) -> Float", "math:sqrt", "Math.sqrt"
) + textwrap.dedent(
    """\
    func main() -> Unit with ffi, io
        print(host_sqrt(9.0))
    end func
    """
)


def test_geno_run_lanes(tmp_path: Path) -> None:
    program = tmp_path / "main.geno"
    program.write_text(SQRT_PROGRAM, encoding="utf-8")

    isolated = _geno_cli("run", "--cap", "ffi,print", "main.geno", cwd=tmp_path)
    assert isolated.returncode != 0
    assert (
        "--cap ffi is not available in the process-isolated run mode: extern "
        "functions run host code outside the sandbox"
    ) in isolated.stderr

    denied = _geno_cli("run", "main.geno", cwd=tmp_path)
    assert denied.returncode != 0
    assert "Capability denied: 'host_sqrt' requires the 'ffi' capability" in (
        denied.stderr
    )

    as_json = _geno_cli(
        "run", "--json", "--cap", "ffi,print", "main.geno", cwd=tmp_path
    )
    assert as_json.returncode != 0
    assert "--cap ffi is not available with --json" in as_json.stdout

    unsafe = _geno_cli(
        "run", "--unsafe", "--cap", "ffi,print", "main.geno", cwd=tmp_path
    )
    assert unsafe.returncode == 0, unsafe.stderr
    assert unsafe.stdout == "3.0\n"
