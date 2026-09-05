"""Regression tests for two silent-failure classes in compiled execution.

1. ``geno run`` must not swallow a ``NameError`` raised inside the program.
   The entrypoint capture used to wrap the whole ``main()`` call in
   ``except NameError: pass`` so that a program without ``main`` would run.
   That also hid every ``NameError`` the runtime prelude raised by naming a
   builtin the sandbox worker does not provide: execution stopped mid-program
   and the CLI still exited 0.

2. Capability flags must not be parsed from the program's own arguments.
   ``cli_args()`` returns everything after ``--``, so a caller that forwards
   untrusted arguments there must not be able to grant itself capabilities.
"""

from __future__ import annotations

import ast
import builtins
import re
import subprocess
import symtable
import sys
from pathlib import Path

import pytest

from geno import sandbox as sandbox_module
from geno.compiler import _compiled_main_result_capture

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SUPPORT = REPO_ROOT / "geno" / "geno" / "_runtime_support.py"
if not RUNTIME_SUPPORT.exists():  # normal layout
    RUNTIME_SUPPORT = REPO_ROOT / "geno" / "_runtime_support.py"
JS_RUNTIME = REPO_ROOT / "geno" / "_js_runtime_support.js"


def _prelude_builtin_names() -> set[str]:
    """Builtin names the runtime prelude references as free globals."""
    source = RUNTIME_SUPPORT.read_text(encoding="utf-8")
    table = symtable.symtable(source, "_runtime_support.py", "exec")

    used: set[str] = set()

    def walk(scope: symtable.SymbolTable) -> None:
        for symbol in scope.get_symbols():
            if symbol.is_global() and not symbol.is_assigned():
                used.add(symbol.get_name())
        for child in scope.get_children():
            walk(child)

    walk(table)

    tree = ast.parse(source)
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    defined |= {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    return {name for name in used if hasattr(builtins, name)} - defined


def _sandbox_available_names() -> set[str]:
    """Names the sandbox actually binds, queried from the sandbox itself."""
    safe_globals = sandbox_module.create_safe_globals(
        sandbox_module.SandboxConfig(), []
    )
    provided = safe_globals["__builtins__"]
    return set(provided) if isinstance(provided, dict) else set(dir(provided))


def test_prelude_names_no_builtin_the_sandbox_withholds() -> None:
    """The runtime prelude must not name a builtin the sandbox withholds.

    Such a name raises NameError at the point of use -- mid-program, long
    after the prelude loaded -- which is how the silent-truncation bug in
    ``geno run`` arose.  The fix direction is always to change the prelude,
    never to widen the sandbox: `id`, `object` and `pow` are withheld
    deliberately (see BLOCKED_BUILTINS and TestSandboxConstantConsistency).
    """
    missing = _prelude_builtin_names() - _sandbox_available_names()
    assert not missing, (
        "runtime prelude references builtins the sandbox does not provide: "
        f"{sorted(missing)}. Rewrite the prelude to avoid them -- widening "
        "the sandbox to satisfy the prelude trades a correctness bug for a "
        "security one."
    )


def test_entrypoint_capture_guards_only_the_name_lookup() -> None:
    """The generated capture must not wrap the call itself in try/except."""
    emitted = _compiled_main_result_capture(False, allow_missing_main=True)
    tree = ast.parse(emitted)
    handlers = [node for node in ast.walk(tree) if isinstance(node, ast.Try)]
    assert len(handlers) == 1
    body = handlers[0].body
    assert len(body) == 1
    statement = body[0]
    # The guarded body binds the entrypoint; it must not call anything.
    assert isinstance(statement, ast.Assign)
    assert not [node for node in ast.walk(statement) if isinstance(node, ast.Call)], (
        "the try/except guarding a missing `main` must cover only the name "
        "lookup -- wrapping the call swallows NameErrors raised inside the "
        "program and silently truncates execution"
    )


def _run_geno(tmp_path: Path, source: str, *args: str) -> subprocess.CompletedProcess:
    program = tmp_path / "prog.geno"
    program.write_text(source, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "geno", "run", str(program), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize(
    "expression",
    [
        "to_string([1, 2, 3])",
        'to_string(map_from_list([("a", 1)]))',
        "to_string((1, 2))",
    ],
)
def test_geno_run_does_not_silently_truncate(tmp_path: Path, expression: str) -> None:
    """Formatting a container must not abort the program with exit 0."""
    result = _run_geno(
        tmp_path,
        "func main() -> Int\n"
        '    print("start")\n'
        f"    print({expression})\n"
        '    print("end")\n'
        "    return 0\n"
        "end func main\n",
    )
    assert "start" in result.stdout
    assert "end" in result.stdout, (
        "execution stopped before the final statement: "
        f"stdout={result.stdout!r} stderr={result.stderr[-400:]!r}"
    )


def test_geno_run_still_allows_a_program_without_main(tmp_path: Path) -> None:
    """The missing-`main` case the guard exists for keeps working."""
    result = _run_geno(
        tmp_path,
        "func helper() -> Int\n    example () -> 1\n    return 1\nend func helper\n",
    )
    assert result.returncode == 0, result.stderr[-400:]


def test_geno_run_surfaces_error_handling_inside_the_program(
    tmp_path: Path,
) -> None:
    """A handled failure must reach the user, not vanish mid-match."""
    result = _run_geno(
        tmp_path,
        "func main() -> String\n"
        '    match json_parse("{bad json") with\n'
        "        | Ok(v) -> return json_to_string(v)\n"
        '        | Err(e) -> return "ERR:" + e\n'
        "    end match\n"
        "end func main\n",
    )
    assert "ERR:" in result.stdout, (
        f"stdout={result.stdout!r} stderr={result.stderr[-400:]!r}"
    )


def test_python_runtime_stops_cap_parsing_at_the_separator() -> None:
    """`--cap` after `--` is program input, not a capability grant."""
    from geno import _runtime_support

    original = sys.argv
    try:
        sys.argv = ["prog", "--", "--cap", "env,fs,process", "somefile.txt"]
        assert _runtime_support._geno_parse_caps() == set()

        sys.argv = ["prog", "--cap", "env", "--", "--cap", "process"]
        assert _runtime_support._geno_parse_caps() == {"env"}
    finally:
        sys.argv = original


def test_js_runtime_stops_cap_parsing_at_the_separator() -> None:
    """The JS prelude must apply the same boundary as the Python one."""
    source = JS_RUNTIME.read_text(encoding="utf-8")
    match = re.search(
        r"const _GENO_CAPS = \(function\(\) \{.*?\n\}\)\(\);", source, re.DOTALL
    )
    assert match is not None, "could not locate the _GENO_CAPS initialiser"
    assert '=== "--") break' in match.group(0), (
        "the JS capability parser must stop at the `--` separator so program "
        "arguments cannot grant capabilities"
    )
