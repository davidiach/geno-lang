"""Shared defaults for cooperative Geno execution limits."""

DEFAULT_INTERPRETER_MAX_STEPS = 1_000_000

# geno test defaults; defined here (a leaf module) so the CLI parser can
# reference them without importing the interpreter-backed test runner.
DEFAULT_TEST_TIMEOUT = 5.0
DEFAULT_TEST_MAX_STEPS = DEFAULT_INTERPRETER_MAX_STEPS
