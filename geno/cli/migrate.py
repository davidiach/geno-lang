"""``geno migrate``: rewrite Geno 0.5 sources for Geno 0.6 (proposal 0010)."""

from __future__ import annotations

import difflib
import sys
from pathlib import Path

from ..migrate import MigrationError, migrate_source


def migrate_files(path: str, check: bool = False, diff: bool = False) -> None:
    """Migrate the ``.geno`` files under *path*, reporting what needs a person.

    Exits 1 when a file could not be read or migrated, and with ``--check``
    when any file would change or needs a manual rewrite.
    """
    from ..test_runner import discover_files

    target = Path(path)
    if not target.exists():
        print(f"Error: '{path}' not found", file=sys.stderr)
        sys.exit(1)
    files = [target] if target.is_file() else discover_files(target)
    if not files:
        print(f"No .geno files found in '{path}'", file=sys.stderr)
        sys.exit(1)

    changed_files = 0
    manual_items = 0
    had_error = False
    for filepath in sorted(files):
        try:
            source = filepath.read_text(encoding="utf-8")
            result = migrate_source(source)
        except (OSError, UnicodeDecodeError, MigrationError) as exc:
            print(f"Error: cannot migrate {filepath}: {exc}", file=sys.stderr)
            had_error = True
            continue
        for note in result.manual:
            manual_items += 1
            print(f"{filepath}:{note.line}: needs a manual rewrite: {note.message}")
        if result.source == source:
            continue
        changed_files += 1
        if diff:
            print(
                "".join(
                    difflib.unified_diff(
                        source.splitlines(keepends=True),
                        result.source.splitlines(keepends=True),
                        fromfile=str(filepath),
                        tofile=str(filepath),
                    )
                ),
                end="",
            )
        elif check:
            print(f"Would migrate: {filepath} ({len(result.changes)} changes)")
        else:
            try:
                filepath.write_text(result.source, encoding="utf-8")
            except OSError as exc:
                print(f"Error: cannot write {filepath}: {exc}", file=sys.stderr)
                had_error = True
                continue
            print(f"Migrated: {filepath} ({len(result.changes)} changes)")
            for note in result.changes:
                print(f"  {filepath}:{note.line}: {note.message}")

    if not changed_files and not manual_items and not had_error:
        print("Nothing to migrate.")
    elif manual_items:
        print(
            f"\n{manual_items} construct(s) need a manual rewrite; "
            "`geno check` reports anything else that no longer type-checks."
        )
    if had_error or (check and (changed_files or manual_items)):
        sys.exit(1)
