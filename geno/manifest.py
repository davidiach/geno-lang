"""
Manifest parsing for geno.toml
==============================

Reads and writes the ``[dependencies]`` section of a Geno project manifest.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]


def kebab_to_pascal(name: str) -> str:
    """Convert a kebab-case package name to a PascalCase import name.

    Examples:
        "http-utils" -> "HttpUtils"
        "json-parser" -> "JsonParser"
        "url" -> "Url"
        "my-lib" -> "MyLib"

    If the name can't cleanly map (empty segments, non-alpha characters),
    returns the original name unchanged.
    """
    parts = name.split("-")
    if any(not part or not part.isalpha() for part in parts):
        return name
    return "".join(part.capitalize() for part in parts)


def pascal_to_kebab(name: str) -> str:
    """Convert a PascalCase import name to a kebab-case package name.

    Examples:
        "HttpUtils" -> "http-utils"
        "JsonParser" -> "json-parser"
        "Url" -> "url"
    """
    result: list[str] = []
    for char in name:
        if char.isupper() and result:
            result.append("-")
        result.append(char.lower())
    return "".join(result)


def can_map_to_pascal(name: str) -> bool:
    """Check whether a package name can be cleanly mapped to PascalCase."""
    pascal = kebab_to_pascal(name)
    return pascal != name or (bool(name) and name[0].isupper() and name.isalpha())


_WINDOWS_RESERVED_DEPENDENCY_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)
_MANIFEST_ENTRYPOINT_RE = re.compile(r"^[A-Z][A-Za-z0-9_]*$")
_DEPENDENCY_NAME_RE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_]*|[A-Za-z]+(?:-[A-Za-z]+)*)$"
)
_MODULE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_PROJECT_CONFIG_BYTES = 1024 * 1024


def _read_bounded_regular_text(
    path: Path,
    *,
    label: str,
    allow_symlink: bool = False,
) -> str:
    """Read a small UTF-8 config from a regular file without unsafe following."""
    if path.is_symlink() and not allow_symlink:
        raise ValueError(f"{label} must not be a symbolic link: {path}")
    flags = os.O_RDONLY
    if not allow_symlink and hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"Could not safely open {label}: {path}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{label} must be a regular file: {path}")
        if info.st_size > _MAX_PROJECT_CONFIG_BYTES:
            raise ValueError(f"{label} is too large: {path}")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            data = handle.read(_MAX_PROJECT_CONFIG_BYTES + 1)
        if len(data) > _MAX_PROJECT_CONFIG_BYTES:
            raise ValueError(f"{label} is too large: {path}")
        return data.decode("utf-8")
    finally:
        if fd >= 0:
            os.close(fd)


def validate_dependency_name(name: str) -> None:
    """Validate dependency name is safe for filesystem use under geno_modules."""
    path_name = Path(name)
    windows_stem = name.rstrip(" .").split(".", 1)[0].upper()
    if (
        not _DEPENDENCY_NAME_RE.fullmatch(name)
        or not name
        or "\x00" in name
        or ":" in name
        or path_name.is_absolute()
        or "/" in name
        or "\\" in name
        or "." in name
        or any(part in {"", ".", ".."} for part in path_name.parts)
        or windows_stem in _WINDOWS_RESERVED_DEPENDENCY_NAMES
    ):
        raise ValueError(
            f"Invalid dependency name '{name}': must be a simple module name"
        )


def validate_module_name(name: str) -> None:
    """Validate a module name before a backend emits it as an identifier."""
    if not _MODULE_NAME_RE.fullmatch(name):
        raise ValueError(
            f"Invalid module name '{name}': must be a single ASCII identifier"
        )


def validate_manifest_entrypoint(entrypoint: str) -> None:
    """Validate manifest entrypoint names before they become filesystem paths."""
    if not _MANIFEST_ENTRYPOINT_RE.fullmatch(entrypoint):
        raise ValueError(
            f"Invalid manifest entrypoint '{entrypoint}': must be a single "
            "PascalCase module name"
        )


def _optional_string(raw: dict, key: str) -> str | None:
    """Return an optional string manifest field, or raise for invalid types."""
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, str):
        raise ValueError(f"Manifest field '{key}' must be a string")
    return value


def _string_list(raw: dict, key: str, *, label: str | None = None) -> list[str]:
    """Return a list-of-strings manifest field, or raise for invalid types."""
    value = raw.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        field = label or key
        raise ValueError(f"Manifest field '{field}' must be a list of strings")
    return list(value)


@dataclass
class Dependency:
    """A single git-based dependency."""

    name: str
    git: str
    branch: str = "main"
    tag: str | None = None  # semver tag, e.g. "v0.3.0"


@dataclass
class Manifest:
    """Parsed representation of geno.toml."""

    entrypoint: str | None = None
    files: list[str] = field(default_factory=list)
    dependencies: Dict[str, Dependency] = field(default_factory=dict)
    name: str | None = None
    version: str | None = None
    targets: list[str] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)  # exported module names
    _raw: dict = field(default_factory=dict, repr=False)


def parse_manifest(path: Path, *, allow_symlink: bool = False) -> Manifest:
    """Parse a geno.toml file into a Manifest."""
    if tomllib is None:
        raise RuntimeError(
            "TOML parsing not available. Install tomli for Python < 3.11."
        )
    text = _read_bounded_regular_text(
        path, label="Geno manifest", allow_symlink=allow_symlink
    )
    raw = tomllib.loads(text)

    files = _string_list(raw, "files")
    targets = _string_list(raw, "targets") if "targets" in raw else []
    if not targets and "target" in raw:
        target = _optional_string(raw, "target")
        if target is not None:
            targets = [target]

    dependencies = raw.get("dependencies", {})
    if not isinstance(dependencies, dict):
        raise ValueError("Manifest field 'dependencies' must be a table")

    deps: Dict[str, Dependency] = {}
    for dep_name, info in dependencies.items():
        validate_dependency_name(dep_name)
        if not isinstance(info, dict) or "git" not in info:
            raise ValueError(
                f"Dependency '{dep_name}' must have a 'git' key with a URL"
            )
        git = info["git"]
        if not isinstance(git, str):
            raise ValueError(f"Dependency '{dep_name}' field 'git' must be a string")
        branch = info.get("branch", "main")
        if not isinstance(branch, str):
            raise ValueError(f"Dependency '{dep_name}' field 'branch' must be a string")
        tag = info.get("tag")
        if tag is not None and not isinstance(tag, str):
            raise ValueError(f"Dependency '{dep_name}' field 'tag' must be a string")
        deps[dep_name] = Dependency(
            name=dep_name,
            git=git,
            branch=branch,
            tag=tag,
        )

    # Parse exports section
    exports_section = raw.get("exports", {})
    if not isinstance(exports_section, dict):
        raise ValueError("Manifest field 'exports' must be a table")
    export_modules = _string_list(exports_section, "modules", label="exports.modules")

    entrypoint = _optional_string(raw, "entrypoint")
    if entrypoint is not None:
        validate_manifest_entrypoint(entrypoint)

    return Manifest(
        entrypoint=entrypoint,
        files=files,
        dependencies=deps,
        name=_optional_string(raw, "name"),
        version=_optional_string(raw, "version"),
        targets=targets,
        exports=export_modules,
        _raw=raw,
    )


def _toml_escape(value: str) -> str:
    """Escape a string for use in a TOML quoted value."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


_BARE_TOML_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _serialize_toml_key(key: str) -> str:
    """Serialize a TOML key, quoting it when required."""
    if _BARE_TOML_KEY_RE.fullmatch(key):
        return key
    return f'"{_toml_escape(key)}"'


_KNOWN_TOP_LEVEL_KEYS = frozenset(
    {
        "name",
        "version",
        "entrypoint",
        "targets",
        "files",
        "target",
        "dependencies",
        "exports",
    }
)


def _serialize_toml_value(value: object) -> str:
    """Serialize a single TOML value to a string."""
    if isinstance(value, str):
        return f'"{_toml_escape(value)}"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value)
    if isinstance(value, list):
        items = ", ".join(_serialize_toml_value(v) for v in value)
        return f"[{items}]"
    if isinstance(value, dict):
        items = ", ".join(
            f"{_serialize_toml_key(key)} = {_serialize_toml_value(item)}"
            for key, item in value.items()
        )
        return f"{{ {items} }}"
    return f'"{_toml_escape(str(value))}"'


def _append_toml_table(
    lines: list[str], key_path: list[str], table: dict[str, object]
) -> None:
    """Append a TOML table, recursing into nested subtables."""
    lines.append(f"[{'.'.join(_serialize_toml_key(part) for part in key_path)}]")

    nested_tables: list[tuple[str, dict[str, object]]] = []
    for key, value in table.items():
        if isinstance(value, dict):
            nested_tables.append((key, value))
        else:
            lines.append(f"{_serialize_toml_key(key)} = {_serialize_toml_value(value)}")

    if nested_tables and lines[-1] != "":
        lines.append("")
    for index, (key, value) in enumerate(nested_tables):
        _append_toml_table(lines, [*key_path, key], value)
        if index != len(nested_tables) - 1:
            lines.append("")


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically.

    Writes a sibling temp file, flushes and fsyncs it, then ``os.replace``s it
    over the target — an atomic rename on the same filesystem. A crash or Ctrl-C
    therefore leaves either the previous file or the fully-written new one, never
    a truncated/corrupt file (unlike a plain ``write_text`` that truncates first).
    """

    def _replacement_mode() -> int:
        try:
            return stat.S_IMODE(path.stat().st_mode)
        except FileNotFoundError:
            current_umask = os.umask(0)
            os.umask(current_umask)
            return 0o666 & ~current_umask

    directory = path.parent
    fd, tmp_name = tempfile.mkstemp(
        dir=directory, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, _replacement_mode())
        os.replace(tmp_name, path)
    except BaseException:
        # Never leak the temp file, and leave the original untouched.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def save_manifest(manifest: Manifest, path: Path) -> None:
    """Write a Manifest back to geno.toml (TOML format).

    Unknown top-level keys from the original file are preserved.
    """
    if manifest.entrypoint is not None:
        validate_manifest_entrypoint(manifest.entrypoint)
    for dep_name in manifest.dependencies:
        validate_dependency_name(dep_name)

    lines: list[str] = []

    if manifest.name:
        lines.append(f'name = "{_toml_escape(manifest.name)}"')
    if manifest.version:
        lines.append(f'version = "{_toml_escape(manifest.version)}"')
    if manifest.entrypoint:
        lines.append(f'entrypoint = "{_toml_escape(manifest.entrypoint)}"')

    if manifest.targets:
        targets_str = ", ".join(f'"{_toml_escape(t)}"' for t in manifest.targets)
        lines.append(f"targets = [{targets_str}]")

    if manifest.files:
        lines.append("files = [")
        for f in manifest.files:
            lines.append(f'    "{_toml_escape(f)}",')
        lines.append("]")

    # Preserve unknown top-level keys from the original TOML
    for key, value in manifest._raw.items():
        if key not in _KNOWN_TOP_LEVEL_KEYS and not isinstance(value, dict):
            lines.append(f"{_serialize_toml_key(key)} = {_serialize_toml_value(value)}")

    if manifest.exports:
        lines.append("")
        lines.append("[exports]")
        lines.append("modules = [")
        for m in manifest.exports:
            lines.append(f'    "{_toml_escape(m)}",')
        lines.append("]")

    if manifest.dependencies:
        lines.append("")
        for dep_name, dep in manifest.dependencies.items():
            lines.append(f"[dependencies.{_serialize_toml_key(dep_name)}]")
            lines.append(f'git = "{_toml_escape(dep.git)}"')
            if dep.tag:
                lines.append(f'tag = "{_toml_escape(dep.tag)}"')
            elif dep.branch != "main":
                lines.append(f'branch = "{_toml_escape(dep.branch)}"')
            lines.append("")

    for key, value in manifest._raw.items():
        if key not in _KNOWN_TOP_LEVEL_KEYS and isinstance(value, dict):
            _append_toml_table(lines, [key], value)
            lines.append("")

    atomic_write_text(path, "\n".join(lines) + "\n")
