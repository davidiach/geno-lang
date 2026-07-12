#!/usr/bin/env python3
"""Validate dependency metadata, lockfiles, and update coverage."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence, cast

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

try:
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.utils import canonicalize_name
    from packaging.version import InvalidVersion, Version
except ImportError as exc:  # pragma: no cover - exercised by install layout
    raise SystemExit(
        "ERROR: scripts/validate_dependencies.py requires packaging. "
        'Install development dependencies with `pip install -e ".[dev]"`.'
    ) from exc

try:
    import yaml
except ImportError as exc:  # pragma: no cover - exercised by install layout
    raise SystemExit(
        "ERROR: scripts/validate_dependencies.py requires pyyaml. "
        "Install Geno or use `pip install -e .` before running this gate."
    ) from exc

ROOT = Path(__file__).resolve().parents[1]
PYTHON_REQUIREMENT_SURFACES = (
    ("requirements.txt", ("runtime",), False),
    ("requirements-dev.txt", ("runtime", "dev", "lsp"), False),
    ("requirements.lock", ("runtime",), True),
    ("requirements-dev.lock", ("runtime", "dev", "lsp"), True),
)


@dataclass(frozen=True)
class ParsedRequirement:
    """A dependency requirement and its source location."""

    requirement: Requirement
    source: str

    @property
    def name(self) -> str:
        return canonicalize_name(self.requirement.name)


def _load_toml(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], tomllib.loads(path.read_text(encoding="utf-8")))


def _parse_requirement(raw: str, source: str) -> ParsedRequirement:
    try:
        return ParsedRequirement(Requirement(raw), source)
    except InvalidRequirement as exc:
        raise ValueError(f"{source}: invalid requirement {raw!r}: {exc}") from exc


def _normalize_specifier(specifier: SpecifierSet) -> frozenset[str]:
    return frozenset(str(item) for item in specifier)


def _normalize_marker(requirement: Requirement) -> str | None:
    if requirement.marker is None:
        return None
    return str(requirement.marker)


def _constraint_text(requirement: Requirement) -> str:
    pieces = [str(requirement.specifier) or "<any>"]
    if requirement.marker is not None:
        pieces.append(f"; {requirement.marker}")
    return " ".join(pieces)


def _expected_python_requirements(
    root: Path,
) -> dict[str, dict[str, ParsedRequirement]]:
    pyproject = _load_toml(root / "pyproject.toml")
    project = pyproject.get("project", {})
    optional = project.get("optional-dependencies", {})
    groups = {
        "runtime": project.get("dependencies", []),
        "dev": optional.get("dev", []),
        "lsp": optional.get("lsp", []),
        "llm": optional.get("llm", []),
    }
    expected: dict[str, dict[str, ParsedRequirement]] = {}
    for group, raw_requirements in groups.items():
        group_requirements: dict[str, ParsedRequirement] = {}
        for raw in raw_requirements:
            parsed = _parse_requirement(raw, f"pyproject.toml:{group}:{raw}")
            group_requirements[parsed.name] = parsed
        expected[group] = group_requirements
    return expected


def _merge_expected(
    expected_by_group: dict[str, dict[str, ParsedRequirement]],
    groups: Sequence[str],
) -> dict[str, ParsedRequirement]:
    merged: dict[str, ParsedRequirement] = {}
    for group in groups:
        for name, parsed in expected_by_group[group].items():
            merged[name] = parsed
    return merged


def _strip_requirement_line(raw_line: str) -> str:
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return ""
    if line.startswith("--hash="):
        return ""
    line = line.rstrip("\\").strip()
    if " #" in line:
        line = line.split(" #", 1)[0].strip()
    return line


def _load_requirements_file(
    root: Path,
    path: Path,
    seen: set[Path] | None = None,
) -> dict[str, ParsedRequirement]:
    seen = seen or set()
    resolved = path.resolve()
    if resolved in seen:
        return {}
    seen.add(resolved)

    requirements: dict[str, ParsedRequirement] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        line = _strip_requirement_line(raw_line)
        if not line:
            continue
        source = f"{path.relative_to(root)}:{line_number}"
        if line.startswith("-r ") or line.startswith("--requirement "):
            include = line.split(maxsplit=1)[1]
            requirements.update(
                _load_requirements_file(root, (path.parent / include).resolve(), seen)
            )
            continue
        if line.startswith("-"):
            continue
        parsed = _parse_requirement(line, source)
        requirements[parsed.name] = parsed
    return requirements


def _validate_lockfile_hashes(root: Path, path: Path) -> list[str]:
    """Return errors for lockfile entries without pip content hashes."""
    errors: list[str] = []
    current: ParsedRequirement | None = None
    current_hashes: list[str] = []

    def flush_current() -> None:
        if current is not None and not current_hashes:
            errors.append(
                f"{current.source}: dependency {current.name!r} must include "
                "at least one --hash=sha256:... entry"
            )

    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        source = f"{path.relative_to(root)}:{line_number}"
        if line.startswith("--hash="):
            if current is None:
                errors.append(f"{source}: hash entry has no preceding requirement")
                continue
            hash_text = line.rstrip("\\").strip()
            if not hash_text.startswith("--hash=sha256:"):
                errors.append(
                    f"{source}: dependency {current.name!r} uses unsupported hash "
                    f"entry {hash_text!r}; expected --hash=sha256:..."
                )
                continue
            current_hashes.append(hash_text)
            continue
        if line.startswith("-r ") or line.startswith("--requirement "):
            continue
        if line.startswith("-"):
            continue

        requirement_line = _strip_requirement_line(raw_line)
        if not requirement_line:
            continue
        flush_current()
        current = _parse_requirement(requirement_line, source)
        current_hashes = []

    flush_current()
    return errors


def _load_commented_optional_requirements(
    root: Path,
    path: Path,
    section_title: str,
) -> dict[str, ParsedRequirement]:
    """Load commented-out requirements from a named optional requirements block."""
    requirements: dict[str, ParsedRequirement] = {}
    in_section = False
    wanted_title = section_title.lower()
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        line = raw_line.strip()
        if line.startswith("# Optional:"):
            in_section = wanted_title in line.lower()
            continue
        if not in_section or not line.startswith("#"):
            continue

        commented = line[1:].strip()
        if not commented:
            continue
        parsed = _parse_requirement(
            commented, f"{path.relative_to(root)}:{line_number}"
        )
        requirements[parsed.name] = parsed
    return requirements


def _pinned_versions(specifier: SpecifierSet) -> list[str]:
    return [
        item.version
        for item in specifier
        if item.operator == "==" and not item.version.endswith(".*")
    ]


def _version_satisfies(version_text: str, expected: SpecifierSet) -> bool:
    if not expected:
        return True
    try:
        return Version(version_text) in expected
    except InvalidVersion:
        return False


def _validate_unlocked_requirements(
    path: str,
    expected: dict[str, ParsedRequirement],
    actual: dict[str, ParsedRequirement],
) -> list[str]:
    errors: list[str] = []
    expected_names = set(expected)
    actual_names = set(actual)
    for name in sorted(expected_names - actual_names):
        errors.append(f"{path}: missing dependency {name!r} from pyproject.toml")
    for name in sorted(actual_names - expected_names):
        errors.append(
            f"{path}: unexpected dependency {name!r} not declared in pyproject.toml"
        )
    for name in sorted(expected_names & actual_names):
        expected_req = expected[name].requirement
        actual_req = actual[name].requirement
        if _normalize_specifier(actual_req.specifier) != _normalize_specifier(
            expected_req.specifier
        ):
            errors.append(
                f"{actual[name].source}: dependency {name!r} constraint drift: "
                f"expected {_constraint_text(expected_req)}, "
                f"got {_constraint_text(actual_req)}"
            )
        if _normalize_marker(actual_req) != _normalize_marker(expected_req):
            errors.append(
                f"{actual[name].source}: dependency {name!r} marker drift: "
                f"expected {_normalize_marker(expected_req)!r}, "
                f"got {_normalize_marker(actual_req)!r}"
            )
    return errors


def _validate_locked_requirements(
    path: str,
    expected: dict[str, ParsedRequirement],
    actual: dict[str, ParsedRequirement],
) -> list[str]:
    errors: list[str] = []
    for name in sorted(expected):
        expected_req = expected[name].requirement
        actual_parsed = actual.get(name)
        if actual_parsed is None:
            errors.append(f"{path}: lockfile missing direct dependency {name!r}")
            continue
        actual_req = actual_parsed.requirement
        pins = _pinned_versions(actual_req.specifier)
        if len(pins) != 1:
            errors.append(
                f"{actual_parsed.source}: dependency {name!r} must be exactly pinned "
                f"in lockfile, got {_constraint_text(actual_req)}"
            )
            continue
        if not _version_satisfies(pins[0], expected_req.specifier):
            errors.append(
                f"{actual_parsed.source}: dependency {name!r} lock pin {pins[0]!r} "
                f"does not satisfy pyproject.toml constraint "
                f"{_constraint_text(expected_req)}"
            )
        if _normalize_marker(actual_req) != _normalize_marker(expected_req):
            errors.append(
                f"{actual_parsed.source}: dependency {name!r} marker drift: "
                f"expected {_normalize_marker(expected_req)!r}, "
                f"got {_normalize_marker(actual_req)!r}"
            )
    return errors


def validate_python_requirements(root: Path = ROOT) -> list[str]:
    """Return validation errors for Python metadata and requirement files."""
    expected_by_group = _expected_python_requirements(root)
    errors: list[str] = []
    for path, groups, locked in PYTHON_REQUIREMENT_SURFACES:
        requirement_path = root / path
        if not requirement_path.exists():
            errors.append(f"{path}: missing dependency surface")
            continue
        expected = _merge_expected(expected_by_group, groups)
        actual = _load_requirements_file(root, requirement_path)
        if locked:
            errors.extend(_validate_lockfile_hashes(root, requirement_path))
            errors.extend(_validate_locked_requirements(path, expected, actual))
        else:
            errors.extend(_validate_unlocked_requirements(path, expected, actual))
    errors.extend(
        _validate_unlocked_requirements(
            "requirements.txt optional LLM comments",
            expected_by_group["llm"],
            _load_commented_optional_requirements(
                root, root / "requirements.txt", "LLM API clients"
            ),
        )
    )
    return errors


def _load_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _validate_package_group(
    group: str,
    package_json: dict[str, Any],
    package_lock: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    manifest_deps = package_json.get(group, {})
    lock_root = package_lock.get("packages", {}).get("", {})
    lock_deps = lock_root.get(group, {})
    if manifest_deps != lock_deps:
        manifest_names = set(manifest_deps)
        lock_names = set(lock_deps)
        for name in sorted(manifest_names - lock_names):
            errors.append(f"vscode-geno/package-lock.json: {group} missing {name!r}")
        for name in sorted(lock_names - manifest_names):
            errors.append(f"vscode-geno/package-lock.json: {group} has stale {name!r}")
        for name in sorted(manifest_names & lock_names):
            if manifest_deps[name] != lock_deps[name]:
                errors.append(
                    f"vscode-geno/package-lock.json: {group} {name!r} drift: "
                    f"package.json has {manifest_deps[name]!r}, "
                    f"lockfile has {lock_deps[name]!r}"
                )
    packages = package_lock.get("packages", {})
    for name in sorted(manifest_deps):
        if f"node_modules/{name}" not in packages:
            errors.append(
                f"vscode-geno/package-lock.json: direct dependency {name!r} "
                "has no node_modules lock entry"
            )
    return errors


def _validate_lock_root_group(
    group: str,
    package_json: dict[str, Any],
    package_lock: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    manifest_values = package_json.get(group, {})
    lock_root = package_lock.get("packages", {}).get("", {})
    lock_values = lock_root.get(group, {})
    if manifest_values == lock_values:
        return errors
    manifest_names = set(manifest_values)
    lock_names = set(lock_values)
    for name in sorted(manifest_names - lock_names):
        errors.append(f"vscode-geno/package-lock.json: {group} missing {name!r}")
    for name in sorted(lock_names - manifest_names):
        errors.append(f"vscode-geno/package-lock.json: {group} has stale {name!r}")
    for name in sorted(manifest_names & lock_names):
        if manifest_values[name] != lock_values[name]:
            errors.append(
                f"vscode-geno/package-lock.json: {group} {name!r} drift: "
                f"package.json has {manifest_values[name]!r}, "
                f"lockfile has {lock_values[name]!r}"
            )
    return errors


def validate_vscode_lockfile(root: Path = ROOT) -> list[str]:
    """Return validation errors for the VS Code extension package lock."""
    package_path = root / "vscode-geno" / "package.json"
    lock_path = root / "vscode-geno" / "package-lock.json"
    if not package_path.exists() or not lock_path.exists():
        return ["vscode-geno: package.json and package-lock.json are both required"]

    package_json = _load_json(package_path)
    package_lock = _load_json(lock_path)
    lock_root = package_lock.get("packages", {}).get("", {})
    errors: list[str] = []
    for field in ("name", "version"):
        if package_json.get(field) != package_lock.get(field):
            errors.append(
                f"vscode-geno/package-lock.json: root {field} drift: "
                f"package.json has {package_json.get(field)!r}, "
                f"lockfile has {package_lock.get(field)!r}"
            )
        if package_json.get(field) != lock_root.get(field):
            errors.append(
                f"vscode-geno/package-lock.json: packages[''] {field} drift: "
                f"package.json has {package_json.get(field)!r}, "
                f"lockfile has {lock_root.get(field)!r}"
            )
    if int(package_lock.get("lockfileVersion", 0)) < 2:
        errors.append("vscode-geno/package-lock.json: lockfileVersion must be >= 2")
    errors.extend(_validate_lock_root_group("engines", package_json, package_lock))
    for group in ("dependencies", "devDependencies"):
        errors.extend(_validate_package_group(group, package_json, package_lock))
    return errors


def validate_dependabot_coverage(root: Path = ROOT) -> list[str]:
    """Return validation errors for dependency automation coverage."""
    path = root / ".github" / "dependabot.yml"
    if not path.exists():
        return [".github/dependabot.yml: missing dependency automation config"]
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    updates = data.get("updates", [])
    has_vscode_npm = any(
        update.get("package-ecosystem") == "npm"
        and str(update.get("directory", "")).strip("/") == "vscode-geno"
        for update in updates
    )
    if has_vscode_npm:
        return []
    return [
        ".github/dependabot.yml: missing npm update entry for /vscode-geno "
        "to keep vscode-geno/package-lock.json fresh"
    ]


def _step_runs_strict_twine_check(step: dict[str, Any]) -> bool:
    run = str(step.get("run", ""))
    return "twine check" in run and "--strict" in run and "dist/" in run


def _step_publishes_to_pypi(step: dict[str, Any]) -> bool:
    return "pypa/gh-action-pypi-publish" in str(step.get("uses", ""))


def validate_publish_metadata_gate(root: Path = ROOT) -> list[str]:
    """Return errors when PyPI publish lacks a strict metadata check first."""
    path = root / ".github" / "workflows" / "publish.yml"
    if not path.exists():
        return [".github/workflows/publish.yml: missing PyPI publish workflow"]

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    jobs = data.get("jobs", {})
    publish_jobs = 0
    errors: list[str] = []
    for job_name, job in jobs.items():
        steps = job.get("steps", []) if isinstance(job, dict) else []
        for index, step in enumerate(steps):
            if not isinstance(step, dict) or not _step_publishes_to_pypi(step):
                continue
            publish_jobs += 1
            prior_steps = [s for s in steps[:index] if isinstance(s, dict)]
            if not any(_step_runs_strict_twine_check(s) for s in prior_steps):
                errors.append(
                    ".github/workflows/publish.yml: job "
                    f"{job_name!r} publishes to PyPI before running "
                    "`twine check --strict` on dist artifacts"
                )
    if publish_jobs == 0:
        errors.append(
            ".github/workflows/publish.yml: no pypa/gh-action-pypi-publish step found"
        )
    return errors


def validate_dependency_surfaces(root: Path = ROOT) -> list[str]:
    """Return all dependency drift errors."""
    return [
        *validate_python_requirements(root),
        *validate_vscode_lockfile(root),
        *validate_dependabot_coverage(root),
        *validate_publish_metadata_gate(root),
    ]


def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _run(command: Sequence[str], root: Path) -> int:
    printable = " ".join(command)
    print(printable)
    return subprocess.run(command, cwd=root, check=False).returncode  # noqa: S603


def check_python_lock_installs(root: Path = ROOT) -> list[str]:
    """Install hash locks in a throwaway venv and return errors."""
    with tempfile.TemporaryDirectory(prefix="geno-dependency-lock-") as raw_tmp:
        venv = Path(raw_tmp) / "venv"
        create_code = _run((sys.executable, "-m", "venv", str(venv)), root)
        if create_code != 0:
            return ["python lock install check: failed to create temporary venv"]
        python = _venv_python(venv)
        errors: list[str] = []
        for lockfile in ("requirements.lock", "requirements-dev.lock"):
            code = _run(
                (
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--require-hashes",
                    "-r",
                    str(root / lockfile),
                ),
                root,
            )
            if code != 0:
                errors.append(
                    f"{lockfile}: lockfile install failed with exit code {code}"
                )
        return errors


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate dependency metadata, lockfiles, and update coverage."
    )
    parser.add_argument(
        "--check-installs",
        action="store_true",
        help="Also install Python hash lockfiles in a throwaway virtualenv.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    errors = validate_dependency_surfaces()
    if args.check_installs and not errors:
        errors.extend(check_python_lock_installs())
    if errors:
        print("dependency validation errors:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("dependency surfaces OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
