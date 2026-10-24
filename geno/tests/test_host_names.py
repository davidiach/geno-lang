"""Geno names that collide with host or runtime names (proposal 0011)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from geno.compiler import CompileError, compile_to_python
from geno.js_compiler import JSCompileError, compile_to_js
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _assert_expected_backend_outputs,
    _compiled_js_output,
    _compiled_js_project_output,
    _compiled_python_output,
    _compiled_python_project_output,
    _interpreter_output,
    _interpreter_project_output,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _assert_parity(source: str, expected: str) -> None:
    _assert_expected_backend_outputs(
        label="host names",
        context=source,
        expected=expected,
        interp_out=_interpreter_output(source),
        py_out=_compiled_python_output(source),
        js_out=_compiled_js_output(source) if HAS_NODE else expected,
    )


HOST_NAMED_PROGRAM = """type Token = T(text: String) | Exception(code: Int)

type Shape = Error(msg: String) | Object(n: Int) | Promise | Union | Constructor

func len(xs: List[Int]) -> Int
    example [1] -> 1
    return length(xs)
end func

func sum(xs: List[Int]) -> Int
    example [1, 2] -> 3
    var total = 0
    for x in xs do
        total = total + x
    end for
    return total
end func

func describe(t: Token) -> String
    example T("a") -> "text a"
    match t with
        | T(text) -> return "text " + text
        | Exception(code) -> return f"error {code}"
    end match
end func

func show(s: Shape) -> String
    example Promise -> "promise"
    match s with
        | Error(msg) -> return "error " + msg
        | Object(n) -> return f"object {n}"
        | Promise -> return "promise"
        | Union -> return "union"
        | Constructor -> return "constructor"
    end match
end func

func main() -> Unit
    let id = 3
    let list = [1, 2]
    let str = "s"
    let object = 6
    let console = 1
    let require = 2
    print(len(list) + id + sum(list) + object + console + require)
    print(str)
    print([T("x"), Exception(7)])
    print(describe(Exception(7)))
    let shapes = [Error("x"), Object(2), Promise, Union, Constructor]
    print(shapes.map(show))
    print(shapes)
    print(Error("a") == Error("a"))
end func
"""

HOST_NAMED_OUTPUT = (
    "17\n"
    "s\n"
    '[T(text: "x"), Exception(code: 7)]\n'
    "error 7\n"
    '["error x", "object 2", "promise", "union", "constructor"]\n'
    '[Error(msg: "x"), Object(n: 2), Promise, Union, Constructor]\n'
    "true\n"
)


def test_host_names_are_ordinary_geno_names() -> None:
    _assert_parity(HOST_NAMED_PROGRAM, HOST_NAMED_OUTPUT)


def test_host_names_in_the_default_run_lane(tmp_path: Path) -> None:
    """`geno run` executes compiled Python in the process sandbox."""
    path = tmp_path / "names.geno"
    path.write_text(HOST_NAMED_PROGRAM, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "geno", "run", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=_REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == HOST_NAMED_OUTPUT


def test_parameters_fields_and_bindings_may_use_host_names() -> None:
    _assert_parity(
        """type Rec = Rec(id: Int, list: List[Int])

func make(list: Int, dict: Int, str: Int) -> Int
    example (1, 2, 3) -> 6
    return list + dict + str
end func

func main() -> Unit
    print(make(str: 3, list: 1, dict: 2))
    let r = Rec(id: 1, list: [2])
    print(r.list)
    print(r)
    let f = fn(len: Int) -> len + 1
    print(f(1))
    print([1, 2].map(fn(sum) -> sum * 2))
    print([fetch * 2 for fetch in [1, 2] if fetch > 1])
    let (min, max) = (1, 9)
    print(min + max)
    match [1, 2, 3] with
        | [id, ...list] -> print(id + length(list))
        | _ -> print(0)
    end match
end func
""",
        "6\n[2]\nRec(id: 1, list: [2])\n2\n[2, 4]\n[4]\n10\n3\n",
    )


def test_top_level_definitions_shadow_builtins_on_every_backend() -> None:
    """A program's own `max` does not replace the runtime's."""
    _assert_parity(
        """let min = 10

func max(a: Int, b: Int) -> Int
    example (1, 2) -> 100
    return 100
end func

func set() -> Int
    example () -> 1
    return 1
end func

func main() -> Unit
    print(max(1, 2) + min + set())
    print(set_size(set_from_list([1, 2, 2])))
    print([3, 1].sort())
end func
""",
        "111\n2\n[1, 3]\n",
    )


def test_a_trait_method_may_use_a_host_name() -> None:
    _assert_parity(
        """trait Sized
    func len(self: Self) -> Int
end trait

type Bag = Bag(items: List[Int])

impl Sized for Bag
    func len(self: Bag) -> Int
        example Bag([1]) -> 1
        return length(self.items)
    end func
end impl

func main() -> Unit
    print(Bag([1, 2]).len())
    print(len(Bag([1])))
end func
""",
        "2\n1\n",
    )


def test_host_names_across_modules() -> None:
    modules = {
        "Lib": (
            "type Fault = Error(msg: String)\n"
            "func len(xs: List[Int]) -> Int\n"
            "    example [1] -> 1\n"
            "    return length(xs)\n"
            "end func\n"
        )
    }
    source = (
        "import Lib\n"
        "func main() -> Unit\n"
        "    print(len([1, 2]))\n"
        '    print(Error("x"))\n'
        "end func\n"
    )
    expected = '2\nError(msg: "x")\n'
    _assert_expected_backend_outputs(
        label="host names across modules",
        context=source,
        expected=expected,
        interp_out=_interpreter_project_output(source, modules),
        py_out=_compiled_python_project_output(source, modules),
        js_out=(_compiled_js_project_output(source, modules) if HAS_NODE else expected),
    )


@pytest.mark.parametrize(
    ("compile_fn", "error_type", "name"),
    [
        (compile_to_python, CompileError, "len"),
        (compile_to_js, JSCompileError, "fetch"),
    ],
)
def test_a_renamed_name_cannot_meet_its_spelling(compile_fn, error_type, name) -> None:
    source = (
        f"func {name}() -> Int\n"
        "    example () -> 1\n"
        "    return 1\n"
        "end func\n"
        "func main() -> Int\n"
        f"    let {name}_g = 2\n"
        f"    return {name}() + {name}_g\n"
        "end func\n"
    )
    message = f"'{name}' and '{name}_g' would both compile to '{name}_g'"
    with pytest.raises(error_type, match=message):
        compile_fn(source)


@pytest.mark.parametrize(
    ("compile_fn", "error_type"),
    [(compile_to_python, CompileError), (compile_to_js, JSCompileError)],
)
def test_runtime_internals_stay_unreachable(compile_fn, error_type) -> None:
    source = "func main() -> Int\n    return length(_GENO_CAPS)\nend func\n"
    with pytest.raises(error_type, match="reserved runtime name"):
        compile_fn(source, typecheck=False)


def test_no_runtime_name_looks_like_a_renamed_one() -> None:
    """A renamed user name must never land on a runtime binding."""
    from geno.builtin_registry import all_builtin_names
    from geno.compiler import (
        _PYTHON_LOCAL_RESERVED_NAMES,
        _PYTHON_PROJECT_RESERVED_NAMES,
        RESERVED_PRELUDE_NAMES,
    )
    from geno.js_compiler import _JS_LOCAL_RESERVED_NAMES, JS_RESERVED_PRELUDE_NAMES

    names = (
        set(all_builtin_names())
        | RESERVED_PRELUDE_NAMES
        | _PYTHON_PROJECT_RESERVED_NAMES
        | _PYTHON_LOCAL_RESERVED_NAMES
        | JS_RESERVED_PRELUDE_NAMES
        | _JS_LOCAL_RESERVED_NAMES
    )
    assert sorted(n for n in names if n.endswith(("_g", "_kw"))) == []
