#!/usr/bin/env python3
"""Validate dependency metadata, lockfiles, and update coverage."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence, cast

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
_PIP_EXECUTABLE_RE = re.compile(
    r"pip(?:\d+(?:\.\d+)*)?(?:\.exe)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedRequirement:
    """A dependency requirement and its source location."""

    requirement: Requirement
    source: str

    @property
    def name(self) -> str:
        return cast(str, canonicalize_name(self.requirement.name))


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
    raw_updates = data.get("updates", [])
    updates = [update for update in raw_updates if isinstance(update, dict)]
    errors: list[str] = []
    if not any(
        update.get("package-ecosystem") == "npm"
        and str(update.get("directory", "")).strip("/") == "vscode-geno"
        for update in updates
    ):
        errors.append(
            ".github/dependabot.yml: missing npm update entry for /vscode-geno "
            "to keep vscode-geno/package-lock.json fresh"
        )
    if not any(
        update.get("package-ecosystem") == "docker"
        and str(update.get("directory", "")).strip("/") == ""
        for update in updates
    ):
        errors.append(
            ".github/dependabot.yml: missing Docker update entry for / "
            "to keep digest-pinned base images fresh"
        )
    return errors


def _step_runs_strict_twine_check(step: dict[str, Any]) -> bool:
    run = str(step.get("run", ""))
    return "twine check" in run and "--strict" in run and "dist/" in run


def _step_uses_action(step: dict[str, Any], action: str) -> bool:
    uses = str(step.get("uses", ""))
    return uses.partition("@")[0] == action


def _step_uses_pinned_action(step: dict[str, Any], action: str) -> bool:
    uses = str(step.get("uses", ""))
    name, separator, ref = uses.partition("@")
    return (
        name == action
        and separator == "@"
        and len(ref) == 40
        and all(char in "0123456789abcdefABCDEF" for char in ref)
    )


def _step_publishes_to_pypi(step: dict[str, Any]) -> bool:
    return _step_uses_action(step, "pypa/gh-action-pypi-publish")


def _job_needs(job: dict[str, Any]) -> tuple[str, ...]:
    needs = job.get("needs", ())
    if isinstance(needs, str):
        return (needs,)
    if isinstance(needs, list):
        return tuple(str(item) for item in needs)
    return ()


def _permissions_grant_oidc_write(permissions: Any) -> bool:
    return permissions == "write-all" or (
        isinstance(permissions, dict) and permissions.get("id-token") == "write"
    )


def _job_has_oidc_write(job: dict[str, Any]) -> bool:
    return _permissions_grant_oidc_write(job.get("permissions", {}))


def _job_environment_name(job: dict[str, Any]) -> str:
    environment = job.get("environment", "")
    if isinstance(environment, dict):
        environment = environment.get("name", "")
    return str(environment)


def _step_installs_release_lock(step: dict[str, Any]) -> bool:
    return _step_has_exact_pip_install(
        step,
        ("--require-hashes", "-r", "requirements-release.lock"),
    )


def _step_runs_release_gate(step: dict[str, Any]) -> bool:
    return "make release-check" in str(step.get("run", ""))


def _step_installs_dev_lock(step: dict[str, Any]) -> bool:
    return _step_has_exact_pip_install(
        step,
        ("--require-hashes", "-r", "requirements-dev.lock"),
    )


def _step_installs_project_without_dependencies(step: dict[str, Any]) -> bool:
    return _step_has_exact_pip_install(
        step,
        ("--no-deps", "--no-build-isolation", "-e", "."),
    )


def _remove_shell_line_continuations(run: str) -> str:
    normalized: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0

    while index < len(run):
        char = run[index]
        if escaped:
            normalized.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            if run.startswith("\r\n", index + 1):
                index += 3
                continue
            if index + 1 < len(run) and run[index + 1] == "\n":
                index += 2
                continue
            normalized.append(char)
            escaped = True
            index += 1
            continue
        if (
            char == "\\"
            and quote == "'"
            and (
                run.startswith("\r\n", index + 1)
                or (index + 1 < len(run) and run[index + 1] == "\n")
            )
        ):
            raise ValueError("backslash-newline inside single quotes is not allowed")
        if char in ("'", '"'):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
        normalized.append(char)
        index += 1

    return "".join(normalized)


def _split_unquoted_shell_controls(raw_line: str) -> tuple[str, ...]:
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    at_word_start = True

    for char in raw_line:
        if escaped:
            current.append(char)
            escaped = False
            at_word_start = False
            continue
        if char == "\\" and quote != "'":
            current.append(char)
            escaped = True
            continue
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
            current.append(char)
            at_word_start = False
            continue
        if char == "#" and at_word_start:
            break
        if char in "()":
            raise ValueError("unquoted shell parentheses are not allowed")
        if char in ";&|":
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current.clear()
            at_word_start = True
            continue
        current.append(char)
        at_word_start = char.isspace()

    segment = "".join(current).strip()
    if segment:
        segments.append(segment)
    return tuple(segments)


def _shell_command_groups(run: str) -> tuple[tuple[str, ...], ...]:
    normalized = _remove_shell_line_continuations(run)
    if "`" in normalized:
        raise ValueError("backtick command substitution is not allowed")
    if any(marker in normalized for marker in ("$(", "<(", ">(")) or re.search(
        r"\$\{[ \t\r\n|]", normalized
    ):
        raise ValueError("shell command or process substitution is not allowed")
    groups: list[tuple[str, ...]] = []
    for raw_line in normalized.splitlines():
        for segment in _split_unquoted_shell_controls(raw_line):
            lexer = shlex.shlex(segment, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = ""
            tokens = tuple(lexer)
            if tokens:
                groups.append(tokens)
    return tuple(groups)


def _is_pip_executable(token: str) -> bool:
    basename = re.split(r"[\\/]", token)[-1]
    return _PIP_EXECUTABLE_RE.fullmatch(basename) is not None


def _is_python_executable(token: str) -> bool:
    basename = re.split(r"[\\/]", token)[-1]
    return (
        re.fullmatch(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?", basename, re.IGNORECASE)
        is not None
    )


def _pip_install_arguments(command: tuple[str, ...]) -> tuple[str, ...] | None:
    if not command:
        return None
    if _is_pip_executable(command[0]):
        pip_index = 0
    elif (
        len(command) >= 3
        and command[1] == "-m"
        and _is_python_executable(command[0])
        and _is_pip_executable(command[2])
    ):
        pip_index = 2
    else:
        return None
    invocation_tokens = command[pip_index:]
    if len(invocation_tokens) < 2 or invocation_tokens[1] != "install":
        return None
    return invocation_tokens[2:]


def _step_has_exact_pip_install(
    step: dict[str, Any],
    expected_arguments: tuple[str, ...],
) -> bool:
    try:
        command_groups = _shell_command_groups(str(step.get("run", "")))
    except ValueError:
        return False
    return any(
        _pip_install_arguments(command) == expected_arguments
        for command in command_groups
    )


def _unsafe_release_gate_install_lines(step: dict[str, Any]) -> tuple[str, ...]:
    run = str(step.get("run", ""))
    try:
        command_groups = _shell_command_groups(run)
    except ValueError as exc:
        return (f"unparseable release-check shell command: {exc}",)

    unsafe: list[str] = []
    for command in command_groups:
        pip_index = next(
            (index for index, token in enumerate(command) if _is_pip_executable(token)),
            None,
        )
        if pip_index is None:
            continue
        invocation_tokens = command[pip_index:]
        try:
            install_offset = invocation_tokens.index("install", 1)
        except ValueError:
            continue
        arguments = invocation_tokens[install_offset + 1 :]
        allowed_arguments = (
            (
                "--require-hashes",
                "-r",
                "requirements-dev.lock",
            ),
            (
                "--require-hashes",
                "-r",
                "requirements-release.lock",
            ),
            (
                "--no-deps",
                "--no-build-isolation",
                "-e",
                ".",
            ),
        )
        if arguments not in allowed_arguments:
            unsafe.append(" ".join(invocation_tokens))
    return tuple(unsafe)


def _step_builds_without_isolation(step: dict[str, Any]) -> bool:
    run = str(step.get("run", ""))
    return "python -m build" in run and "--no-isolation" in run


def _artifact_options(step: dict[str, Any]) -> dict[str, Any]:
    options = step.get("with", {})
    return options if isinstance(options, dict) else {}


def _artifact_name(step: dict[str, Any]) -> str:
    return str(_artifact_options(step).get("name", ""))


def _artifact_path(step: dict[str, Any]) -> str:
    return str(_artifact_options(step).get("path", ""))


def _artifact_provenance_overrides(step: dict[str, Any]) -> tuple[str, ...]:
    options = _artifact_options(step)
    forbidden = ("github-token", "repository", "run-id")
    return tuple(name for name in forbidden if name in options)


def _validate_release_lock(root: Path) -> list[str]:
    lock_path = root / "requirements-release.lock"
    if not lock_path.exists():
        return ["requirements-release.lock: missing hash-locked release dependencies"]

    errors = _validate_lockfile_hashes(root, lock_path)
    requirements = _load_requirements_file(root, lock_path)
    release_tools = ("build", "setuptools", "twine", "wheel")
    for package in release_tools:
        parsed = requirements.get(package)
        specs = list(parsed.requirement.specifier) if parsed is not None else []
        if (
            parsed is None
            or len(specs) != 1
            or specs[0].operator != "=="
            or specs[0].version.endswith(".*")
        ):
            errors.append(
                "requirements-release.lock: release tool "
                f"{package!r} must have one exact pin"
            )

    input_path = root / "requirements-release.in"
    if not input_path.exists():
        errors.append(
            "requirements-release.in: missing direct release-tool requirements"
        )
        return errors

    direct_requirements = _load_requirements_file(root, input_path)
    for package in release_tools:
        if package not in direct_requirements:
            errors.append(
                "requirements-release.in: release tool "
                f"{package!r} must be directly pinned"
            )

    for name, direct in direct_requirements.items():
        direct_specs = list(direct.requirement.specifier)
        if (
            len(direct_specs) != 1
            or direct_specs[0].operator != "=="
            or direct_specs[0].version.endswith(".*")
        ):
            errors.append(
                f"{direct.source}: direct release dependency {name!r} "
                "must have one exact pin"
            )
            continue

        locked = requirements.get(name)
        if locked is None:
            errors.append(
                f"requirements-release.lock: missing direct release dependency {name!r}"
            )
            continue
        if _normalize_specifier(locked.requirement.specifier) != _normalize_specifier(
            direct.requirement.specifier
        ) or _normalize_marker(locked.requirement) != _normalize_marker(
            direct.requirement
        ):
            errors.append(
                f"requirements-release.lock: direct pin drift for {name!r}: "
                f"requirements-release.in has {_constraint_text(direct.requirement)!r}, "
                f"lock has {_constraint_text(locked.requirement)!r}"
            )
    return errors


def validate_publish_metadata_gate(root: Path = ROOT) -> list[str]:
    """Return errors when the PyPI OIDC boundary or artifact gate is unsafe."""
    errors = _validate_release_lock(root)
    path = root / ".github" / "workflows" / "publish.yml"
    if not path.exists():
        return [*errors, ".github/workflows/publish.yml: missing PyPI publish workflow"]

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if _permissions_grant_oidc_write(data.get("permissions", {})):
        errors.append(
            ".github/workflows/publish.yml: workflow-level permissions must not "
            "grant id-token: write or write-all"
        )
    jobs = data.get("jobs", {})
    if not isinstance(jobs, dict):
        return [*errors, ".github/workflows/publish.yml: jobs must be a mapping"]

    release_gate_jobs: set[str] = set()
    for job_name, raw_job in jobs.items():
        if not isinstance(raw_job, dict):
            continue
        steps = [step for step in raw_job.get("steps", []) if isinstance(step, dict)]
        for gate_index, gate_step in enumerate(steps):
            if not _step_runs_release_gate(gate_step):
                continue
            release_gate_jobs.add(str(job_name))
            prior_steps = steps[:gate_index]
            if not any(_step_installs_dev_lock(step) for step in prior_steps):
                errors.append(
                    ".github/workflows/publish.yml: release-check dependencies must "
                    "be installed from requirements-dev.lock with --require-hashes"
                )
            if not any(_step_installs_release_lock(step) for step in prior_steps):
                errors.append(
                    ".github/workflows/publish.yml: release-check build dependencies "
                    "must be installed from requirements-release.lock with "
                    "--require-hashes"
                )
            if not any(
                _step_installs_project_without_dependencies(step)
                for step in prior_steps
            ):
                errors.append(
                    ".github/workflows/publish.yml: release-check must install the "
                    "project editable with --no-deps and --no-build-isolation"
                )
            unsafe_installs = tuple(
                line
                for step in prior_steps
                for line in _unsafe_release_gate_install_lines(step)
            )
            if unsafe_installs:
                errors.append(
                    ".github/workflows/publish.yml: release-check has unhashed dependency "
                    "install commands: " + "; ".join(unsafe_installs)
                )
    if not release_gate_jobs:
        errors.append(
            ".github/workflows/publish.yml: missing required release-check gate"
        )

    publish_jobs = 0
    for job_name, raw_job in jobs.items():
        if not isinstance(raw_job, dict):
            continue
        job = raw_job
        steps = [step for step in job.get("steps", []) if isinstance(step, dict)]
        publish_indexes = [
            index for index, step in enumerate(steps) if _step_publishes_to_pypi(step)
        ]
        if not publish_indexes:
            if _job_has_oidc_write(job):
                errors.append(
                    ".github/workflows/publish.yml: only the PyPI publish job "
                    f"may receive id-token: write (found on {job_name!r})"
                )
            continue

        publish_jobs += len(publish_indexes)
        permissions = job.get("permissions", {})
        if not (
            isinstance(permissions, dict) and permissions.get("id-token") == "write"
        ):
            errors.append(
                f".github/workflows/publish.yml: publish job {job_name!r} "
                "must receive an explicit id-token: write permission"
            )
        elif any(
            value == "write" and name != "id-token"
            for name, value in permissions.items()
        ):
            errors.append(
                f".github/workflows/publish.yml: publish job {job_name!r} "
                "must not receive additional write permissions"
            )
        if _job_environment_name(job) != "pypi":
            errors.append(
                f".github/workflows/publish.yml: publish job {job_name!r} "
                "must use the protected 'pypi' environment"
            )
        if any("run" in step for step in steps):
            errors.append(
                f".github/workflows/publish.yml: OIDC publish job {job_name!r} "
                "must not execute run steps"
            )
        if len(steps) != 2 or not all(
            _step_uses_action(step, "actions/download-artifact")
            or _step_publishes_to_pypi(step)
            for step in steps
        ):
            errors.append(
                f".github/workflows/publish.yml: OIDC publish job {job_name!r} "
                "must contain only artifact download and PyPI publish actions"
            )

        for publish_index in publish_indexes:
            publish_step = steps[publish_index]
            if not _step_uses_pinned_action(
                publish_step, "pypa/gh-action-pypi-publish"
            ):
                errors.append(
                    ".github/workflows/publish.yml: PyPI publish action must be "
                    "pinned to a full commit SHA"
                )
            pinned_downloads = [
                step
                for step in steps[:publish_index]
                if _step_uses_pinned_action(step, "actions/download-artifact")
            ]
            for download in pinned_downloads:
                overrides = _artifact_provenance_overrides(download)
                if overrides:
                    errors.append(
                        ".github/workflows/publish.yml: artifact download must use "
                        "same-run provenance; forbidden options: "
                        + ", ".join(overrides)
                    )
            prior_downloads = [
                step
                for step in pinned_downloads
                if not _artifact_provenance_overrides(step)
                and _artifact_name(step)
                and _artifact_path(step)
            ]
            if not prior_downloads:
                errors.append(
                    f".github/workflows/publish.yml: publish job {job_name!r} "
                    "must download tested artifacts by name to an explicit path "
                    "with a commit-pinned action"
                )
                continue

            dependencies = _job_needs(job)
            build_jobs = [
                (name, jobs[name])
                for name in dependencies
                if name in jobs and isinstance(jobs[name], dict)
            ]
            download_artifacts = {
                (_artifact_name(step), _artifact_path(step)) for step in prior_downloads
            }
            artifact_gate_found = False
            for _build_job_name, build_job in build_jobs:
                build_steps = [
                    step
                    for step in build_job.get("steps", [])
                    if isinstance(step, dict)
                ]
                upload_indexes = [
                    index
                    for index, step in enumerate(build_steps)
                    if _step_uses_pinned_action(step, "actions/upload-artifact")
                    and (_artifact_name(step), _artifact_path(step))
                    in download_artifacts
                ]
                for upload_index in upload_indexes:
                    prior_build_steps = build_steps[:upload_index]
                    if (
                        any(
                            _step_runs_strict_twine_check(step)
                            for step in prior_build_steps
                        )
                        and any(
                            _step_installs_release_lock(step)
                            for step in prior_build_steps
                        )
                        and any(
                            _step_builds_without_isolation(step)
                            for step in prior_build_steps
                        )
                        and bool(release_gate_jobs.intersection(_job_needs(build_job)))
                    ):
                        artifact_gate_found = True
                        break
                if artifact_gate_found:
                    break
            if not artifact_gate_found:
                errors.append(
                    ".github/workflows/publish.yml: publish job must depend "
                    "directly on a build job that hash-installs "
                    "requirements-release.lock, builds with `--no-isolation`, "
                    "depends on the hash-locked release-check gate, "
                    "runs `twine check --strict`, and uploads the same artifact "
                    "with a commit-pinned action"
                )

    if publish_jobs == 0:
        errors.append(
            ".github/workflows/publish.yml: no pypa/gh-action-pypi-publish step found"
        )
    elif publish_jobs != 1:
        errors.append(
            ".github/workflows/publish.yml: exactly one PyPI publish step is required"
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
    """Install each applicable hash lock in its own throwaway virtualenv."""
    lockfiles = ["requirements.lock", "requirements-dev.lock"]
    if sys.platform.startswith("linux"):
        # requirements-release.lock is generated for the Linux/Python 3.11
        # publishing environment. Static pin/hash validation still runs on
        # every platform.
        lockfiles.append("requirements-release.lock")

    errors: list[str] = []
    for lockfile in lockfiles:
        with tempfile.TemporaryDirectory(prefix="geno-dependency-lock-") as raw_tmp:
            venv = Path(raw_tmp) / "venv"
            create_code = _run((sys.executable, "-m", "venv", str(venv)), root)
            if create_code != 0:
                errors.append(
                    f"{lockfile}: failed to create isolated temporary virtualenv"
                )
                continue
            python = _venv_python(venv)
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
