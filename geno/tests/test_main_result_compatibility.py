from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from geno.api import RunConfig, run
from geno.compiler import compile_to_python
from geno.js_compiler import compile_to_js
from geno.target_profile import TargetProfile
from geno.tests._script_runner import run_node_code, run_python_code

_NODE = shutil.which("node")


def _source(return_type: str, body: str) -> str:
    indented = "\n".join(f"    {line}" for line in body.splitlines())
    return f"func main() -> {return_type}\n{indented}\nend func\n"


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
    ("mode_args", "source", "expected_stdout"),
    [
        pytest.param((), _source("Unit", "return ()"), "", id="process-unit"),
        pytest.param(
            ("--unsafe",),
            _source("Unit", "return ()"),
            "",
            id="direct-unit",
        ),
        pytest.param((), _source("Int", "return 0"), "=> 0\n", id="process-zero"),
        pytest.param(
            ("--unsafe",),
            _source("Int", "return 0"),
            "=> 0\n",
            id="direct-zero",
        ),
        pytest.param((), _source("Int", "return 2"), "=> 2\n", id="process-two"),
        pytest.param(
            ("--unsafe",),
            _source("Int", "return 2"),
            "=> 2\n",
            id="direct-two",
        ),
        pytest.param(
            (),
            _source("Int", 'print("report-ready")\nreturn 2'),
            "report-ready\n=> 2\n",
            id="process-output-before-result",
        ),
        pytest.param(
            ("--unsafe",),
            _source("Int", 'print("report-ready")\nreturn 2'),
            "report-ready\n=> 2\n",
            id="direct-output-before-result",
        ),
    ],
)
def test_geno_run_displays_main_result_without_changing_status(
    tmp_path: Path,
    mode_args: tuple[str, ...],
    source: str,
    expected_stdout: str,
) -> None:
    result = _run_cli(tmp_path, source, *mode_args)

    assert result.returncode == 0
    assert result.stdout == expected_stdout
    assert result.stderr == ""


def test_geno_run_json_returns_main_value_without_changing_status(
    tmp_path: Path,
) -> None:
    result = _run_cli(
        tmp_path,
        _source("Int", 'print("report-ready")\nreturn 2'),
        "--json",
    )

    assert result.returncode == 0
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["value"] == 2
    assert payload["output"] == "report-ready\n"


@pytest.mark.parametrize(
    "mode_args",
    [pytest.param((), id="process"), pytest.param(("--unsafe",), id="direct")],
)
def test_geno_run_uncaught_runtime_error_is_diagnostic(
    tmp_path: Path, mode_args: tuple[str, ...]
) -> None:
    result = _run_cli(tmp_path, _source("Int", "return 1 / 0"), *mode_args)

    assert result.returncode != 0
    assert "zero" in result.stderr.lower()


def test_embedding_run_returns_value_and_output_without_exiting() -> None:
    result = run(
        _source("Int", 'print("report-ready")\nreturn 2'),
        config=RunConfig(capabilities={"print"}),
    )

    assert result.ok is True
    assert result.value == 2
    assert result.value_raw == 2
    assert result.output == "report-ready\n"


@pytest.mark.parametrize(
    ("source", "expected_stdout"),
    [
        pytest.param(_source("Unit", "return ()"), "", id="unit"),
        pytest.param(_source("Int", "return 2"), "2\n", id="int-two"),
        pytest.param(
            _source("Int", 'print("report-ready")\nreturn 2'),
            "report-ready\n2\n",
            id="output-before-result",
        ),
    ],
)
def test_compiled_python_displays_main_result_without_changing_status(
    source: str, expected_stdout: str
) -> None:
    result = run_python_code(
        compile_to_python(source),
        python_executable=sys.executable,
        args=("--cap", "print"),
    )

    assert result.returncode == 0
    assert result.stdout == expected_stdout
    assert result.stderr == ""


def test_importing_compiled_python_does_not_run_main(tmp_path: Path) -> None:
    compiled = tmp_path / "app.py"
    compiled.write_text(compile_to_python(_source("Int", "return 2")), encoding="utf-8")
    module_spec = importlib.util.spec_from_file_location("compiled_app", compiled)
    assert module_spec is not None
    assert module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    captured = StringIO()

    with redirect_stdout(captured):
        module_spec.loader.exec_module(module)

    assert captured.getvalue() == ""
    assert module.main() == 2


@pytest.mark.skipif(_NODE is None, reason="Node.js is not installed")
@pytest.mark.parametrize(
    ("source", "expected_stdout"),
    [
        pytest.param(_source("Unit", "return ()"), "", id="unit"),
        pytest.param(_source("Int", "return 2"), "2\n", id="int-two"),
        pytest.param(
            _source("Int", 'print("report-ready")\nreturn 2'),
            "report-ready\n2\n",
            id="output-before-result",
        ),
    ],
)
def test_compiled_node_displays_main_result_without_changing_status(
    source: str, expected_stdout: str
) -> None:
    js_code = compile_to_js(source)
    assert isinstance(js_code, str)
    result = run_node_code(js_code, node_executable=_NODE, args=("--cap", "print"))

    assert result.returncode == 0
    assert result.stdout == expected_stdout
    assert result.stderr == ""


@pytest.mark.skipif(_NODE is None, reason="Node.js is not installed")
def test_compiled_node_esm_runs_directly_but_is_inert_when_imported(
    tmp_path: Path,
) -> None:
    js_code = compile_to_js(_source("Int", "return 2"), esm=True)
    assert isinstance(js_code, str)
    compiled = tmp_path / "app.mjs"
    compiled.write_text(js_code, encoding="utf-8")

    direct = subprocess.run(
        [_NODE or "node", str(compiled)], capture_output=True, text=True, timeout=10
    )
    assert direct.returncode == 0
    assert direct.stdout == "2\n"
    assert direct.stderr == ""

    importer = tmp_path / "importer.mjs"
    importer.write_text(
        'import { main } from "./app.mjs";\n'
        'console.log("imported");\n'
        "console.log(main());\n",
        encoding="utf-8",
    )
    imported = subprocess.run(
        [_NODE or "node", str(importer)], capture_output=True, text=True, timeout=10
    )
    assert imported.returncode == 0
    assert imported.stdout == "imported\n2\n"
    assert imported.stderr == ""


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
        assert json.loads(result.stdout)["value"] is None
    else:
        assert result.stdout == ""


def test_compiled_python_uncaught_runtime_error_keeps_traceback() -> None:
    result = run_python_code(
        compile_to_python(_source("Int", "return 1 / 0")),
        python_executable=sys.executable,
    )

    assert result.returncode != 0
    assert "Traceback" in result.stderr
    assert "zero" in result.stderr.lower()


@pytest.mark.skipif(_NODE is None, reason="Node.js is not installed")
def test_compiled_node_uncaught_runtime_error_keeps_diagnostic() -> None:
    js_code = compile_to_js(_source("Int", "return 1 / 0"))
    assert isinstance(js_code, str)
    result = run_node_code(js_code, node_executable=_NODE or "node")

    assert result.returncode != 0
    assert result.stderr
    assert "zero" in result.stderr.lower()


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


@pytest.mark.skipif(_NODE is None, reason="Node.js is not installed")
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

    assert result.returncode == 0
    assert result.stdout == "2\n"
    assert result.stderr == ""


@pytest.mark.skipif(_NODE is None, reason="Node.js is not installed")
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


@pytest.mark.skipif(_NODE is None, reason="Node.js is not installed")
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

    assert result.returncode == 0
    assert result.stdout == "2\n"
    assert result.stderr == ""


@pytest.mark.skipif(_NODE is None, reason="Node.js is not installed")
def test_compiled_node_esm_runs_main_through_package_directory(
    tmp_path: Path,
) -> None:
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

    assert result.returncode == 0
    assert result.stdout == "2\n"
    assert result.stderr == ""
