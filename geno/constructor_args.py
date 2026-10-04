"""Named constructor arguments (``Point(x: 1, y: 2)``)."""

from __future__ import annotations

from collections.abc import Sequence


def constructor_argument_order(
    constructor: str,
    argument_names: Sequence[str | None],
    field_names: Sequence[str],
) -> list[int]:
    """Return, for each field in declaration order, its argument's source index.

    Positional arguments fill fields from the left and must come before any
    named argument, as in function calls.  Arguments are still evaluated in
    source order; only their placement follows the fields.  Raises
    ``ValueError`` with a user-facing message for an unknown, repeated or
    missing field.
    """
    source_index: dict[str, int] = {}
    seen_named = False
    for position, name in enumerate(argument_names):
        if name is None:
            if seen_named:
                raise ValueError(
                    "Positional argument cannot follow a named argument in "
                    f"constructor {constructor}"
                )
            if position >= len(field_names):
                raise ValueError(
                    f"Constructor {constructor} expects {len(field_names)} "
                    f"arguments, got {len(argument_names)}"
                )
            source_index[field_names[position]] = position
            continue
        seen_named = True
        if name not in field_names:
            raise ValueError(f"Constructor {constructor} has no field '{name}'")
        if name in source_index:
            raise ValueError(
                f"Field '{name}' is given more than once in constructor {constructor}"
            )
        source_index[name] = position
    missing = [field for field in field_names if field not in source_index]
    if missing:
        raise ValueError(
            f"Constructor {constructor} is missing field "
            + ", ".join(f"'{field}'" for field in missing)
        )
    return [source_index[field] for field in field_names]
