from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from geno.api import RunConfig, run
from geno.compiler import Compiler, compile_to_python
from geno.dependency_graph import DependencyGraph
from geno.js_compiler import JSCompiler, compile_to_js
from geno.project_graph import ProjectGraph
from geno.target_profile import TargetProfile
from geno.tests._script_runner import run_node_code, run_python_code
from geno.typechecker import TypeChecker

_NODE = shutil.which("node")


def _compile_js_source(source: str) -> str:
    js_code = compile_to_js(source)
    assert isinstance(js_code, str)
    return js_code


def _source(return_type: str, body: str, *, async_main: bool = False) -> str:
    async_prefix = "async " if async_main else ""
    indented = "\n".join(f"    {line}" for line in body.splitlines())
    return f"{async_prefix}func main() -> {return_type}\n{indented}\nend func\n"


def _run_cli(
    tmp_path: Path, source: str, *mode_args: str
) -> subprocess.CompletedProcess[str]:
    app = tmp_path / "App.geno"
    app.write_text(source, encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "geno",
            "run",
            "--no-check-examples",
            *mode_args,
            str(app),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    "mode_args",
    [
        pytest.param((), id="process"),
        pytest.param(("--unsafe",), id="direct"),
    ],
)
@pytest.mark.parametrize(
    ("source", "expected_status"),
    [
        pytest.param(_source("Unit", "return ()"), 0, id="unit"),
        pytest.param(_source("Int", "return 0"), 0, id="int-zero"),
        pytest.param(_source("Int", "return 2"), 2, id="int-two"),
        pytest.param(_source("Int", "return 258"), 2, id="int-normalized"),
        pytest.param(_source("Int", "return -1"), 255, id="int-negative"),
    ],
)
def test_geno_run_uses_main_result_as_exit_status(
    tmp_path: Path,
    mode_args: tuple[str, ...],
    source: str,
    expected_status: int,
) -> None:
    result = _run_cli(tmp_path, source, *mode_args)

    assert result.returncode == expected_status
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize(
    "mode_args",
    [
        pytest.param((), id="process"),
        pytest.param(("--unsafe",), id="direct"),
        pytest.param(("--json",), id="json"),
    ],
)
def test_geno_run_uses_declared_type_not_runtime_int(
    tmp_path: Path, mode_args: tuple[str, ...]
) -> None:
    result = _run_cli(tmp_path, _source("Float", "return 2"), *mode_args)

    assert result.returncode == 0
    assert result.stderr == ""
    if "--json" in mode_args:
        payload = json.loads(result.stdout)
        assert payload["value"] == 2
    else:
        assert result.stdout == "=> 2.0\n"


@pytest.mark.parametrize(
    "mode_args",
    [
        pytest.param((), id="process"),
        pytest.param(("--unsafe",), id="direct"),
        pytest.param(("--json",), id="json"),
    ],
)
def test_geno_run_does_not_treat_imported_main_as_entrypoint(
    tmp_path: Path, mode_args: tuple[str, ...]
) -> None:
    app = tmp_path / "App.geno"
    app.write_text("import Lib\n", encoding="utf-8")
    (tmp_path / "Lib.geno").write_text(_source("Int", "return 2"), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "geno",
            "run",
            "--no-check-examples",
            *mode_args,
            str(app),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    if "--json" in mode_args:
        payload = json.loads(result.stdout)
        assert payload["value"] is None
    else:
        assert result.stdout == ""


@pytest.mark.parametrize(
    "mode_args",
    [
        pytest.param((), id="process"),
        pytest.param(("--unsafe",), id="direct"),
    ],
)
def test_geno_run_preserves_output_before_expected_nonzero_exit(
    tmp_path: Path, mode_args: tuple[str, ...]
) -> None:
    result = _run_cli(
        tmp_path,
        _source("Int", 'print("report-ready")\nreturn 2'),
        *mode_args,
    )

    assert result.returncode == 2
    assert result.stdout == "report-ready\n"
    assert result.stderr == ""
    assert "Traceback" not in result.stdout + result.stderr
    assert "Stack trace" not in result.stdout + result.stderr
    assert "=> 2" not in result.stdout


@pytest.mark.parametrize(
    "mode_args",
    [
        pytest.param((), id="process"),
        pytest.param(("--unsafe",), id="direct"),
    ],
)
def test_geno_run_uncaught_runtime_error_is_diagnostic(
    tmp_path: Path, mode_args: tuple[str, ...]
) -> None:
    result = _run_cli(tmp_path, _source("Int", "return 1 / 0"), *mode_args)

    assert result.returncode != 0
    assert result.stderr
    assert "zero" in result.stderr.lower()
    assert "report-ready" not in result.stdout


def test_geno_run_json_emits_result_before_main_exit_status(tmp_path: Path) -> None:
    result = _run_cli(
        tmp_path,
        _source("Int", 'print("report-ready")\nreturn 2'),
        "--json",
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["value"] == 2
    assert payload["output"] == "report-ready\n"
    assert result.stderr == ""


def test_embedding_run_returns_int_and_output_without_exiting() -> None:
    result = run(
        _source("Int", 'print("report-ready")\nreturn 2'),
        config=RunConfig(capabilities={"print"}),
    )

    assert result.ok is True
    assert result.value == 2
    assert result.value_raw == 2
    assert result.output == "report-ready\n"


@pytest.mark.parametrize(
    ("source", "expected_status"),
    [
        pytest.param(_source("Unit", "return ()"), 0, id="unit"),
        pytest.param(_source("Int", "return 0"), 0, id="int-zero"),
        pytest.param(_source("Int", "return 2"), 2, id="int-two"),
        pytest.param(_source("Int", "return 258"), 2, id="int-normalized"),
        pytest.param(_source("Int", "return -1"), 255, id="int-negative"),
        pytest.param(_source("Int", "return 2", async_main=True), 2, id="async-int"),
    ],
)
def test_compiled_python_uses_main_result_as_exit_status(
    source: str, expected_status: int
) -> None:
    result = run_python_code(
        compile_to_python(source),
        python_executable=sys.executable,
    )

    assert result.returncode == expected_status
    assert result.stdout == ""
    assert result.stderr == ""


def test_compiled_python_resolves_main_return_alias() -> None:
    source = "type Exit = Int\n\n" + _source("Exit", "return 2")

    result = run_python_code(
        compile_to_python(source),
        python_executable=sys.executable,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == ""


def test_compiled_python_preserves_output_before_expected_nonzero_exit() -> None:
    result = run_python_code(
        compile_to_python(_source("Int", 'print("report-ready")\nreturn 2')),
        python_executable=sys.executable,
        args=("--cap", "print"),
    )

    assert result.returncode == 2
    assert result.stdout == "report-ready\n"
    assert result.stderr == ""


def test_compiled_python_import_does_not_run_main_or_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifact = tmp_path / "compiled_app.py"
    artifact.write_text(
        compile_to_python(_source("Int", "return 2")),
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location("compiled_app", artifact)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    assert capsys.readouterr().out == ""
    assert module.main() == 2
    assert capsys.readouterr().out == ""


def test_compiled_python_uncaught_runtime_error_keeps_traceback() -> None:
    result = run_python_code(
        compile_to_python(_source("Int", "return 1 / 0")),
        python_executable=sys.executable,
    )

    assert result.returncode != 0
    assert "Traceback" in result.stderr
    assert "zero" in result.stderr.lower()


@pytest.mark.skipif(_NODE is None, reason="Node.js not installed")
@pytest.mark.parametrize(
    ("source", "expected_status"),
    [
        pytest.param(_source("Unit", "return ()"), 0, id="unit"),
        pytest.param(_source("Int", "return 0"), 0, id="int-zero"),
        pytest.param(_source("Int", "return 2"), 2, id="int-two"),
        pytest.param(_source("Int", "return 258"), 2, id="int-normalized"),
        pytest.param(_source("Int", "return -1"), 255, id="int-negative"),
        pytest.param(_source("Int", "return 2", async_main=True), 2, id="async-int"),
    ],
)
def test_compiled_node_uses_main_result_as_exit_status(
    source: str, expected_status: int
) -> None:
    result = run_node_code(_compile_js_source(source), node_executable=_NODE or "node")

    assert result.returncode == expected_status
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.skipif(_NODE is None, reason="Node.js not installed")
def test_compiled_node_esm_runs_main_only_as_entrypoint(tmp_path: Path) -> None:
    js_code = compile_to_js(_source("Int", "return 2"), esm=True)
    assert isinstance(js_code, str)
    module_path = tmp_path / "app.mjs"
    module_path.write_text(js_code, encoding="utf-8")

    direct = subprocess.run(
        [_NODE or "node", str(module_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )

    host_path = tmp_path / "host.mjs"
    host_path.write_text(
        'import "./app.mjs";\nconsole.log("host continued");\n',
        encoding="utf-8",
    )
    imported = subprocess.run(
        [_NODE or "node", str(host_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert direct.returncode == 2
    assert direct.stdout == ""
    assert direct.stderr == ""
    assert imported.returncode == 0
    assert imported.stdout == "host continued\n"
    assert imported.stderr == ""


def test_browser_targeted_esm_does_not_import_node_runtime() -> None:
    source = _source("Int", "return 2")
    profile = TargetProfile.load("browser")
    esm_result = compile_to_js(
        source,
        esm=True,
        source_map=True,
        target_profile=profile,
    )
    script_result = compile_to_js(source, source_map=True, target_profile=profile)

    assert isinstance(esm_result, tuple)
    assert isinstance(script_result, tuple)
    js_code, esm_source_map = esm_result
    _, script_source_map = script_result
    assert 'from "node:' not in js_code
    assert js_code.startswith('"use strict";\n')
    assert "const _main_result = main();" in js_code
    assert (
        json.loads(esm_source_map)["mappings"]
        == json.loads(script_source_map)["mappings"]
    )


@pytest.mark.skipif(_NODE is None, reason="Node.js not installed")
@pytest.mark.parametrize(
    (
        "entry_alias",
        "main_body",
        "later_alias",
        "expected_status",
        "expected_stdout",
    ),
    [
        pytest.param(
            "String",
            'return "legacy"',
            "Int",
            0,
            "legacy\n",
            id="entry-string-later-int",
        ),
        pytest.param(
            "Int",
            "return 2",
            "String",
            2,
            "",
            id="entry-int-later-string",
        ),
    ],
)
def test_compiled_node_resolves_main_alias_in_entrypoint_module(
    tmp_path: Path,
    entry_alias: str,
    main_body: str,
    later_alias: str,
    expected_status: int,
    expected_stdout: str,
) -> None:
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "Main"\nfiles = ["Main", "Zed"]\n',
        encoding="utf-8",
    )
    (tmp_path / "Main.geno").write_text(
        f"type Exit = {entry_alias}\n\n" + _source("Exit", main_body),
        encoding="utf-8",
    )
    (tmp_path / "Zed.geno").write_text(
        f"type Exit = {later_alias}\n",
        encoding="utf-8",
    )
    graph = DependencyGraph.resolve(ProjectGraph.discover(tmp_path))
    TypeChecker().check_project_graph(graph)

    result = run_node_code(
        JSCompiler().compile_project(graph),
        node_executable=_NODE or "node",
    )

    assert result.returncode == expected_status
    assert result.stdout == expected_stdout
    assert result.stderr == ""


@pytest.mark.skipif(_NODE is None, reason="Node.js not installed")
@pytest.mark.parametrize(
    ("imports", "main_body", "expected_status", "expected_stdout"),
    [
        pytest.param(
            "import Zed\nimport Shared",
            "return 2",
            2,
            "",
            id="last-source-import-is-int",
        ),
        pytest.param(
            "import Shared\nimport Zed",
            'return "legacy"',
            0,
            "legacy\n",
            id="last-source-import-is-string",
        ),
    ],
)
def test_compiled_node_resolves_main_alias_in_source_import_order(
    tmp_path: Path,
    imports: str,
    main_body: str,
    expected_status: int,
    expected_stdout: str,
) -> None:
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "Main"\nfiles = ["Main", "Zed", "Shared"]\n',
        encoding="utf-8",
    )
    (tmp_path / "Main.geno").write_text(
        imports + "\n\n" + _source("Exit", main_body),
        encoding="utf-8",
    )
    (tmp_path / "Shared.geno").write_text(
        "export type Exit = Int\n",
        encoding="utf-8",
    )
    (tmp_path / "Zed.geno").write_text(
        "export type Exit = String\n",
        encoding="utf-8",
    )
    graph = DependencyGraph.resolve(ProjectGraph.discover(tmp_path))
    TypeChecker().check_project_graph(graph)

    result = run_node_code(
        JSCompiler().compile_project(graph),
        node_executable=_NODE or "node",
    )

    assert result.returncode == expected_status
    assert result.stdout == expected_stdout
    assert result.stderr == ""


def _write_imported_alias_scope_project(tmp_path: Path) -> None:
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "Main"\nfiles = ["Main", "Shared"]\n',
        encoding="utf-8",
    )
    (tmp_path / "Shared.geno").write_text(
        "type X = Int\ntype Exit = X\n",
        encoding="utf-8",
    )
    (tmp_path / "Main.geno").write_text(
        "import Shared\n\ntype X = String\n\n" + _source("Exit", "return 2"),
        encoding="utf-8",
    )


def test_geno_run_json_preserves_imported_alias_scope_for_exit_status(
    tmp_path: Path,
) -> None:
    _write_imported_alias_scope_project(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "geno",
            "run",
            "--no-check-examples",
            "--json",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 2
    assert result.stderr == ""
    assert json.loads(result.stdout)["value"] == 2


def test_embedding_preserves_imported_alias_scope_for_exit_metadata() -> None:
    result = run(
        "import Shared\n\ntype X = String\n\n" + _source("Exit", "return 2"),
        RunConfig(modules={"Shared": "type X = Int\ntype Exit = X\n"}),
    )

    assert result.ok is True
    assert result.value_raw == 2
    assert result.__dict__["_main_returns_int"] is True


def test_unchecked_project_compilers_preserve_imported_alias_scope(
    tmp_path: Path,
) -> None:
    _write_imported_alias_scope_project(tmp_path)
    graph = DependencyGraph.resolve(ProjectGraph.discover(tmp_path))

    py_result = run_python_code(
        Compiler().compile_project(graph),
        python_executable=sys.executable,
    )

    assert py_result.returncode == 2
    assert py_result.stdout == ""
    assert py_result.stderr == ""

    if _NODE is not None:
        js_result = run_node_code(
            JSCompiler().compile_project(graph),
            node_executable=_NODE,
        )
        assert js_result.returncode == 2
        assert js_result.stdout == ""
        assert js_result.stderr == ""


def test_process_mode_displays_large_legacy_result_outside_print_limit(
    tmp_path: Path,
) -> None:
    result = _run_cli(
        tmp_path,
        _source("String", 'return repeat_string("x", 100001)'),
    )

    assert result.returncode == 0
    assert result.stdout == "=> " + ("x" * 100001) + "\n"
    assert result.stderr == ""


@pytest.mark.skipif(_NODE is None, reason="Node.js not installed")
def test_compiled_node_drains_large_output_before_expected_nonzero_exit() -> None:
    result = run_node_code(
        _compile_js_source(
            _source(
                "Int",
                'print(repeat_string("x", 70000))\nreturn 2',
            )
        ),
        node_executable=_NODE or "node",
        args=("--cap", "print"),
    )

    assert result.returncode == 2
    assert result.stdout == ("x" * 70000) + "\n"
    assert result.stderr == ""


@pytest.mark.skipif(_NODE is None, reason="Node.js not installed")
def test_compiled_node_uncaught_runtime_error_keeps_diagnostic() -> None:
    result = run_node_code(
        _compile_js_source(_source("Int", "return 1 / 0")),
        node_executable=_NODE or "node",
    )

    assert result.returncode != 0
    assert result.stderr
    assert "zero" in result.stderr.lower()


@pytest.mark.parametrize(
    ("return_type", "body", "expected"),
    [
        ("Float", "return 2", "2.0\n"),
        ("String", 'return "legacy"', "legacy\n"),
    ],
)
def test_non_int_main_keeps_legacy_display_behavior(
    return_type: str, body: str, expected: str
) -> None:
    py_result = run_python_code(
        compile_to_python(_source(return_type, body)),
        python_executable=sys.executable,
    )
    assert py_result.returncode == 0
    assert py_result.stdout == expected

    if _NODE is not None:
        js_result = run_node_code(
            _compile_js_source(_source(return_type, body)),
            node_executable=_NODE,
        )
        assert js_result.returncode == 0
        assert js_result.stdout == expected


@pytest.mark.parametrize(
    "mode_args",
    [
        pytest.param((), id="process"),
        pytest.param(("--unsafe",), id="direct"),
        pytest.param(("--json",), id="json"),
    ],
)
@pytest.mark.parametrize(
    ("return_type", "body", "expected_status", "expected_value"),
    [
        pytest.param(
            "Unit",
            'print("async-report")\nreturn ()',
            0,
            {"_tuple": []},
            id="unit",
        ),
        pytest.param("Int", 'print("async-report")\nreturn 2', 2, 2, id="int"),
    ],
)
def test_geno_run_awaits_async_main(
    tmp_path: Path,
    mode_args: tuple[str, ...],
    return_type: str,
    body: str,
    expected_status: int,
    expected_value: object,
) -> None:
    result = _run_cli(
        tmp_path,
        _source(return_type, body, async_main=True),
        *mode_args,
    )

    assert result.returncode == expected_status
    assert result.stderr == ""
    if "--json" in mode_args:
        payload = json.loads(result.stdout)
        assert payload["value"] == expected_value
        assert payload["output"] == "async-report\n"
    else:
        assert result.stdout == "async-report\n"


def test_embedding_run_awaits_async_main_without_exiting() -> None:
    result = run(
        _source(
            "Int",
            'print("async-report")\nreturn 2',
            async_main=True,
        ),
        config=RunConfig(capabilities={"print"}),
    )

    assert result.ok is True
    assert result.value == 2
    assert result.value_raw == 2
    assert result.output == "async-report\n"


def _nested_generic_alias_source() -> str:
    return (
        "type Outer[T] = Inner[Identity[T]]\ntype Inner[T] = T\ntype Identity[T] = T\n\n"
        + _source("Outer[Int]", "return 2")
    )


@pytest.mark.parametrize(
    "mode_args",
    [
        pytest.param((), id="process"),
        pytest.param(("--unsafe",), id="direct"),
        pytest.param(("--json",), id="json"),
    ],
)
def test_geno_run_resolves_nested_generic_main_alias(
    tmp_path: Path, mode_args: tuple[str, ...]
) -> None:
    result = _run_cli(tmp_path, _nested_generic_alias_source(), *mode_args)

    assert result.returncode == 2
    assert result.stderr == ""
    if "--json" in mode_args:
        assert json.loads(result.stdout)["value"] == 2
    else:
        assert result.stdout == ""


def test_compiled_backends_resolve_nested_generic_main_alias() -> None:
    source = _nested_generic_alias_source()
    py_result = run_python_code(
        compile_to_python(source),
        python_executable=sys.executable,
    )

    assert py_result.returncode == 2
    assert py_result.stdout == ""
    assert py_result.stderr == ""

    if _NODE is not None:
        js_result = run_node_code(
            _compile_js_source(source),
            node_executable=_NODE,
        )
        assert js_result.returncode == 2
        assert js_result.stdout == ""
        assert js_result.stderr == ""


def _self_named_generic_int_alias_source() -> str:
    return "type Exit[Int] = Int\n\n" + _source("Exit[Int]", "return 2")


@pytest.mark.parametrize(
    "mode_args",
    [
        pytest.param((), id="process"),
        pytest.param(("--unsafe",), id="direct"),
        pytest.param(("--json",), id="json"),
    ],
)
def test_geno_run_resolves_self_named_generic_int_alias(
    tmp_path: Path, mode_args: tuple[str, ...]
) -> None:
    result = _run_cli(tmp_path, _self_named_generic_int_alias_source(), *mode_args)

    assert result.returncode == 2
    assert result.stderr == ""
    if "--json" in mode_args:
        assert json.loads(result.stdout)["value"] == 2
    else:
        assert result.stdout == ""


def test_embedding_and_compiled_backends_resolve_self_named_generic_int_alias() -> None:
    source = _self_named_generic_int_alias_source()
    embedded = run(source)

    assert embedded.ok is True
    assert embedded.value_raw == 2
    assert embedded.__dict__["_main_returns_int"] is True

    py_result = run_python_code(
        compile_to_python(source),
        python_executable=sys.executable,
    )

    assert py_result.returncode == 2
    assert py_result.stdout == ""
    assert py_result.stderr == ""

    if _NODE is not None:
        js_result = run_node_code(
            _compile_js_source(source),
            node_executable=_NODE,
        )
        assert js_result.returncode == 2
        assert js_result.stdout == ""
        assert js_result.stderr == ""


@pytest.mark.parametrize(
    "mode_args",
    [
        pytest.param((), id="process"),
        pytest.param(("--unsafe",), id="direct"),
        pytest.param(("--json",), id="json"),
    ],
)
def test_builtin_float_name_takes_precedence_over_alias_for_cli_exit(
    tmp_path: Path, mode_args: tuple[str, ...]
) -> None:
    source = "type Float = Int\n\n" + _source("Float", "return 2")

    result = _run_cli(tmp_path, source, *mode_args)

    assert result.returncode == 0
    assert result.stderr == ""
    if "--json" in mode_args:
        value = json.loads(result.stdout)["value"]
        assert isinstance(value, float)
        assert value == 2.0
    else:
        assert result.stdout == "=> 2.0\n"


def test_builtin_names_take_precedence_over_aliases_in_compiled_backends() -> None:
    float_source = "type Float = Int\n\n" + _source("Float", "return 2")
    py_float = run_python_code(
        compile_to_python(float_source),
        python_executable=sys.executable,
    )

    assert py_float.returncode == 0
    assert py_float.stdout == "2.0\n"
    assert py_float.stderr == ""

    int_source = "type Int = String\n\n" + _source("Int", "return 2")
    py_int = run_python_code(
        compile_to_python(int_source),
        python_executable=sys.executable,
    )
    assert py_int.returncode == 2
    assert py_int.stdout == ""
    assert py_int.stderr == ""

    if _NODE is not None:
        js_float = run_node_code(
            _compile_js_source(float_source),
            node_executable=_NODE,
        )
        assert js_float.returncode == 0
        assert js_float.stdout == "2.0\n"
        assert js_float.stderr == ""

        js_int = run_node_code(
            _compile_js_source(int_source),
            node_executable=_NODE,
        )
        assert js_int.returncode == 2
        assert js_int.stdout == ""
        assert js_int.stderr == ""


def test_sync_main_returning_async_value_is_not_awaited(tmp_path: Path) -> None:
    source = (
        "async func deferred() -> Int\n"
        "    return 2\n"
        "end func\n\n"
        "func main() -> Async[Int]\n"
        "    return deferred()\n"
        "end func\n"
    )

    embedded = run(source)
    direct = _run_cli(tmp_path, source, "--unsafe")

    assert embedded.ok is True
    assert str(embedded.value_raw) == "<async deferred>"
    assert embedded.value == "<async deferred>"
    assert embedded.output == ""
    assert direct.returncode == 0
    assert direct.stdout == "=> <async deferred>\n"
    assert direct.stderr == ""


def test_json_exit_status_reuses_executed_program_metadata(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from geno.api import RunResult
    from geno.cli import run as run_module

    executed = RunResult(
        ok=True,
        value=2,
        value_raw=2,
    )
    executed.__dict__["_main_returns_int"] = True
    monkeypatch.setattr("geno.api.run_path", lambda _filename, _config: executed)

    def unexpected_resolution(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("JSON mode must not resolve the project after execution")

    monkeypatch.setattr(run_module, "_resolve_run_program", unexpected_resolution)

    status = run_module.run_file("removed-after-run.geno", json_output=True)

    assert status == 2
    assert json.loads(capsys.readouterr().out)["value"] == 2


@pytest.mark.skipif(_NODE is None, reason="Node.js not installed")
def test_compiled_node_esm_runs_main_through_symlink(tmp_path: Path) -> None:
    js_code = compile_to_js(_source("Int", "return 2"), esm=True)
    assert isinstance(js_code, str)
    module_path = tmp_path / "app.mjs"
    module_path.write_text(js_code, encoding="utf-8")
    link_path = tmp_path / "app-link.mjs"
    try:
        link_path.symlink_to(module_path.name)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    result = subprocess.run(
        [_NODE or "node", str(link_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.skipif(_NODE is None, reason="Node.js not installed")
def test_compiled_node_esm_eval_import_ignores_matching_host_argv(
    tmp_path: Path,
) -> None:
    js_code = compile_to_js(_source("Int", "return 2"), esm=True)
    assert isinstance(js_code, str)
    (tmp_path / "app.mjs").write_text(js_code, encoding="utf-8")

    result = subprocess.run(
        [
            _NODE or "node",
            "--input-type=module",
            "--eval",
            'import("./app.mjs").then(() => console.log("host continued"))',
            "./app.mjs",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert result.stdout == "host continued\n"
    assert result.stderr == ""


@pytest.mark.skipif(_NODE is None, reason="Node.js not installed")
@pytest.mark.parametrize(
    "runtime_flag",
    ["--enable-source-maps", "--preserve-symlinks"],
)
def test_compiled_node_esm_runs_main_with_non_eval_runtime_flag(
    tmp_path: Path, runtime_flag: str
) -> None:
    js_code = compile_to_js(_source("Int", "return 2"), esm=True)
    assert isinstance(js_code, str)
    module_path = tmp_path / "app.mjs"
    module_path.write_text(js_code, encoding="utf-8")

    result = subprocess.run(
        [_NODE or "node", runtime_flag, str(module_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.skipif(_NODE is None, reason="Node.js not installed")
def test_compiled_node_resolves_zero_arity_tuple_alias_to_int() -> None:
    source = "type Tuple = Int\n\n" + _source("Tuple", "return 2")

    result = run_node_code(
        _compile_js_source(source),
        node_executable=_NODE or "node",
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize(
    "mode_args",
    [
        pytest.param((), id="process"),
        pytest.param(("--unsafe",), id="direct"),
        pytest.param(("--json",), id="json"),
    ],
)
def test_parameterized_tuple_keeps_builtin_precedence_over_alias(
    tmp_path: Path, mode_args: tuple[str, ...]
) -> None:
    source = "type Tuple = Int\n\n" + _source("(Int, Int)", "return (2, 3)")

    result = _run_cli(tmp_path, source, *mode_args)

    assert result.returncode == 0
    assert result.stderr == ""
    if "--json" in mode_args:
        assert json.loads(result.stdout)["value"] == {"_tuple": [2, 3]}
    else:
        assert result.stdout == "=> (2, 3)\n"


@pytest.mark.skipif(_NODE is None, reason="Node.js not installed")
def test_compiled_node_esm_runs_main_through_package_directory(tmp_path: Path) -> None:
    package_path = tmp_path / "package"
    package_path.mkdir()
    js_code = compile_to_js(_source("Int", "return 2"), esm=True)
    assert isinstance(js_code, str)
    (package_path / "app.mjs").write_text(js_code, encoding="utf-8")
    (package_path / "package.json").write_text(
        json.dumps({"type": "module", "main": "app.mjs"}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [_NODE or "node", str(package_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == ""


def test_embedding_run_result_dataclass_schema_is_unchanged() -> None:
    from dataclasses import asdict

    result = run(_source("Int", "return 2"))

    assert result.ok
    assert result.__dict__["_main_returns_int"] is True
    assert "_main_returns_int" not in asdict(result)
