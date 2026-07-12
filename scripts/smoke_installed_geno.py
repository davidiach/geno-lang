"""Smoke test for an *installed* geno wheel or sdist.

This script is meant to be run from a virtualenv that has ``geno`` installed
from a built artifact (wheel or sdist), **not** from an editable checkout. It
verifies that runtime assets shipped inside the package
(``_js_runtime_support.js``, ``packages.json``, ``std/*.geno``) are reachable,
that the CLI entrypoint works, and that a stdlib-importing program compiles
to JS end-to-end.

Running this against an editable install (``pip install -e .``) will raise
an error, because the imported ``geno`` would be the source checkout rather
than the built artifact under test.

Uses explicit ``raise`` instead of ``assert`` so the checks are not stripped
under ``python -O`` / ``PYTHONOPTIMIZE=1``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import geno
import geno.package_index as package_index
from geno.js_runtime_prelude import JS_RUNTIME_PRELUDE
from geno.target_profile import TargetProfile


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _assert_imported_installed_package() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    source_package = repo_root / "geno"
    geno_file = Path(geno.__file__).resolve()

    if geno_file.is_relative_to(source_package):
        raise RuntimeError(
            f"Imported source checkout instead of installed package: {geno_file}. "
            "Run this script from a venv with geno installed from a built wheel/sdist."
        )


def _assert_runtime_assets_available() -> None:
    _check(
        len(JS_RUNTIME_PRELUDE) > 1000,
        f"JS runtime prelude looks empty or truncated ({len(JS_RUNTIME_PRELUDE)} chars)",
    )
    index_path = Path(package_index.__file__).with_name("packages.json")
    _check(index_path.is_file(), f"package index asset missing: {index_path}")
    try:
        index_data = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"package index asset is unreadable: {index_path}") from exc
    _check(isinstance(index_data, dict), "package index root must be an object")
    _check(index_data.get("version") == 1, "package index version must be 1")
    _check(
        isinstance(index_data.get("packages"), list),
        "package index packages must be a list",
    )
    _check(
        isinstance(package_index.search(""), list),
        "package index search is unavailable",
    )


def _assert_target_metadata_available() -> None:
    browser = TargetProfile.load("browser")
    _check(
        not browser.is_available("fs_read_text"), "browser target permits fs_read_text"
    )
    _check(
        browser.capabilities == {"clock", "http", "print", "random", "regex"},
        f"browser target capabilities drifted: {sorted(browser.capabilities)}",
    )
    node = TargetProfile.load("node-cli")
    _check(not node.is_available("http_listen"), "node-cli target permits http_listen")
    _check(not node.is_available("http_route"), "node-cli target permits http_route")
    _check(
        not node.is_available("http_respond"), "node-cli target permits http_respond"
    )


def _assert_cli_entrypoint_runs() -> None:
    subprocess.run(
        [sys.executable, "-m", "geno", "--version"],
        check=True,
    )


def _assert_stdlib_import_compiles_to_js() -> None:
    source = (
        "import List\n\nfunc main() -> Int\n    return length([1, 2, 3])\nend func\n"
    )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        path = tmp_path / "stdlib_import.geno"
        output = tmp_path / "stdlib_import.js"
        path.write_text(source, encoding="utf-8")

        subprocess.run(  # noqa: S603
            [sys.executable, "-m", "geno", "check", str(path)],
            cwd=tmp_path,
            check=True,
        )
        subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-m",
                "geno",
                "compile",
                str(path),
                "--target",
                "js",
                "-o",
                str(output),
            ],
            cwd=tmp_path,
            check=True,
        )
        compiled = output.read_text(encoding="utf-8")
        _check(
            len(compiled) > 500,
            f"compiled JS looks truncated ({len(compiled)} chars)",
        )
        _check("length" in compiled, "compiled JS missing stdlib builtin 'length'")


def main() -> None:
    _assert_imported_installed_package()
    _assert_runtime_assets_available()
    _assert_target_metadata_available()
    _assert_cli_entrypoint_runs()
    _assert_stdlib_import_compiles_to_js()
    print(f"Installed Geno smoke passed: {geno.__version__}")


if __name__ == "__main__":
    main()
