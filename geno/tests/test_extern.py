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
from geno.tests._script_runner import GENERATED_PROGRAM_TIMEOUT
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


def count_to(n):
    return list(range(n))


def map_with_key_len(n):
    return {"a" * n: 1}


class _BadLenStr(str):
    def __len__(self):
        raise RuntimeError("bad_len_hook")


def map_with_bad_len_key(n):
    return {_BadLenStr("a" * n): 1}


import enum
import http


class Mode(str, enum.Enum):
    READ = "r"


class Liar(str):
    def __eq__(self, other):
        return True

    __hash__ = str.__hash__


class Half(float):
    def __eq__(self, other):
        return False

    __hash__ = float.__hash__


def int_enum():
    return http.HTTPStatus.NOT_FOUND


def str_enum():
    return Mode.READ


def liar():
    return Liar("abc")


def half():
    return Half(0.5)


def enum_table():
    return {Mode.READ: http.HTTPStatus.NOT_FOUND}


def enum_json():
    return {Mode.READ: [http.HTTPStatus.NOT_FOUND, Liar("abc"), Half(0.5), True]}


def huge():
    return 2**100000


class Converts(int):
    def __int__(self):
        return 7

    def __float__(self):
        return 7.5


class Refuses(int):
    def __int__(self):
        raise ValueError("nope")


class Floats(float):
    def __float__(self):
        return 7.5


def converts():
    return Converts(404)


def refuses():
    return Refuses(404)


def converts_json():
    return [Converts(404), Floats(0.5)]


def converts_float():
    return Converts(2)


def huge_in_list():
    return [1, 2**100000]


def huge_in_json():
    return {"n": [2**100000]}


class DictHook(dict):
    def __len__(self):
        raise ValueError("dict_len_boom")

    def items(self):
        raise ValueError("dict_items_boom")


class ListHook(list):
    def __len__(self):
        raise ValueError("list_len_boom")

    def __iter__(self):
        raise ValueError("list_iter_boom")


class TupleHook(tuple):
    def __len__(self):
        raise ValueError("tuple_len_boom")

    def __iter__(self):
        raise ValueError("tuple_iter_boom")


class StrLenHook(str):
    def __len__(self):
        raise ValueError("str_len_boom")


def dict_hook():
    return DictHook({"a": 1})


def list_hook():
    return ListHook([1, 2])


def tuple_hook():
    return TupleHook((3, 4))


def str_len_hook():
    return StrLenHook("ok")


def container_hooks_json():
    return DictHook({"xs": ListHook([TupleHook((1, StrLenHook("ok")))])})


import asyncio as _asyncio

_async_call_count = 0


async def async_double(n):
    await _asyncio.sleep(0)
    return n * 2


async def async_echo(value):
    await _asyncio.sleep(0)
    return value


async def async_boom():
    await _asyncio.sleep(0)
    raise ValueError("async failure")


async def async_count_calls():
    global _async_call_count
    await _asyncio.sleep(0)
    _async_call_count += 1
    return _async_call_count


def boom_huge(n):
    raise ValueError("x" * n)


def user_missing_field():
    return {"name": "Ada"}


def user_optional_and_extra():
    return {"name": "Ada", "extra": 999}


def user_oversized_extra(n):
    return {"name": "Ada", "x" * n: 1}


def user_dict_hook():
    return DictHook({StrLenHook("name"): StrLenHook("Ada"), StrLenHook("age"): 36})


def enum_str_subclass():
    return StrLenHook("Green")


def bad_enum():
    return "Purple"


def union_missing_type():
    return {"radius": 1.0}


def union_non_str_type():
    return {"type": 123}


def union_bad_type():
    return {"type": "Triangle"}


def recursive_cycle():
    node = {"type": "Node"}
    node["left"] = node
    node["right"] = {"type": "Leaf", "value": 1}
    return node


not_callable = 42
"""

JS_HOST = """
"use strict";
let _asyncCallCount = 0;
module.exports = {
    echo: value => value,
    boom: () => { throw new Error("bad input"); },
    boomHuge: n => { throw new Error("x".repeat(n)); },
    asyncDouble: async n => { await Promise.resolve(); return n * 2; },
    asyncEcho: async value => { await Promise.resolve(); return value; },
    asyncBoom: async () => { await Promise.resolve(); throw new Error("async failure"); },
    asyncRejectString: () => Promise.reject("plain rejection"),
    asyncCountCalls: async () => { await Promise.resolve(); _asyncCallCount += 1; return _asyncCallCount; },
    userMissingField: () => ({ name: "Ada" }),
    userOptionalAndExtra: () => ({ name: "Ada", extra: 999 }),
    userOversizedExtra: n => ({ name: "Ada", ["x".repeat(n)]: 1 }),
    userAsMap: () => new Map([["name", "Ada"], ["age", 36]]),
    badEnum: () => "Purple",
    unionMissingType: () => ({ radius: 1.0 }),
    unionNonStrType: () => ({ type: 123 }),
    unionBadType: () => ({ type: "Triangle" }),
    recursiveCycle: () => {
        const node = { type: "Node" };
        node.left = node;
        node.right = { type: "Leaf", value: 1 };
        return node;
    },
    stringForInt: () => "x",
    boolForInt: () => true,
    intForFloat: () => 3,
    nestedBad: () => [1, "two"],
    shortTuple: () => [1],
    intKeys: () => new Map([[1, "a"]]),
    orderedMap: () => ({ b: 1, a: 2 }),
    nothing: () => undefined,
    sparse: () => new Array(2),
    promise: () => Promise.resolve(42),
    when: () => new Date(0),
    throwingGetter: () => Object.defineProperty({}, "k", {
        enumerable: true,
        get() { throw new Error("getter_boom"); },
    }),
    throwingProxy: () => new Proxy({}, {
        ownKeys() { throw new Error("proxy_boom"); },
    }),
    cycle: () => { const items = []; items.push(items); return items; },
    argsSeen: (...args) => JSON.stringify(args),
    countTo: n => Array.from({ length: n }, (_, index) => index),
    mapWithKeyLen: n => ({ ["a".repeat(n)]: 1 }),
    oversizedObjectWithGetter: n => {
        const obj = {};
        for (let i = 0; i < n; i++) {
            Object.defineProperty(obj, "k" + i, {
                enumerable: true,
                get() { throw new Error("should_not_read_property"); },
            });
        }
        return obj;
    },
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


def _extern(
    name: str,
    signature: str,
    python: str | None,
    js: str | None,
    *,
    is_async: bool = False,
) -> str:
    prefix = "async extern func" if is_async else "extern func"
    lines = [f"{prefix} {name}{signature} with ffi"]
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
        ("(xs: Array[Int]) -> Int", "Array[Int] cannot cross"),
        ("(m: Map[Int, String]) -> Int", "a Map's keys must be String"),
        ("(f: (Int) -> Int) -> Int", "cannot take parameter 'f'"),
        (
            "(r: Result[Int, String]) -> Int",
            "only allowed as the top-level return type",
        ),
        (
            "(x: Int) -> Result[Int, Int]",
            "an extern returning Result must use String as its error type",
        ),
        (
            "(x: Int) -> List[Result[Int, String]]",
            "only allowed as the top-level return type",
        ),
        (
            "(x: Int) -> Result[Result[Int, String], String]",
            "only allowed as the top-level return type",
        ),
        ("(x: Int) -> Set[Int]", "Set[Int] cannot cross"),
        ("(x: Int) -> MutableMap[String, Int]", "cannot return"),
        ("(x: Async[Int]) -> Int", "cannot take parameter 'x'"),
        (
            "(b: BadRecord) -> Int",
            "in field 'cb' of BadRecord",
        ),
        (
            "(x: Int) -> BadResultRecord",
            "in field 'res' of BadResultRecord",
        ),
    ],
)
def test_a_type_that_cannot_cross_the_boundary_is_e333(
    signature: str, message: str
) -> None:
    source = (
        "type BadRecord = BadRecord(cb: (Int) -> Int)\n"
        "type BadResultRecord = BadResultRecord(res: Result[Int, String])\n"
        + _extern("f", signature, "m:f", None)
    )
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


@pytest.mark.parametrize(("target", "ok"), [("python-cli", False), ("node-cli", True)])
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


@pytest.mark.parametrize("target", ["browser", "python-hosted"])
def test_a_target_that_never_grants_ffi_rejects_every_extern_use(target: str) -> None:
    source = _extern("only_js", "(x: Int) -> Int", "m:f", "Math.abs") + MAIN_CALLS
    result = _check(source, target=target)
    assert not result.ok
    assert (
        f"Extern function 'only_js' is not available on the '{target}' target: "
        "it never grants the 'ffi' capability"
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


PYTHON_BACKENDS = ["interpreter", "python"]


@pytest.mark.parametrize("backend", PYTHON_BACKENDS)
def test_a_host_subclass_of_a_builtin_type_becomes_a_plain_value(
    backend: str, host_dir: Path
) -> None:
    """An IntEnum, a (str, Enum) member or a str or float subclass with its
    own ``__eq__`` arrives as the plain value it holds, at any depth, so the
    interpreter and compiled Python compare and print it alike.
    """
    source = (
        _extern("int_enum", "() -> Int", "ffi_host:int_enum", None)
        + _extern("str_enum", "() -> String", "ffi_host:str_enum", None)
        + _extern("liar", "() -> String", "ffi_host:liar", None)
        + _extern("half", "() -> Float", "ffi_host:half", None)
        + _extern("enum_table", "() -> Map[String, Int]", "ffi_host:enum_table", None)
        + _extern("enum_json", "() -> JsonValue", "ffi_host:enum_json", None)
        + textwrap.dedent(
            """\
            func main() -> Unit with ffi, io
                let s = int_enum()
                print(s == 404)
                print(s)
                print(f"{s}")
                let m = str_enum()
                print(m == "r")
                print(m)
                print(json_encode(m))
                let w = liar()
                print(w == "zzz")
                print(w == "abc")
                print(half() == 0.5)
                let t = enum_table()
                print(t == {"r": 404})
                print(t)
                let items = [JsonInt(404), JsonString("abc"), JsonFloat(0.5), JsonBool(true)]
                let d = enum_json()
                print(d == JsonObject([("r", JsonArray(items))]))
                print(json_stringify(d))
            end func
            """
        )
    )
    assert _run(backend, source, host_dir) == (
        True,
        "true\n404\n404\ntrue\nr\n"
        '"r"\nfalse\ntrue\ntrue\ntrue\n{"r": 404}\ntrue\n'
        '{"r":[404,"abc",0.5,true]}\n',
    )


@pytest.mark.parametrize("backend", PYTHON_BACKENDS)
def test_a_host_subclass_is_read_without_running_its_own_methods(
    backend: str, host_dir: Path
) -> None:
    """The value a subclass holds is what arrives: its own ``__int__`` or
    ``__float__`` neither replaces it nor, by raising, escapes the extern's
    boundary (0.7 review)."""
    source = (
        _extern("converts", "() -> Int", "ffi_host:converts", None)
        + _extern("refuses", "() -> Int", "ffi_host:refuses", None)
        + _extern("converts_json", "() -> JsonValue", "ffi_host:converts_json", None)
        + _extern("converts_float", "() -> Float", "ffi_host:converts_float", None)
        + textwrap.dedent(
            """\
            func main() -> Unit with ffi, io
                let c = converts()
                print(c == 404)
                print(refuses())
                print(json_stringify(converts_json()))
                print(converts_float())
            end func
            """
        )
    )
    assert _run(backend, source, host_dir) == (
        True,
        "true\n404\n[404,0.5]\n2.0\n",
    )


def test_a_plain_host_value_is_returned_as_is() -> None:
    from geno import _runtime_support

    value = 2**70
    shape = ("int",)
    ops = _runtime_support._GENO_JSON_OPS
    assert _runtime_support._geno_extern_from_host(value, shape, None, ops, 0) is value


@pytest.mark.parametrize("backend", PYTHON_BACKENDS)
@pytest.mark.parametrize(
    ("name", "type_text"),
    [
        ("huge", "Int"),
        ("huge_in_list", "List[Int]"),
        ("huge_in_json", "JsonValue"),
    ],
)
def test_a_host_int_is_bound_by_the_integer_size_limit(
    backend: str, host_dir: Path, name: str, type_text: str
) -> None:
    source = _extern(name, f"() -> {type_text}", f"ffi_host:{name}", None) + (
        f"func main() -> Unit with ffi, io\n    let v = {name}()\n"
        '    print("returned")\nend func\n'
    )
    ok, message = _run(backend, source, host_dir)
    assert not ok
    assert message == "Integer exceeds maximum size (100001 bits)"


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_too_deeply_nested_argument_names_the_extern(
    backend: str, host_dir: Path
) -> None:
    source = _echo("echo_json", "JsonValue") + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            var v = JsonNull
            var i = 0
            while i < 400 do
                v = JsonArray([v])
                i = i + 1
            end while
            let r = echo_json(v)
            print("done")
        end func
        """
    )
    assert _run(backend, source, host_dir) == (
        False,
        "Extern function 'echo_json' got a JsonValue argument nested more than "
        "320 levels deep",
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


def test_run_revalidates_the_ffi_opt_in_on_a_reused_config() -> None:
    config = api.RunConfig(capabilities={"ffi"}, allow_ffi=True)
    source = "func main() -> Int\n    return 7\nend func\n"
    assert api.run(source, config=config).value == 7
    config.allow_ffi = False
    with pytest.raises(ValueError, match="allow_ffi=True"):
        api.run(source, config=config)


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


@pytest.mark.parametrize(
    "capabilities", [{" ffi"}, ("print", "ffi "), ["\tffi\n"]], ids=repr
)
def test_the_hosted_server_refuses_ffi_however_it_is_spelled(capabilities) -> None:
    """The refusal applies to the names the server grants, once normalized."""
    from geno.monitoring import RuntimeMetricsCollector
    from geno.server import create_handler

    with pytest.raises(ValueError, match="cannot grant 'ffi'"):
        create_handler(RuntimeMetricsCollector(), allowed_capabilities=capabilities)
    with pytest.raises(ValueError, match="cannot grant 'ffi'"):
        create_handler(
            RuntimeMetricsCollector(),
            allowed_capabilities={"print"},
            default_request_capabilities=capabilities,
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
    # The hint explains the risk and offers no command line to paste.
    assert "lane never grants 'ffi'" in denied.stderr
    assert "full authority" in denied.stderr
    assert "geno run --unsafe" not in denied.stderr

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


@pytest.mark.parametrize("backend", BACKENDS)
def test_a_host_result_is_bound_by_the_collection_limit(
    backend: str, host_dir: Path
) -> None:
    source = _extern(
        "count_to",
        "(n: Int) -> List[Int]",
        "ffi_host:count_to",
        "./ffi_host.js#countTo",
    ) + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            print(length(count_to(20)))
        end func
        """
    )
    if backend == "interpreter":
        result = api.run(
            source,
            config=api.RunConfig(
                capabilities={"ffi", "print"}, allow_ffi=True, max_collection_size=10
            ),
        )
        assert not result.ok
        message = result.diagnostics[0].message
    else:
        if backend == "python":
            path = host_dir / "program.py"
            code = "_GENO_MAX_COLLECTION_SIZE = 10\n" + compile_to_python(source)
            command = [sys.executable, str(path)]
        else:
            path = host_dir / "program.js"
            js = compile_to_js(source)
            assert isinstance(js, str)
            code = "globalThis.__GENO_MAX_COLLECTION_SIZE = 10;\n" + js
            command = ["node", str(path)]
        path.write_text(code, encoding="utf-8")
        proc = subprocess.run(
            [*command, "--cap", "ffi,print"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=host_dir,
            env={**os.environ, "PYTHONPATH": str(host_dir)},
        )
        assert proc.returncode != 0, proc.stdout
        message = _failure_line(proc.stderr)
    assert "size exceeds limit" in message, message


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("return_type", ["Map[String, Int]", "JsonValue"])
def test_a_host_map_or_json_key_is_bound_by_the_string_limit(
    backend: str, return_type: str, host_dir: Path
) -> None:
    source = _extern(
        "map_with_key_len",
        f"(n: Int) -> {return_type}",
        "ffi_host:map_with_key_len",
        "./ffi_host.js#mapWithKeyLen",
    ) + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            let value = map_with_key_len(20)
            print("returned")
        end func
        """
    )
    if backend == "interpreter":
        result = api.run(
            source,
            config=api.RunConfig(
                capabilities={"ffi", "print"}, allow_ffi=True, max_collection_size=10
            ),
        )
        assert not result.ok
        message = result.diagnostics[0].message
    else:
        if backend == "python":
            path = host_dir / "program.py"
            code = "_GENO_MAX_COLLECTION_SIZE = 10\n" + compile_to_python(source)
            command = [sys.executable, str(path)]
        else:
            path = host_dir / "program.js"
            js = compile_to_js(source)
            assert isinstance(js, str)
            code = "globalThis.__GENO_MAX_COLLECTION_SIZE = 10;\n" + js
            command = ["node", str(path)]
        path.write_text(code, encoding="utf-8")
        proc = subprocess.run(
            [*command, "--cap", "ffi,print"],
            capture_output=True,
            text=True,
            timeout=GENERATED_PROGRAM_TIMEOUT,
            cwd=host_dir,
            env={**os.environ, "PYTHONPATH": str(host_dir)},
        )
        assert proc.returncode != 0, proc.stdout
        message = _failure_line(proc.stderr)
    assert "String size exceeds limit" in message, message


@pytest.mark.parametrize("backend", ["interpreter", "python"])
@pytest.mark.parametrize("return_type", ["Map[String, Int]", "JsonValue"])
def test_a_host_str_subclass_key_measures_base_length_without_len_hook(
    backend: str, return_type: str, host_dir: Path
) -> None:
    source = _extern(
        "map_with_bad_len_key",
        f"(n: Int) -> {return_type}",
        "ffi_host:map_with_bad_len_key",
        None,
    ) + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            let value = map_with_bad_len_key(3)
            print("ok")
        end func
        """
    )
    if backend == "interpreter":
        result = api.run(
            source,
            config=api.RunConfig(
                capabilities={"ffi", "print"}, allow_ffi=True, max_collection_size=10
            ),
        )
        assert result.ok, [d.message for d in result.diagnostics]
        assert result.output.strip() == "ok"
    else:
        path = host_dir / "program.py"
        code = "_GENO_MAX_COLLECTION_SIZE = 10\n" + compile_to_python(source)
        path.write_text(code, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(path), "--cap", "ffi,print"],
            capture_output=True,
            text=True,
            timeout=GENERATED_PROGRAM_TIMEOUT,
            cwd=host_dir,
            env={**os.environ, "PYTHONPATH": str(host_dir)},
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "ok"


@needs_node
def test_js_oversized_object_fails_before_reading_property_values(
    host_dir: Path,
) -> None:
    source = _extern(
        "big_obj",
        "(n: Int) -> Map[String, Int]",
        None,
        "./ffi_host.js#oversizedObjectWithGetter",
    ) + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            let value = big_obj(20)
            print("returned")
        end func
        """
    )
    path = host_dir / "program.js"
    js = compile_to_js(source)
    assert isinstance(js, str)
    path.write_text(
        "globalThis.__GENO_MAX_COLLECTION_SIZE = 10;\n" + js, encoding="utf-8"
    )
    proc = subprocess.run(
        ["node", str(path), "--cap", "ffi,print"],
        capture_output=True,
        text=True,
        timeout=GENERATED_PROGRAM_TIMEOUT,
        cwd=host_dir,
    )
    assert proc.returncode != 0, proc.stdout
    message = _failure_line(proc.stderr)
    assert "Map size exceeds limit" in message, message


@pytest.mark.parametrize("backend", ["python", pytest.param("js", marks=needs_node)])
def test_a_compiled_extern_frame_is_located_at_its_declaration(
    backend: str, tmp_path: Path
) -> None:
    (tmp_path / "main.geno").write_text(SQRT_PROGRAM, encoding="utf-8")
    args = ["compile", "main.geno", "-o", "out.py"]
    run = [sys.executable, "out.py"]
    if backend == "js":
        args = ["compile", "--target", "js", "main.geno", "-o", "out.js"]
        run = ["node", "out.js"]
    assert _geno_cli(*args, cwd=tmp_path).returncode == 0
    proc = subprocess.run(
        [*run, "--cap", "print"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=tmp_path,
    )
    assert proc.returncode != 0
    assert "main.geno:1:1 in host_sqrt" in proc.stderr, proc.stderr


def test_geno_doc_marks_an_extern(tmp_path: Path) -> None:
    (tmp_path / "main.geno").write_text(SQRT_PROGRAM, encoding="utf-8")
    proc = _geno_cli("doc", "main.geno", "-o", "doc.html", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    html = (tmp_path / "doc.html").read_text(encoding="utf-8")
    assert '<span class="keyword">extern func</span> host_sqrt' in html


def test_a_function_named_extern_may_close_with_its_name() -> None:
    source = textwrap.dedent(
        """\
        func extern(x: Int) -> Int
            example (1) -> 2
            return x + 1
        end func extern

        func main() -> Unit with io
            print(extern(1))
        end func
        """
    )
    result = api.run(source, config=api.RunConfig(capabilities={"print"}))
    assert result.ok, _messages(result)
    assert result.output == "2\n"


@pytest.mark.parametrize(
    ("signature", "export", "message"),
    [
        (
            "() -> List[Int]",
            "sparse",
            "Extern function 'f' returned a value that is not List[Int]: "
            "$[0]: expected Int, got undefined",
        ),
        (
            "() -> (Option[Int], Int)",
            "sparse",
            "Extern function 'f' returned a value that is not (Option[Int], Int): "
            "$[1]: expected Int, got undefined",
        ),
        (
            "() -> Map[String, Int]",
            "promise",
            "Extern function 'f' returned a value that is not Map[String, Int]: "
            "expected a plain object, got Promise",
        ),
        (
            "() -> JsonValue",
            "when",
            "Extern function 'f' returned a value that is not JsonValue: "
            "expected a plain object, got Date",
        ),
    ],
)
@needs_node
def test_js_results_that_are_not_plain_data_are_rejected(
    host_dir: Path, signature: str, export: str, message: str
) -> None:
    source = _extern("f", signature, None, f"./ffi_host.js#{export}") + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            print(f())
        end func
        """
    )
    ok, failure = _run("js", source, host_dir)
    assert not ok
    assert failure == message


@pytest.mark.parametrize("backend", PYTHON_BACKENDS)
def test_host_container_subclass_hooks_do_not_run_or_escape(
    backend: str, host_dir: Path
) -> None:
    source = (
        _extern("dict_hook", "() -> Map[String, Int]", "ffi_host:dict_hook", None)
        + _extern("list_hook", "() -> List[Int]", "ffi_host:list_hook", None)
        + _extern("tuple_hook", "() -> (Int, Int)", "ffi_host:tuple_hook", None)
        + _extern("str_len_hook", "() -> String", "ffi_host:str_len_hook", None)
        + _extern(
            "container_hooks_json",
            "() -> JsonValue",
            "ffi_host:container_hooks_json",
            None,
        )
        + textwrap.dedent(
            """\
            func main() -> Unit with ffi, io
                print(dict_hook())
                print(list_hook())
                print(tuple_hook())
                print(str_len_hook())
                print(json_stringify(container_hooks_json()))
            end func
            """
        )
    )
    assert _run(backend, source, host_dir) == (
        True,
        '{"a": 1}\n[1, 2]\n(3, 4)\nok\n{"xs":[[1,"ok"]]}\n',
    )


def test_python_extern_result_conversion_error_stays_inside_boundary() -> None:
    from geno import _runtime_support

    class BrokenOps:
        def map(self, pairs: object) -> object:
            raise ValueError("ops_boom")

    with pytest.raises(
        _runtime_support._GenoExternFailure,
        match="Extern function 'f' failed: ValueError: ops_boom",
    ):
        _runtime_support._geno_extern_invoke(
            "f",
            ("builtins", ("dict",)),
            [],
            (),
            ("map", ("int",)),
            BrokenOps(),
        )


@pytest.mark.parametrize(
    ("signature", "export", "detail"),
    [
        ("() -> Map[String, Int]", "throwingGetter", "Error: getter_boom"),
        ("() -> JsonValue", "throwingGetter", "Error: getter_boom"),
        ("() -> Map[String, Int]", "throwingProxy", "Error: proxy_boom"),
        ("() -> JsonValue", "throwingProxy", "Error: proxy_boom"),
    ],
)
@needs_node
def test_js_host_getter_and_proxy_errors_stay_inside_extern_boundary(
    host_dir: Path, signature: str, export: str, detail: str
) -> None:
    source = _extern("f", signature, None, f"./ffi_host.js#{export}") + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            print(f())
        end func
        """
    )
    ok, failure = _run("js", source, host_dir)
    assert not ok
    assert failure == f"Extern function 'f' failed: {detail}"


# ---------------------------------------------------------------------------
# Phase 1 / FFI v2: Fallible externs (Result[T, String]), async extern func,
# and records / enums / tagged unions across the host boundary
# ---------------------------------------------------------------------------


def test_async_extern_parses_and_formats() -> None:
    source = textwrap.dedent(
        """\
        export async extern func fetch_row(id: Int) -> Result[Int, String] with ffi
            python "ffi_host:async_double"
            js "./ffi_host.js#asyncDouble"
        end func fetch_row
        """
    )
    program = parse(source)
    (defn,) = program.definitions
    assert defn.is_extern
    assert defn.is_async
    assert defn.exported
    assert defn.extern_bindings == {
        "python": "ffi_host:async_double",
        "js": "./ffi_host.js#asyncDouble",
    }

    messy = (
        "export async extern func fetch_row(id: Int) -> Result[Int, String] with ffi\n"
        'python "ffi_host:async_double"\n'
        '        js   "./ffi_host.js#asyncDouble"\n'
        "end func\n"
    )
    formatted = format_source(messy)
    assert formatted == (
        "export async extern func fetch_row(id: Int) -> Result[Int, String] with ffi\n"
        '    python "ffi_host:async_double"\n'
        '    js "./ffi_host.js#asyncDouble"\n'
        "end func\n"
    )
    assert format_source(formatted) == formatted


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            'extern async func f(x: Int) -> Int with ffi\n    python "m:f"\nend func',
            "Write 'async extern func', not 'extern async func'",
        ),
        (
            '@untested("host")\nasync extern func f(x: Int) -> Int with ffi\n'
            '    python "m:f"\nend func',
            "@untested is not needed on an extern function",
        ),
    ],
)
def test_malformed_async_externs_are_parse_errors(body: str, message: str) -> None:
    with pytest.raises(ParseError) as raised:
        parse(body + "\n")
    assert message in str(raised.value)


def test_geno_doc_marks_an_async_extern(tmp_path: Path) -> None:
    source = (
        _extern(
            "fetch_val",
            "(x: Int) -> Result[Int, String]",
            "ffi_host:async_double",
            "./ffi_host.js#asyncDouble",
            is_async=True,
        )
        + "func main() -> Unit\n    return ()\nend func\n"
    )
    (tmp_path / "main.geno").write_text(source, encoding="utf-8")
    proc = _geno_cli("doc", "main.geno", "-o", "doc.html", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    html = (tmp_path / "doc.html").read_text(encoding="utf-8")
    assert '<span class="keyword">async extern func</span> fetch_val' in html


def test_async_extern_respects_target_checks() -> None:
    source = _extern(
        "only_js_async", "(x: Int) -> Int", None, "m#f", is_async=True
    ) + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            print(await only_js_async(2))
        end func
        """
    )
    assert not _check(source, target="python-cli").ok
    assert not _check(source, target="browser").ok
    assert not _check(source, target="python-hosted").ok
    assert _check(source, target="node-cli").ok


@pytest.mark.parametrize("backend", BACKENDS)
def test_fallible_extern_returns_ok_and_err(backend: str, host_dir: Path) -> None:
    source = (
        _extern(
            "safe_sqrt", "(x: Float) -> Result[Float, String]", "math:sqrt", "Math.sqrt"
        )
        + _extern(
            "safe_boom",
            "() -> Result[Int, String]",
            "ffi_host:boom",
            "./ffi_host.js#boom",
        )
        + _extern(
            "safe_unit",
            "() -> Result[Unit, String]",
            "ffi_host:nothing",
            "./ffi_host.js#nothing",
        )
        + _extern(
            "safe_opt",
            "() -> Result[Option[Int], String]",
            "ffi_host:nothing",
            "./ffi_host.js#nothing",
        )
        + textwrap.dedent(
            """\
            func main() -> Unit with ffi, io
                print(safe_sqrt(9.0))
                match safe_boom() with
                    | Ok(v) -> print(f"unexpected {v}")
                    | Err(msg) -> print(contains(msg, "bad input"))
                end match
                print(safe_unit())
                print(safe_opt())
            end func
            """
        )
    )
    assert _run(backend, source, host_dir) == (
        True,
        "Ok(value: 3.0)\ntrue\nOk(value: ())\nOk(value: None)\n",
    )


@pytest.mark.parametrize("backend", BACKENDS)
def test_fallible_extern_still_panics_on_load_failure_and_mismatch(
    backend: str, host_dir: Path
) -> None:
    bad_load = _extern(
        "missing",
        "() -> Result[Int, String]",
        "no_such_module_for_geno:f",
        "./no_such_module.js#f",
    ) + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            print(missing())
        end func
        """
    )
    ok, msg = _run(backend, bad_load, host_dir)
    assert not ok
    assert "could not load" in msg, msg

    mismatch = _extern(
        "bad_type",
        "() -> Result[Int, String]",
        "ffi_host:string_for_int",
        "./ffi_host.js#stringForInt",
    ) + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            print(bad_type())
        end func
        """
    )
    ok, msg = _run(backend, mismatch, host_dir)
    assert not ok
    assert "returned a value that is not Int:" in msg, msg


@pytest.mark.parametrize("backend", BACKENDS)
def test_fallible_extern_enforces_string_limit_on_error_message(
    backend: str, host_dir: Path
) -> None:
    source = _extern(
        "boom_huge",
        "(n: Int) -> Result[Int, String]",
        "ffi_host:boom_huge",
        "./ffi_host.js#boomHuge",
    ) + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            let r = boom_huge(30)
            print("returned")
        end func
        """
    )
    if backend == "interpreter":
        result = api.run(
            source,
            config=api.RunConfig(
                capabilities={"ffi", "print"}, allow_ffi=True, max_collection_size=10
            ),
        )
        assert not result.ok
        message = result.diagnostics[0].message
    else:
        if backend == "python":
            path = host_dir / "program.py"
            code = "_GENO_MAX_COLLECTION_SIZE = 10\n" + compile_to_python(source)
            command = [sys.executable, str(path)]
        else:
            path = host_dir / "program.js"
            js = compile_to_js(source)
            assert isinstance(js, str)
            code = "globalThis.__GENO_MAX_COLLECTION_SIZE = 10;\n" + js
            command = ["node", str(path)]
        path.write_text(code, encoding="utf-8")
        proc = subprocess.run(
            [*command, "--cap", "ffi,print"],
            capture_output=True,
            text=True,
            timeout=GENERATED_PROGRAM_TIMEOUT,
            cwd=host_dir,
            env={**os.environ, "PYTHONPATH": str(host_dir)},
        )
        assert proc.returncode != 0, proc.stdout
        message = _failure_line(proc.stderr)
    assert "String size exceeds limit" in message, message


@needs_node
def test_fallible_js_extern_panics_on_throwing_getter_not_err(host_dir: Path) -> None:
    source = _extern(
        "bad_getter",
        "() -> Result[Map[String, Int], String]",
        None,
        "./ffi_host.js#throwingGetter",
    ) + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            print(bad_getter())
        end func
        """
    )
    ok, msg = _run("js", source, host_dir)
    assert not ok
    assert msg == "Extern function 'bad_getter' failed: Error: getter_boom"


@pytest.mark.parametrize("backend", PYTHON_BACKENDS)
def test_fallible_python_extern_enforces_int_bit_limit(
    backend: str, host_dir: Path
) -> None:
    source = _extern(
        "huge", "() -> Result[Int, String]", "ffi_host:huge", None
    ) + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            let r = huge()
            print("returned")
        end func
        """
    )
    ok, msg = _run(backend, source, host_dir)
    assert not ok
    assert msg == "Integer exceeds maximum size (100001 bits)"


@pytest.mark.parametrize("backend", BACKENDS)
def test_async_extern_resolves_rejects_and_runs_once(
    backend: str, host_dir: Path
) -> None:
    source = (
        _extern(
            "async_double",
            "(n: Int) -> Int",
            "ffi_host:async_double",
            "./ffi_host.js#asyncDouble",
            is_async=True,
        )
        + _extern(
            "async_boom_fallible",
            "() -> Result[Int, String]",
            "ffi_host:async_boom",
            "./ffi_host.js#asyncBoom",
            is_async=True,
        )
        + _extern(
            "sync_as_async",
            "(n: Int) -> Int",
            "ffi_host:echo",
            "./ffi_host.js#echo",
            is_async=True,
        )
        + _extern(
            "sync_boom_as_async",
            "() -> Result[Int, String]",
            "ffi_host:boom",
            "./ffi_host.js#boom",
            is_async=True,
        )
        + _extern(
            "count_once",
            "() -> Int",
            "ffi_host:async_count_calls",
            "./ffi_host.js#asyncCountCalls",
            is_async=True,
        )
        + textwrap.dedent(
            """\
            func main() -> Unit with ffi, io
                print(await async_double(21))
                match await async_boom_fallible() with
                    | Ok(v) -> print(f"unexpected {v}")
                    | Err(msg) -> print(contains(msg, "async failure"))
                end match
                print(await sync_as_async(9))
                match await sync_boom_as_async() with
                    | Ok(v) -> print(f"unexpected {v}")
                    | Err(msg) -> print(contains(msg, "bad input"))
                end match
                let pending = count_once()
                let first = await pending
                let second = await pending
                print(first)
                print(second)
            end func
            """
        )
    )
    assert _run(backend, source, host_dir) == (True, "42\ntrue\n9\ntrue\n1\n1\n")


@pytest.mark.parametrize("backend", BACKENDS)
def test_async_extern_infallible_rejection_panics(backend: str, host_dir: Path) -> None:
    source = _extern(
        "async_boom",
        "() -> Int",
        "ffi_host:async_boom",
        "./ffi_host.js#asyncBoom",
        is_async=True,
    ) + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            print(await async_boom())
        end func
        """
    )
    ok, msg = _run(backend, source, host_dir)
    assert not ok
    expected = (
        "Extern function 'async_boom' failed: Error: async failure"
        if backend == "js"
        else "Extern function 'async_boom' failed: ValueError: async failure"
    )
    assert msg == expected


@needs_node
def test_async_js_extern_handles_non_error_rejection(host_dir: Path) -> None:
    source = _extern(
        "reject_str",
        "() -> Result[Int, String]",
        None,
        "./ffi_host.js#asyncRejectString",
        is_async=True,
    ) + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            print(await reject_str())
        end func
        """
    )
    assert _run("js", source, host_dir) == (True, 'Err(error: "plain rejection")\n')


@pytest.mark.parametrize("backend", BACKENDS)
def test_async_extern_without_ffi_is_denied_without_loading(
    backend: str, host_dir: Path
) -> None:
    source = _extern(
        "marked_async",
        "() -> Int",
        "ffi_marker:run",
        "./ffi_marker.js#run",
        is_async=True,
    ) + textwrap.dedent(
        """\
        func main() -> Unit with ffi, io
            print(await marked_async())
        end func
        """
    )
    ok, msg = _run(backend, source, host_dir, capabilities=frozenset({"print"}))
    assert not ok
    assert msg.startswith(
        "Capability denied: 'marked_async' requires the 'ffi' capability"
    ), msg
    assert not (host_dir / "imported.txt").exists()


def test_async_extern_in_interpreter_works_inside_running_event_loop(
    host_dir: Path,
) -> None:
    import asyncio

    source = _extern(
        "async_double",
        "(n: Int) -> Int",
        "ffi_host:async_double",
        None,
        is_async=True,
    ) + textwrap.dedent(
        """\
        func main() -> Int with ffi
            return await async_double(15)
        end func
        """
    )

    async def inside_loop() -> int:
        res = api.run(
            source, config=api.RunConfig(capabilities={"ffi"}, allow_ffi=True)
        )
        assert res.ok, _messages(res)
        return res.value

    assert asyncio.run(inside_loop()) == 30


@pytest.mark.parametrize("backend", BACKENDS)
def test_records_enums_unions_generics_and_recursive_types_cross_ffi(
    backend: str, host_dir: Path
) -> None:
    types_src = textwrap.dedent(
        """\
        type Color = Red | Green | Blue
        type Address = Address(city: String, zip: Option[String])
        type User = User(name: String, age: Int, color: Color, address: Address)
        type Shape = Circle(radius: Float) | Rect(w: Float, h: Float) | Point
        type Box[T] = Box(value: T)
        type Tree = Leaf(value: Int) | Node(left: Tree, right: Tree)
        """
    )
    source = (
        types_src
        + _echo("echo_user", "User")
        + _echo("echo_color", "Color")
        + _echo("echo_shape", "Shape")
        + _echo("echo_box", "Box[User]")
        + _echo("echo_tree", "Tree")
        + _extern(
            "async_echo_user",
            "(u: User) -> Result[User, String]",
            "ffi_host:async_echo",
            "./ffi_host.js#asyncEcho",
            is_async=True,
        )
        + textwrap.dedent(
            """\
            func main() -> Unit with ffi, io
                let u = User(name: "Ada", age: 36, color: Green, address: Address(city: "London", zip: Some("SW1")))
                print(echo_user(u) == u)
                print(echo_color(Blue) == Blue)
                let s1: Shape = Circle(radius: 2.5)
                let s2: Shape = Point
                print(echo_shape(s1) == s1)
                print(echo_shape(s2) == s2)
                let b = Box(value: u)
                print(echo_box(b) == b)
                let t = Node(left: Leaf(value: 1), right: Node(left: Leaf(value: 2), right: Leaf(value: 3)))
                print(echo_tree(t) == t)
                match await async_echo_user(u) with
                    | Ok(got) -> print(got == u)
                    | Err(e) -> print(e)
                end match
            end func
            """
        )
    )
    assert _run(backend, source, host_dir) == (
        True,
        "true\ntrue\ntrue\ntrue\ntrue\ntrue\ntrue\n",
    )


@pytest.mark.parametrize("backend", BACKENDS)
def test_record_wire_format_optional_omission_and_extra_keys(
    backend: str, host_dir: Path
) -> None:
    source = (
        textwrap.dedent(
            """\
            type Color = Red | Green
            type Profile = Profile(name: String, nick: Option[String])
            type Shape = Circle(radius: Float) | Point
            """
        )
        + _extern(
            "inspect_args",
            "(p: Profile, c: Color, s: Shape) -> String",
            "ffi_host:args_seen",
            "./ffi_host.js#argsSeen",
        )
        + _extern(
            "from_partial",
            "() -> Profile",
            "ffi_host:user_optional_and_extra",
            "./ffi_host.js#userOptionalAndExtra",
        )
        + textwrap.dedent(
            """\
            func main() -> Unit with ffi, io
                print(inspect_args(p: Profile(name: "Ada", nick: None), c: Red, s: Circle(radius: 1.5)))
                print(from_partial() == Profile(name: "Ada", nick: None))
            end func
            """
        )
    )
    ok, output = _run(backend, source, host_dir)
    assert ok, output
    lines = output.splitlines()
    assert lines[1] == "true"
    if backend == "js":
        assert (
            lines[0]
            == '[{"name":"Ada","nick":null},"Red",{"type":"Circle","radius":1.5}]'
        )
    else:
        assert (
            lines[0]
            == "({'name': 'Ada', 'nick': None}, 'Red', {'type': 'Circle', 'radius': 1.5})"
        )


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(
    ("types_src", "ret_type", "py_fn", "js_fn", "expected"),
    [
        (
            "type User = User(name: String, age: Int)\n",
            "User",
            "ffi_host:user_missing_field",
            "./ffi_host.js#userMissingField",
            "Extern function 'f' returned a value that is not User: missing field \"age\"",
        ),
        (
            "type Color = Red | Green | Blue\n",
            "Color",
            "ffi_host:bad_enum",
            "./ffi_host.js#badEnum",
            'Extern function \'f\' returned a value that is not Color: expected one of "Red", "Green", "Blue", got "Purple"',
        ),
        (
            "type Shape = Circle(radius: Float) | Point\n",
            "Shape",
            "ffi_host:union_missing_type",
            "./ffi_host.js#unionMissingType",
            "Extern function 'f' returned a value that is not Shape: missing field \"type\"",
        ),
        (
            "type Shape = Circle(radius: Float) | Point\n",
            "Shape",
            "ffi_host:union_non_str_type",
            "./ffi_host.js#unionNonStrType",
            "Extern function 'f' returned a value that is not Shape: $.type: expected String, got ",
        ),
        (
            "type Shape = Circle(radius: Float) | Point\n",
            "Shape",
            "ffi_host:union_bad_type",
            "./ffi_host.js#unionBadType",
            'Extern function \'f\' returned a value that is not Shape: $.type: expected one of "Circle", "Point", got "Triangle"',
        ),
        (
            "type Tree = Leaf(value: Int) | Node(left: Tree, right: Tree)\n",
            "Tree",
            "ffi_host:recursive_cycle",
            "./ffi_host.js#recursiveCycle",
            "Extern function 'f' returned a value that is not Tree: the result is nested too deeply",
        ),
    ],
)
def test_adt_host_mismatch_messages_match_across_backends(
    backend: str,
    host_dir: Path,
    types_src: str,
    ret_type: str,
    py_fn: str,
    js_fn: str,
    expected: str,
) -> None:
    source = (
        types_src
        + _extern("f", f"() -> {ret_type}", py_fn, js_fn)
        + "func main() -> Unit with ffi, io\n    print(f())\nend func\n"
    )
    ok, msg = _run(backend, source, host_dir)
    assert not ok
    assert msg.startswith(expected), msg


@pytest.mark.parametrize("backend", BACKENDS)
def test_recursive_adt_argument_depth_limit(backend: str, host_dir: Path) -> None:
    source = (
        "type Chain = End | Link(next: Chain)\n"
        + _echo("echo_chain", "Chain")
        + textwrap.dedent(
            """\
            func main() -> Unit with ffi, io
                var c: Chain = End
                var i = 0
                while i < 400 do
                    c = Link(next: c)
                    i = i + 1
                end while
                let r = echo_chain(c)
                print("done")
            end func
            """
        )
    )
    assert _run(backend, source, host_dir) == (
        False,
        "Extern function 'echo_chain' got an argument nested more than 320 levels deep",
    )


@pytest.mark.parametrize("backend", BACKENDS)
def test_record_oversized_extra_key_hits_string_limit(
    backend: str, host_dir: Path
) -> None:
    source = (
        "type Profile = Profile(name: String)\n"
        + _extern(
            "extra_key",
            "(n: Int) -> Profile",
            "ffi_host:user_oversized_extra",
            "./ffi_host.js#userOversizedExtra",
        )
        + textwrap.dedent(
            """\
            func main() -> Unit with ffi, io
                let p = extra_key(25)
                print("returned")
            end func
            """
        )
    )
    if backend == "interpreter":
        result = api.run(
            source,
            config=api.RunConfig(
                capabilities={"ffi", "print"}, allow_ffi=True, max_collection_size=10
            ),
        )
        assert not result.ok
        message = result.diagnostics[0].message
    else:
        if backend == "python":
            path = host_dir / "program.py"
            code = "_GENO_MAX_COLLECTION_SIZE = 10\n" + compile_to_python(source)
            command = [sys.executable, str(path)]
        else:
            path = host_dir / "program.js"
            js = compile_to_js(source)
            assert isinstance(js, str)
            code = "globalThis.__GENO_MAX_COLLECTION_SIZE = 10;\n" + js
            command = ["node", str(path)]
        path.write_text(code, encoding="utf-8")
        proc = subprocess.run(
            [*command, "--cap", "ffi,print"],
            capture_output=True,
            text=True,
            timeout=GENERATED_PROGRAM_TIMEOUT,
            cwd=host_dir,
            env={**os.environ, "PYTHONPATH": str(host_dir)},
        )
        assert proc.returncode != 0, proc.stdout
        message = _failure_line(proc.stderr)
    assert "String size exceeds limit" in message, message


@pytest.mark.parametrize("backend", PYTHON_BACKENDS)
def test_record_and_enum_ignore_python_subclass_hooks(
    backend: str, host_dir: Path
) -> None:
    source = (
        "type User = User(name: String, age: Int)\n"
        "type Color = Red | Green\n"
        + _extern("user_hook", "() -> User", "ffi_host:user_dict_hook", None)
        + _extern("color_hook", "() -> Color", "ffi_host:enum_str_subclass", None)
        + textwrap.dedent(
            """\
            func main() -> Unit with ffi, io
                print(user_hook() == User(name: "Ada", age: 36))
                print(color_hook() == Green)
            end func
            """
        )
    )
    assert _run(backend, source, host_dir) == (True, "true\ntrue\n")


@needs_node
def test_js_record_accepts_map_and_rejects_date(host_dir: Path) -> None:
    ok_src = (
        "type User = User(name: String, age: Int)\n"
        + _extern("from_map", "() -> User", None, "./ffi_host.js#userAsMap")
        + textwrap.dedent(
            """\
            func main() -> Unit with ffi, io
                print(from_map() == User(name: "Ada", age: 36))
            end func
            """
        )
    )
    assert _run("js", ok_src, host_dir) == (True, "true\n")

    bad_src = (
        "type User = User(name: String, age: Int)\n"
        + _extern("bad_date", "() -> User", None, "./ffi_host.js#when")
        + "func main() -> Unit with ffi, io\n    print(bad_date())\nend func\n"
    )
    ok, msg = _run("js", bad_src, host_dir)
    assert not ok
    assert (
        msg
        == "Extern function 'bad_date' returned a value that is not User: expected a plain object, got Date"
    )


@pytest.mark.parametrize("backend", BACKENDS)
def test_imported_module_record_crosses_ffi(backend: str, host_dir: Path) -> None:
    (host_dir / "Models.geno").write_text(
        "export type Item = Item(id: Int, label: String)\n", encoding="utf-8"
    )
    (host_dir / "main.geno").write_text(
        textwrap.dedent(
            """\
            import Models

            extern func echo_item(item: Item) -> Item with ffi
                python "ffi_host:echo"
                js "./ffi_host.js#echo"
            end func

            func main() -> Unit with ffi, io
                let it = Item(id: 7, label: "widget")
                print(echo_item(it) == it)
            end func
            """
        ),
        encoding="utf-8",
    )
    if backend == "interpreter":
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "run",
                "--unsafe",
                "--cap",
                "ffi,print",
                "main.geno",
            ],
            capture_output=True,
            text=True,
            timeout=GENERATED_PROGRAM_TIMEOUT,
            cwd=host_dir,
            env={**os.environ, "PYTHONPATH": str(host_dir)},
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == "true\n"
    elif backend == "python":
        c = _geno_cli("compile", "main.geno", "-o", "out.py", cwd=host_dir)
        assert c.returncode == 0, c.stderr
        proc = subprocess.run(
            [sys.executable, "out.py", "--cap", "ffi,print"],
            capture_output=True,
            text=True,
            timeout=GENERATED_PROGRAM_TIMEOUT,
            cwd=host_dir,
            env={**os.environ, "PYTHONPATH": str(host_dir)},
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == "true\n"
    else:
        c = _geno_cli(
            "compile", "--target", "js", "main.geno", "-o", "out.js", cwd=host_dir
        )
        assert c.returncode == 0, c.stderr
        proc = subprocess.run(
            ["node", "out.js", "--cap", "ffi,print"],
            capture_output=True,
            text=True,
            timeout=GENERATED_PROGRAM_TIMEOUT,
            cwd=host_dir,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == "true\n"
