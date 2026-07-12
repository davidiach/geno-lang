#!/usr/bin/env python3
"""Check CI/DX hardening ratchets against tracked debt budgets."""

from __future__ import annotations

import ast
import io
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence, cast

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
PYTHON_DEBT_TARGETS = ("geno", "benchmark", "experiment", "analysis", "scripts")
EXCLUDED_DEBT_PREFIXES = (("geno", "tests"),)

RUFF_GLOBAL_IGNORE_BUDGET = 24
RUFF_PER_FILE_IGNORE_BUDGET = 24
TYPE_IGNORE_BUDGET = 41
UNTYPED_FUNCTION_BUDGET = 423
# +1 for the compiled http_listen handler boundary (H-03): a compiled Geno
# route handler can raise anything, so a broad `except Exception` that returns
# a generic 500 (and never leaks a traceback to the client) is required.
BROAD_EXCEPTION_HANDLER_BUDGET = 55
WINDOWS_PACKAGE_POLICY_TEST = (
    "geno/tests/test_package_manager.py::TestManifestParse::"
    "test_manifest_filesystem_sensitive_dependency_name_raises"
)


@dataclass(frozen=True)
class BudgetResult:
    """Measured debt budget for one CI/DX ratchet."""

    name: str
    actual: int
    budget: int

    @property
    def passed(self) -> bool:
        return self.actual <= self.budget


def _repo_parts(path: Path, root: Path = ROOT) -> tuple[str, ...]:
    try:
        return path.resolve().relative_to(root).parts
    except ValueError:
        return path.parts


def _is_excluded(path: Path, root: Path = ROOT) -> bool:
    parts = _repo_parts(path, root)
    return any(parts[: len(prefix)] == prefix for prefix in EXCLUDED_DEBT_PREFIXES)


def iter_python_debt_files(root: Path = ROOT) -> list[Path]:
    """Return Python files included in non-test CI/DX debt budgets."""
    files: list[Path] = []
    for target in PYTHON_DEBT_TARGETS:
        target_path = root / target
        if not target_path.exists():
            continue
        for path in sorted(target_path.rglob("*.py")):
            if not _is_excluded(path, root):
                files.append(path)
    return files


def count_type_ignores(files: Sequence[Path]) -> int:
    """Count explicit mypy escape hatches in non-test Python code."""
    total = 0
    for path in files:
        source = path.read_text(encoding="utf-8")
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT and token.string.startswith(
                "# type: ignore"
            ):
                total += 1
    return total


def _function_missing_annotations(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    positional_args = [*node.args.posonlyargs, *node.args.args]
    for index, arg in enumerate(positional_args):
        if index == 0 and arg.arg in {"self", "cls"}:
            continue
        if arg.annotation is None:
            return True

    for arg in node.args.kwonlyargs:
        if arg.annotation is None:
            return True
    if node.args.vararg is not None and node.args.vararg.annotation is None:
        return True
    if node.args.kwarg is not None and node.args.kwarg.annotation is None:
        return True
    return node.returns is None


def count_untyped_functions(files: Sequence[Path]) -> int:
    """Count non-test functions missing parameter or return annotations."""
    total = 0
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        total += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and _function_missing_annotations(node)
        )
    return total


def _is_exception_node(node: ast.AST | None) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Name):
        return node.id == "Exception"
    if isinstance(node, ast.Attribute):
        return node.attr == "Exception"
    if isinstance(node, ast.Tuple):
        return any(_is_exception_node(item) for item in node.elts)
    return False


def count_broad_exception_handlers(files: Sequence[Path]) -> int:
    """Count bare and ``Exception`` handlers in non-test Python code."""
    total = 0
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        total += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler) and _is_exception_node(node.type)
        )
    return total


def _load_pyproject(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], tomllib.loads(path.read_text(encoding="utf-8")))


def _ruff_lint_table(root: Path = ROOT) -> dict[str, Any]:
    pyproject = _load_pyproject(root / "pyproject.toml")
    return cast(dict[str, Any], pyproject["tool"]["ruff"]["lint"])


def count_ruff_global_ignores(root: Path = ROOT) -> int:
    """Count globally ignored Ruff rules."""
    return len(_ruff_lint_table(root).get("ignore", []))


def count_ruff_per_file_ignores(root: Path = ROOT) -> int:
    """Count per-file Ruff ignore entries across all patterns."""
    ignores = _ruff_lint_table(root).get("per-file-ignores", {})
    return sum(len(rules) for rules in ignores.values())


def check_debt_budgets(root: Path = ROOT) -> list[BudgetResult]:
    """Measure all tracked CI/DX debt budgets."""
    files = iter_python_debt_files(root)
    return [
        BudgetResult(
            "ruff-global-ignores",
            count_ruff_global_ignores(root),
            RUFF_GLOBAL_IGNORE_BUDGET,
        ),
        BudgetResult(
            "ruff-per-file-ignores",
            count_ruff_per_file_ignores(root),
            RUFF_PER_FILE_IGNORE_BUDGET,
        ),
        BudgetResult(
            "type-ignore-comments", count_type_ignores(files), TYPE_IGNORE_BUDGET
        ),
        BudgetResult(
            "untyped-functions",
            count_untyped_functions(files),
            UNTYPED_FUNCTION_BUDGET,
        ),
        BudgetResult(
            "broad-exception-handlers",
            count_broad_exception_handlers(files),
            BROAD_EXCEPTION_HANDLER_BUDGET,
        ),
    ]


def check_local_ci_surface(root: Path = ROOT) -> list[str]:
    """Verify optional and release checks are reachable from local CI."""
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from scripts import local_ci

    errors: list[str] = []
    optional_names = [step.name for step in local_ci.build_optional_steps()]
    if optional_names != ["optional-test-collection", "fuzz-property-tests"]:
        errors.append(
            "local_ci optional mode must expose optional collection and fuzz/property tests"
        )

    full_names = [step.name for step in local_ci.build_full_steps()]
    if "ci-dx-ratchets" not in full_names:
        errors.append("local_ci full mode must include the CI/DX ratchet check")

    release_names = [step.name for step in local_ci.build_release_steps()]
    if "release-gate-vscode" not in release_names:
        errors.append("local_ci release mode must include the VS Code release gate")
    if "release-gate-apps" not in release_names:
        errors.append("local_ci release mode must include the example app release gate")
    return errors


def check_workflow_surface(root: Path = ROOT) -> list[str]:
    """Verify platform and optional-runtime coverage stays represented."""
    ci_workflow = (root / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    required_ci_snippets = {
        "windows-latest": "Windows CI runner",
        "geno/tests/test_cli.py": "Windows CLI/path smoke slice",
        "geno/tests/test_sandbox_config.py": "Windows sandbox config smoke slice",
        WINDOWS_PACKAGE_POLICY_TEST: "Windows package path policy smoke slice",
        "optional-test-collection": "hosted optional collection job",
        "fuzz-property-tests": "hosted fuzz/property job",
        "lsp-tests": "hosted optional LSP job",
        "make security PYTHON=python": "hosted security corpus and bounty job",
        "dependency-audit:": "hosted Python dependency audit job",
        "make dependency-audit PYTHON=python": "hosted dependency audit command",
        'pip install "pip-audit==2.10.0"': "exact pip-audit CI install pin",
    }
    errors = [
        f".github/workflows/ci.yml missing {label}: {snippet}"
        for snippet, label in required_ci_snippets.items()
        if snippet not in ci_workflow
    ]
    if "sandbox-regression" not in makefile:
        errors.append("Makefile missing sandbox-regression local target")
    if "dependency-audit:" not in makefile:
        errors.append("Makefile missing dependency-audit local target")
    required_makefile_snippets = {
        "--require-hashes -r requirements.lock": "runtime lockfile audit",
        "--require-hashes -r requirements-dev.lock": "dev lockfile audit",
        "--strict --progress-spinner off": "strict dependency audit mode",
    }
    for snippet, label in required_makefile_snippets.items():
        if snippet not in makefile:
            errors.append(f"Makefile dependency-audit target missing {label}")
    return errors


def check_test_typing_profile_surface(root: Path = ROOT) -> list[str]:
    """Verify tests have a separate staged mypy profile."""
    profile_path = root / "mypy-tests.ini"
    if not profile_path.exists():
        return ["missing staged test typing profile: mypy-tests.ini"]

    profile_text = profile_path.read_text(encoding="utf-8")
    errors: list[str] = []
    required_snippets = {
        "files = geno/tests": "test file scope",
        "check_untyped_defs = True": "test function body checking",
        "follow_imports = skip": "test-friendly import boundary",
    }
    for snippet, label in required_snippets.items():
        if snippet not in profile_text:
            errors.append(f"mypy-tests.ini missing {label}: {snippet}")

    pyproject = _load_pyproject(root / "pyproject.toml")
    mypy_config = cast(dict[str, Any], pyproject["tool"]["mypy"])
    excludes = mypy_config.get("exclude", [])
    if "^geno/tests/" not in excludes:
        errors.append("production mypy profile must keep tests excluded")
    return errors


def collect_errors(root: Path = ROOT) -> list[str]:
    """Collect all ratchet and CI surface failures."""
    errors: list[str] = []
    for result in check_debt_budgets(root):
        if not result.passed:
            errors.append(
                f"{result.name} budget exceeded: {result.actual} > {result.budget}"
            )
    errors.extend(check_local_ci_surface(root))
    errors.extend(check_workflow_surface(root))
    errors.extend(check_test_typing_profile_surface(root))
    return errors


def main() -> int:
    """CLI entrypoint."""
    results = check_debt_budgets(ROOT)
    print("CI/DX ratchet budgets:")
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"  [{status}] {result.name}: {result.actual}/{result.budget}")

    errors = collect_errors(ROOT)
    if errors:
        print("\nCI/DX ratchet errors:")
        for error in errors:
            print(f"  [FAIL] {error}")
        return 1

    print("\nCI/DX ratchet check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
