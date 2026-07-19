"""Shared defaults for cooperative Geno execution limits."""

import sys

DEFAULT_INTERPRETER_MAX_STEPS = 1_000_000

# XNU accounts large reserved VM-map regions and Python compilation needs more
# growth headroom than other supported hosts. This remains a finite kernel-
# enforced budget; callers can explicitly choose a smaller or larger value.
DEFAULT_PROCESS_MAX_MEMORY_BYTES = (
    512 * 1024 * 1024 if sys.platform == "darwin" else 256 * 1024 * 1024
)

# geno test defaults; defined here (a leaf module) so the CLI parser can
# reference them without importing the interpreter-backed test runner.
DEFAULT_TEST_TIMEOUT = 5.0
DEFAULT_TEST_MAX_STEPS = DEFAULT_INTERPRETER_MAX_STEPS
