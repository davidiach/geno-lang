"""A `let` in a nested block must not leak into the enclosing scope.

Geno blocks scope their bindings: `test_shadowing` in ``test_interpreter.py``
pins the interpreter to returning 5 for a program whose `if` body rebinds `x`
to 10, and the JS backend agrees because `const`/`let` are block scoped in
JavaScript.  The Python backend emitted the same name inside the block, and
Python scopes per function rather than per block, so the inner binding
overwrote the outer one and the compiled program returned 10 -- a wrong
answer, silently, on the project's own test program.

The compiler now renames a binding that shadows an enclosing one and resolves
later references in that block to the fresh name.  Re-binding in the *same*
block is left alone: the interpreter and the Python backend both treat that
as a rebind (a closure created before it observes the new value), so renaming
there would introduce a divergence rather than remove one.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from geno.compiler import compile_to_python
from geno.interpreter import Interpreter
from geno.lexer import Lexer
from geno.parser import Parser

# (id, function body, expected result)
SCOPE_CASES = [
    pytest.param(
        "    let x: Int = 5\n    if true then\n        let x: Int = 10\n    end if\n    return x",
        5,
        id="if_body",
    ),
    pytest.param(
        "    let x: Int = 5\n    if false then\n        let x: Int = 1\n    else\n        let x: Int = 10\n    end if\n    return x",
        5,
        id="else_body",
    ),
    pytest.param(
        "    let x: Int = 5\n    var n: Int = 0\n    while n < 1 do\n        let x: Int = 10\n        n = n + 1\n    end while\n    return x",
        5,
        id="while_body",
    ),
    pytest.param(
        "    let x: Int = 5\n    if true then\n        if true then\n            let x: Int = 10\n        end if\n    end if\n    return x",
        5,
        id="doubly_nested",
    ),
    pytest.param(
        "    var x: Int = 5\n    if true then\n        var x: Int = 10\n    end if\n    return x",
        5,
        id="var_binding",
    ),
    pytest.param(
        "    let x: Int = 5\n    if true then\n        let x: Int = 1\n    end if\n    if true then\n        let x: Int = 2\n    end if\n    return x",
        5,
        id="two_sibling_blocks",
    ),
    # The inner binding is still usable inside its own block.
    pytest.param(
        "    let x: Int = 5\n    if true then\n        let x: Int = 10\n        return x\n    end if\n    return x",
        10,
        id="inner_binding_is_visible_inside",
    ),
    # Same-scope rebinding is a rebind, not a new scope.
    pytest.param(
        "    let x: Int = 5\n    let x: Int = 10\n    return x",
        10,
        id="same_scope_rebind",
    ),
    # A block that shadows nothing must be untouched.
    pytest.param(
        "    let x: Int = 5\n    if true then\n        let y: Int = 10\n    end if\n    return x",
        5,
        id="no_shadowing",
    ),
]


def _program(body: str) -> str:
    return f"func main() -> Int\n{body}\nend func\n"


@pytest.mark.parametrize(("body", "expected"), SCOPE_CASES)
def test_compiled_python_scopes_block_bindings(body: str, expected: int) -> None:
    """This is the lane that was wrong: Python has no block scope."""
    namespace: dict[str, object] = {}
    exec(compile_to_python(_program(body)), namespace)
    assert namespace["main"]() == expected


@pytest.mark.parametrize(("body", "expected"), SCOPE_CASES)
def test_interpreter_scopes_block_bindings(body: str, expected: int) -> None:
    program = Parser(Lexer(_program(body)).tokenize()).parse_program()
    assert Interpreter().run(program) == expected


@pytest.mark.parametrize(("body", "expected"), SCOPE_CASES)
def test_interpreter_and_compiled_python_agree(body: str, expected: int) -> None:
    """The divergence itself, stated directly."""
    source = _program(body)
    program = Parser(Lexer(source).tokenize()).parse_program()
    namespace: dict[str, object] = {}
    exec(compile_to_python(source), namespace)
    assert Interpreter().run(program) == namespace["main"]()


def test_parameter_is_not_overwritten_by_a_nested_binding() -> None:
    source = """func main() -> Int
    example () -> 5
    return helper(5)
end func

func helper(p: Int) -> Int
    example (1) -> 1
    if true then
        let p: Int = 10
    end if
    return p
end func
"""
    namespace: dict[str, object] = {}
    exec(compile_to_python(source), namespace)
    assert namespace["main"]() == 5


def test_shadowed_binding_does_not_collide_with_a_real_user_name() -> None:
    """The generated name must not capture a name the program itself uses."""
    source = _program(
        "    let x: Int = 5\n"
        "    if true then\n"
        "        let x: Int = 10\n"
        "        let y: Int = x\n"
        "        return y\n"
        "    end if\n"
        "    return x"
    )
    namespace: dict[str, object] = {}
    exec(compile_to_python(source), namespace)
    assert namespace["main"]() == 10


def test_geno_run_agrees_with_the_interpreter_lane(tmp_path: Path) -> None:
    """End to end, both `geno run` lanes must print the same result."""
    program = tmp_path / "prog.geno"
    program.write_text(
        _program(
            "    let x: Int = 5\n    if true then\n        let x: Int = 10\n    end if\n    return x"
        ),
        encoding="utf-8",
    )
    root = Path(__file__).resolve().parents[2]
    outputs = []
    for extra in ([], ["--unsafe"]):
        completed = subprocess.run(
            [sys.executable, "-m", "geno", "run", *extra, str(program)],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr[-400:]
        outputs.append(completed.stdout.strip().splitlines()[-1])
    assert outputs[0] == outputs[1] == "=> 5"


def test_generated_python_is_syntactically_valid_for_deep_nesting() -> None:
    """Renaming must survive several levels without colliding."""
    body = "    let x: Int = 0\n"
    for depth in range(1, 6):
        body += (
            "    " * depth
            + "if true then\n"
            + "    " * depth
            + f"    let x: Int = {depth}\n"
        )
    for depth in range(5, 0, -1):
        body += "    " * depth + "end if\n"
    body += "    return x"
    namespace: dict[str, object] = {}
    exec(compile_to_python(_program(body)), namespace)
    assert namespace["main"]() == 0


# ---------------------------------------------------------------------------
# The JavaScript backend's side of the same rule.
#
# JS is block scoped, so nesting needed no help -- but it rejects two `const`
# declarations of one name in a single block, and a same-scope Geno rebind
# (`let x = 1` then `let x = 2`) emitted exactly that.  The program was a
# SyntaxError and did not run at all, where the interpreter and the Python
# backend both returned the rebound value.  A block that re-binds a name now
# introduces it with `let` and makes the later occurrences assignments, which
# keeps the rebind observable to a closure made in between.
# ---------------------------------------------------------------------------

REBIND_CASES = [
    pytest.param(
        "    let x: Int = 5\n    let x: Int = 10\n    return x", 10, id="let_twice"
    ),
    pytest.param(
        "    let x: Int = 1\n    let x: Int = 2\n    let x: Int = 3\n    return x",
        3,
        id="let_three_times",
    ),
    pytest.param(
        "    var x: Int = 5\n    var x: Int = 10\n    return x", 10, id="var_twice"
    ),
    pytest.param(
        "    let y: Int = 0\n    if true then\n        let x: Int = 1\n        let x: Int = 2\n        return x\n    end if\n    return y",
        2,
        id="rebind_inside_a_block",
    ),
    pytest.param(
        "    let x: Int = 5\n    let y: Int = 10\n    return x + y",
        15,
        id="no_rebind_is_untouched",
    ),
]


def _run_node(source: str) -> str:
    from geno.js_compiler import compile_to_js

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".mjs", delete=False, encoding="utf-8", newline="\n"
    ) as handle:
        handle.write(compile_to_js(source))
        path = handle.name
    try:
        completed = subprocess.run(
            ["node", path], capture_output=True, text=True, timeout=60, check=False
        )
        assert completed.returncode == 0, completed.stderr[-400:]
        return completed.stdout.strip().splitlines()[-1]
    finally:
        Path(path).unlink(missing_ok=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not available")
@pytest.mark.parametrize(("body", "expected"), REBIND_CASES)
def test_js_backend_runs_a_same_scope_rebind(body: str, expected: int) -> None:
    """This emitted two `const` declarations and would not parse."""
    assert _run_node(_program(body)) == str(expected)


@pytest.mark.parametrize(("body", "expected"), REBIND_CASES)
def test_compiled_python_agrees_on_a_same_scope_rebind(
    body: str, expected: int
) -> None:
    namespace: dict[str, object] = {}
    exec(compile_to_python(_program(body)), namespace)
    assert namespace["main"]() == expected


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not available")
def test_a_closure_made_between_two_bindings_sees_the_rebind() -> None:
    """Why the rebind is an assignment rather than a second binding.

    Renaming instead would give the closure the *old* value on JS while the
    interpreter and Python gave it the new one -- trading one divergence for
    another.
    """
    source = """func main() -> Int
    let x: Int = 1
    let f = fn() -> x
    let x: Int = 2
    return f()
end func
"""
    namespace: dict[str, object] = {}
    exec(compile_to_python(source), namespace)
    assert namespace["main"]() == 2
    assert _run_node(source) == "2"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not available")
def test_a_binding_may_rebind_a_parameter() -> None:
    """A `let` re-binding a parameter agrees across the backends.

    This one was never broken: the JS function body is wrapped in `try {`,
    so the emitted `const p` sat in a nested block and legally shadowed the
    parameter.  It now compiles to an assignment instead, which is what the
    interpreter and the Python backend do -- so a closure over the parameter
    made beforehand observes the new value on every backend rather than only
    on two of them.
    """
    source = """func main() -> Int
    example () -> 9
    return helper(1)
end func

func helper(p: Int) -> Int
    example (1) -> 9
    let p: Int = 9
    return p
end func
"""
    namespace: dict[str, object] = {}
    exec(compile_to_python(source), namespace)
    assert namespace["main"]() == 9
    assert _run_node(source) == "9"


def test_only_a_rebound_name_loses_const() -> None:
    """`const` must survive for every name the block does not re-bind."""
    from geno.js_compiler import compile_to_js

    js = compile_to_js(
        _program(
            "    let a: Int = 1\n    let b: Int = 2\n    let b: Int = 3\n    return a + b"
        )
    )
    body = js[js.index("function main") :]
    body = body[: body.index("catch")]
    assert "const a = " in body, body
    assert "let b = " in body, body
    assert "const b = " not in body, body


# ---------------------------------------------------------------------------
# Every binder, not just `let`/`var`.
#
# The first version of the block-scoping fix routed only `let` and `var`
# through the scope machinery.  The other binders -- a `for` variable, a
# `catch` variable, a match pattern, a tuple destructure -- were still emitted
# under their plain mangled name, which broke two ways at once: the Python
# backend read them back through an enclosing block's rename (so the arm body
# saw the wrong variable), and they leaked into the enclosing function scope
# (so they overwrote a same-named outer binding).  On the JavaScript side the
# same half-fix turned a legal shadow into an assignment to a `const`.
#
# These cases pin all three execution paths together, which is the only way
# the class of bug shows up at all.
# ---------------------------------------------------------------------------

BINDER_CASES = [
    pytest.param(
        """func main() -> Int
    let v: Int = 1
    if true then
        let v: Int = 2
        let o: Option[Int] = Some(7)
        match o with
        | Some(v) -> return v
        | None -> return 0
        end match
    end if
    return v
end func
""",
        7,
        id="match_arm_binding_is_not_read_through_a_rename",
    ),
    pytest.param(
        """func main() -> Int
    let v: Int = 1
    let o: Option[Int] = Some(7)
    match o with
    | Some(v) -> let z: Int = v
    | None -> let z: Int = 0
    end match
    return v
end func
""",
        1,
        id="match_pattern_does_not_leak_outward",
    ),
    pytest.param(
        """func main() -> Int
    let o: Option[Int] = Some(1)
    match o with
    | Some(v) ->
        let v: Int = 9
        return v
    | None -> return 0
    end match
end func
""",
        9,
        id="match_arm_may_rebind_its_pattern_variable",
    ),
    pytest.param(
        """func main() -> Int
    let i: Int = 1
    for i: Int in [7, 8] do
        let z: Int = i
    end for
    return i
end func
""",
        1,
        id="for_variable_does_not_leak_outward",
    ),
    pytest.param(
        """func main() -> Int
    var total: Int = 0
    for i: Int in [1, 2, 3] do
        let i: Int = 10
        total = total + i
    end for
    return total
end func
""",
        30,
        id="for_body_may_rebind_the_loop_variable",
    ),
    pytest.param(
        """func main() -> Int
    var total: Int = 0
    for i: Int in [1, 2] do
        for i: Int in [10, 20] do
            total = total + i
        end for
    end for
    return total
end func
""",
        60,
        id="nested_loops_may_share_a_variable_name",
    ),
    pytest.param(
        """func main() -> String
    let e: String = "outer"
    try
        throw "boom"
    catch e: String
        let z: String = e
    end try
    return e
end func
""",
        "outer",
        id="catch_variable_does_not_leak_outward",
    ),
    pytest.param(
        """func main() -> String
    let e: String = "outer"
    if true then
        let e: String = "block"
        try
            throw "boom"
        catch e: String
            return e
        end try
    end if
    return e
end func
""",
        "boom",
        id="catch_variable_is_not_read_through_a_rename",
    ),
    pytest.param(
        """func main() -> String
    try
        throw "boom"
    catch e: String
        let e: String = "rebound"
        return e
    end try
end func
""",
        "rebound",
        id="catch_body_may_rebind_the_catch_variable",
    ),
    pytest.param(
        """func main() -> Int
    let a: Int = 1
    let (a, b): (Int, Int) = (7, 2)
    return a + b
end func
""",
        9,
        id="destructure_may_rebind_an_existing_name",
    ),
    pytest.param(
        """func main() -> Int
    let t: (Int, Int) = (1, 2)
    let (a, b): (Int, Int) = t
    let a: Int = 5
    return a + b
end func
""",
        7,
        id="a_later_let_may_rebind_a_destructured_name",
    ),
    pytest.param(
        """func main() -> Int
    let a: Int = 1
    if true then
        let (a, b): (Int, Int) = (7, 2)
        let c: Int = a + b
    end if
    return a
end func
""",
        1,
        id="destructure_does_not_leak_outward",
    ),
    pytest.param(
        """func main() -> Int
    let x: Int = 1
    if true then
        let x: Int = 2
        let f = fn() do
            let x: Int = 3
            return x
        end fn
        return f()
    end if
    return x
end func
""",
        3,
        id="block_lambda_binding_is_its_own",
    ),
    pytest.param(
        """func main() -> Int
    let x: Int = 1
    if true then
        let x: Int = 2
        let f = fn() -> x
        return f()
    end if
    return x
end func
""",
        2,
        id="closure_reads_the_enclosing_block_binding",
    ),
    # Per-iteration capture has to keep working through the block-scope
    # machinery: the capture rebinds the loop variable to a per-iteration copy
    # and must win over the block's own resolution of that name, or every
    # closure ends up sharing the final value.
    pytest.param(
        """func main() -> Int
    let x: Int = 9
    if true then
        let x: Int = 8
        var fs: List[() -> Int] = []
        for x: Int in [1, 2, 3] do
            fs = append(fs, fn() -> x)
        end for
        return fs[0]() * 100 + fs[1]() * 10 + fs[2]()
    end if
    return x
end func
""",
        123,
        id="per_iteration_capture_survives_a_shadowed_loop_variable",
    ),
]


@pytest.mark.parametrize(("source", "expected"), BINDER_CASES)
def test_compiled_python_agrees_with_the_interpreter_on_every_binder(
    source: str, expected: object
) -> None:
    program = Parser(Lexer(source).tokenize()).parse_program()
    namespace: dict[str, object] = {}
    exec(compile_to_python(source), namespace)
    assert namespace["main"]() == expected
    assert Interpreter().run(program) == expected


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not available")
@pytest.mark.parametrize(("source", "expected"), BINDER_CASES)
def test_compiled_js_agrees_on_every_binder(source: str, expected: object) -> None:
    assert _run_node(source) == str(expected)
