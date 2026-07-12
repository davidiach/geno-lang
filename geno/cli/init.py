"""``geno init`` — create a new project."""

from __future__ import annotations

import sys
from pathlib import Path


def init_project(name: str, template: str = "minimal"):
    """Create a new Geno project directory."""
    from ..init import create_project

    is_current_directory = name == "."
    if (
        ("/" in name and not is_current_directory)
        or "\\" in name
        or (name.startswith(".") and not is_current_directory)
        or name.startswith("~")
        or Path(name).is_absolute()
    ):
        print(f"Error: '{name}' is not a valid project name", file=sys.stderr)
        sys.exit(1)

    project_path = Path(name)

    try:
        created_files = create_project(project_path, template)
        print(f"Created project '{name}':")
        for f in created_files:
            print(f"  {f}")
    except (OSError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
