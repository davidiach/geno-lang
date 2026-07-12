"""``geno install/add/search/update`` — package management."""

from __future__ import annotations

import sys
from typing import NoReturn

_PACKAGE_COMMAND_ERRORS = (
    FileNotFoundError,
    KeyError,
    OSError,
    RuntimeError,
    ValueError,
)


def _exit_with_package_error(prefix: str, exc: BaseException) -> NoReturn:
    print(f"{prefix}: {exc}", file=sys.stderr)
    raise SystemExit(1)


def pkg_install():
    """Install dependencies from geno.toml."""
    from ..package_manager import install

    try:
        installed = install()
        if installed:
            for name in installed:
                print(f"  Installed {name}")
            print(f"Installed {len(installed)} package(s).")
        else:
            print("All packages already installed.")
    except FileNotFoundError as e:
        _exit_with_package_error("Error", e)
    except _PACKAGE_COMMAND_ERRORS as e:
        _exit_with_package_error("Error installing packages", e)


def pkg_add(name: str, url: str | None = None, branch: str | None = None):
    """Add a dependency and install it."""
    from ..manifest import can_map_to_pascal, kebab_to_pascal
    from ..package_manager import add

    tag = None

    # Resolve URL from package index if not provided
    if url is None:
        from ..package_index import resolve

        pkg = resolve(name)
        if pkg is None:
            print(
                f"Error: Package '{name}' not found in index. "
                f"Provide a git URL: geno add {name} <url>",
                file=sys.stderr,
            )
            sys.exit(1)
        url = pkg["git"]
        if branch is None:
            tag = pkg["tag"]
        print(f"Resolved '{name}' from package index")

    branch_ref = branch or "main"

    if not can_map_to_pascal(name):
        print(
            f"Warning: Package name '{name}' cannot be cleanly mapped to a "
            f"PascalCase import identifier. Consider using kebab-case "
            f"(e.g., 'my-lib') for package names.",
            file=sys.stderr,
        )

    try:
        add(name, url, branch=branch_ref, tag=tag)
        pascal = kebab_to_pascal(name)
        ref = f"tag: {tag}" if tag else f"branch: {branch_ref}"
        print(f"Added {name} ({url}, {ref})")
        if pascal != name:
            print(f"  Import as: import {pascal}")
    except _PACKAGE_COMMAND_ERRORS as e:
        _exit_with_package_error("Error", e)


def pkg_search(query: str):
    """Search the package index."""
    from ..package_index import search

    results = search(query)
    if not results:
        print(f"No packages found matching '{query}'")
        return

    print(f"Found {len(results)} package(s):\n")
    for pkg in results:
        targets = ", ".join(pkg.get("targets", []))
        print(f"  {pkg['name']} v{pkg.get('latest_version', pkg.get('version', '?'))}")
        print(f"    {pkg.get('description', '')}")
        print(f"    git: {pkg.get('git', '')}")
        if targets:
            print(f"    targets: {targets}")
        print()


def pkg_update(name=None):
    """Update dependencies to latest commits."""
    from ..package_manager import update

    try:
        updated = update(name)
        if updated:
            for dep_name in updated:
                print(f"  Updated {dep_name}")
            print(f"Updated {len(updated)} package(s).")
        else:
            print("All packages up to date.")
    except _PACKAGE_COMMAND_ERRORS as e:
        _exit_with_package_error("Error", e)
