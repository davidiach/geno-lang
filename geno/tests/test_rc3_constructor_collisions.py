"""Constructor collision diagnostics agree across source and project checking (#209)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from geno.api import RunConfig, check, check_path, run, run_path
from geno.dependency_graph import DependencyGraph
from geno.diagnostics import ErrorCode
from geno.project_graph import ProjectGraph
from geno.typechecker import TypeChecker
from geno.types import GenoTypeError

MODULES = {
    "A": "export type AValue = Shared(left: Int)\n",
    "B": "export type BValue = Shared(right: String)\n",
    "ViaA": "import A\n",
    "ViaB": "import B\n",
}
MAIN = "func main() -> Int\n    return 0\nend func\n"


def _project(tmp_path: Path, source: str, modules: dict[str, str]) -> DependencyGraph:
    sources = {"Main": source, **modules}
    names = ", ".join(f'"{name}"' for name in sources)
    (tmp_path / "geno.toml").write_text(
        f'entrypoint = "Main"\nfiles = [{names}]\n', encoding="utf-8"
    )
    for name, text in sources.items():
        (tmp_path / f"{name}.geno").write_text(text, encoding="utf-8")
    return DependencyGraph.resolve(ProjectGraph.discover(tmp_path))


@pytest.mark.parametrize(
    ("imports", "later_module", "earlier_type"),
    [
        ("import A\nimport B\n", "B", "AValue"),
        ("import B\nimport A\n", "A", "BValue"),
        ("import ViaA\nimport B\n", "B", "AValue"),
        ("import A\nimport ViaB\n", "B", "AValue"),
    ],
)
def test_project_and_source_imports_report_the_same_collision(
    tmp_path, imports, later_module, earlier_type
):
    graph = _project(tmp_path, imports + MAIN, MODULES)
    failures = []
    for project_mode in (False, True):
        checker = TypeChecker()
        with pytest.raises(GenoTypeError) as failure:
            if project_mode:
                checker.check_project_graph(graph)
            else:
                checker.check_program(
                    graph.parsed["Main"],
                    modules={name: graph.parsed[name] for name in MODULES},
                )
        error = failure.value
        assert error.error_code == ErrorCode.TYPE_DUPLICATE_DEFINITION
        assert error.message == (
            f"Constructor 'Shared' is already defined by type '{earlier_type}'"
        )
        assert Path(error.location.filename) == tmp_path / f"{later_module}.geno"
        assert error.location.line == 1
        failures.append(error)
    assert failures[0].location == failures[1].location


@pytest.mark.parametrize(
    "entrypoint", ["source-check", "source-run", "path-check", "path-run"]
)
def test_embedding_entrypoints_reject_colliding_constructors(tmp_path, entrypoint):
    source = "import A\nimport B\n" + MAIN
    _project(tmp_path, source, MODULES)
    config = RunConfig(modules=MODULES)
    if entrypoint == "source-check":
        result = check(source, modules=MODULES)
    elif entrypoint == "source-run":
        result = run(source, config=config)
    elif entrypoint == "path-check":
        result = check_path(tmp_path)
    else:
        result = run_path(tmp_path)
    assert not result.ok
    assert any(
        d.code == ErrorCode.TYPE_DUPLICATE_DEFINITION for d in result.diagnostics
    )
    assert any(
        "Constructor 'Shared' is already defined" in d.message
        for d in result.diagnostics
    )


@pytest.mark.parametrize(
    "command", ["check", "run", "test", "compile-python", "compile-js"]
)
def test_cli_rejects_collisions_before_execution_or_compilation(tmp_path, command):
    source = (
        'import A\nimport B\nfunc main() -> Unit\n    print("executed")\nend func\n'
    )
    _project(tmp_path, source, MODULES)
    output = tmp_path / ("out.js" if command == "compile-js" else "out.py")
    if command.startswith("compile"):
        args = ["compile", str(tmp_path), "-o", str(output)]
        if command == "compile-js":
            args += ["--target", "js"]
    elif command == "run":
        args = ["run", "--unsafe", "--cap", "print", str(tmp_path)]
    else:
        args = [command, str(tmp_path)]
    completed = subprocess.run(
        [sys.executable, "-m", "geno", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert (
        "Constructor 'Shared' is already defined" in completed.stdout + completed.stderr
    )
    assert "executed" not in completed.stdout
    assert not output.exists()


@pytest.mark.parametrize(
    ("source", "modules"),
    [
        ("import A\nimport A\n" + MAIN, {"A": MODULES["A"]}),
        (
            "import A\nimport ViaA\n" + MAIN,
            {"A": MODULES["A"], "ViaA": MODULES["ViaA"]},
        ),
        (
            "import A as X\nimport B as Y\n" + MAIN,
            {"A": MODULES["A"], "B": MODULES["B"]},
        ),
        (
            "import A\nimport B\n" + MAIN,
            {"A": MODULES["A"], "B": "export type BValue = Other(right: String)\n"},
        ),
        (
            "import A\nimport B\n" + MAIN,
            {
                "A": "type AValue = Shared(left: Int)\nexport type PublicA = VisibleA | OtherA\n",
                "B": "type BValue = Shared(right: String)\nexport type PublicB = VisibleB | OtherB\n",
            },
        ),
    ],
    ids=[
        "repeated-import",
        "diamond-import",
        "aliased-imports",
        "distinct-constructors",
        "private-constructors",
    ],
)
def test_noncolliding_import_scopes_remain_valid(tmp_path, source, modules):
    graph = _project(tmp_path, source, modules)
    TypeChecker().check_program(
        graph.parsed["Main"], modules={name: graph.parsed[name] for name in modules}
    )
    TypeChecker().check_project_graph(graph)


def test_local_constructor_collision_keeps_the_original_diagnostic(tmp_path):
    source = "import A\ntype Local = Shared(other: Bool)\n" + MAIN
    graph = _project(tmp_path, source, {"A": MODULES["A"]})
    with pytest.raises(
        GenoTypeError, match="already defined by type 'AValue'"
    ) as failure:
        TypeChecker().check_project_graph(graph)
    assert failure.value.error_code == ErrorCode.TYPE_DUPLICATE_DEFINITION
    assert Path(failure.value.location.filename) == tmp_path / "Main.geno"
    assert failure.value.location.line == 2


def test_qualified_function_use_does_not_hide_an_unaliased_collision(tmp_path):
    source = (
        "import A\nimport B\nfunc main() -> Unit\n"
        "    let first = A.make()\n"
        "    let second = B.make()\n"
        "end func\n"
    )
    modules = {
        "A": MODULES["A"]
        + (
            "export func make() -> AValue\n"
            "    example () -> Shared(1)\n    return Shared(1)\nend func\n"
        ),
        "B": MODULES["B"]
        + (
            "export func make() -> BValue\n"
            '    example () -> Shared("b")\n    return Shared("b")\nend func\n'
        ),
    }
    graph = _project(tmp_path, source, modules)
    with pytest.raises(GenoTypeError, match="Constructor 'Shared' is already defined"):
        TypeChecker().check_project_graph(graph)
