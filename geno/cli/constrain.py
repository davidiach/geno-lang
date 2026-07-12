"""``geno constrain`` — inspect next-token constraints."""

from __future__ import annotations

import sys


def constrain_cli(
    prefix: str | None, json_output: bool = False, validate_only: bool = False
) -> None:
    """Inspect next-token constraints for a partial Geno source prefix."""
    import json as json_mod

    from ..api import constrain_prefix

    if prefix is None:
        prefix = sys.stdin.read()

    result = constrain_prefix(prefix)

    if json_output:
        print(json_mod.dumps(result.to_dict(), indent=2, allow_nan=False))
    elif validate_only:
        if result.valid:
            print("valid")
        else:
            print(f"Invalid prefix: {result.error}", file=sys.stderr)
            if result.unclosed_blocks:
                print(
                    f"Unclosed blocks: {', '.join(result.unclosed_blocks)}",
                    file=sys.stderr,
                )
    else:
        stream = sys.stdout if result.valid else sys.stderr
        if not result.valid:
            print(f"Invalid prefix: {result.error}", file=stream)
        print(result.allowed_next, file=stream)

    if not result.valid:
        sys.exit(1)
