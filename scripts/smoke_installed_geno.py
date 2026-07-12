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

import subprocess
import sys
import tempfile
from pathlib import Path

import geno
from geno.js_runtime_prelude import JS_RUNTIME_PRELUDE
from geno.package_index import resolve, search
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
    _check(resolve("geno-json") is not None, "package index missing 'geno-json'")
    _check(
        any(pkg.get("name") == "geno-http" for pkg in search("http")),
        "package index search('http') missing 'geno-http'",
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
