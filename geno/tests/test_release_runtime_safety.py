"""Regression tests for the public-release runtime hardening audit."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from geno import _runtime_support as compiled_runtime
from geno.builtins import (
    _validate_regex_pattern,
    builtin_is_permutation,
    builtin_json_parse,
    builtin_round,
)
from geno.values import ConstructorValue
from geno.values import RuntimeError as GenoRuntimeError


def _nested_json(depth: int) -> str:
    return "[" * depth + "0" + "]" * depth


@pytest.mark.parametrize(
    "validator",
    [_validate_regex_pattern, compiled_runtime._validate_regex_pattern],
)
def test_overlapping_noncapturing_regex_is_rejected(validator) -> None:
    with pytest.raises(
        (GenoRuntimeError, RuntimeError), match="overlapping alternation"
    ):
        validator("(?:a|a)*b", "regex_match")


def test_deep_json_returns_err_in_interpreter_and_compiled_python() -> None:
    text = _nested_json(129)

    interpreted = builtin_json_parse(text)
    compiled = compiled_runtime.json_parse(text)

    assert interpreted.constructor == "Err"
    assert "nested too deeply" in interpreted.fields["error"]
    assert type(compiled).__name__ == "Err"
    assert "nested too deeply" in compiled.error


@pytest.mark.parametrize("rounder", [builtin_round, compiled_runtime.round_])
def test_round_does_not_cross_half_due_to_float_addition(rounder) -> None:
    assert rounder(0.49999999999999994) == 0
    assert rounder(0.5) == 1
    assert rounder(-0.5) == 0


def test_is_permutation_handles_unorderable_values_without_quadratic_fallback() -> None:
    left = [ConstructorValue("Some", {"value": i % 7}) for i in range(2_000)]
    right = list(reversed(left))

    assert builtin_is_permutation(left, right) is True


def _run_cli(tmp_path, source: str, *args: str) -> subprocess.CompletedProcess[str]:
    program = tmp_path / "Main.geno"
    program.write_text(source, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "geno", "run", str(program), *args],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            """func main() -> Int\n    return clock_now()\nend func\n""",
            None,
        ),
        (
            """func main() -> Int\n    return random_int(min: 1, max: 1)\nend func\n""",
            "=> 1",
        ),
    ],
)
def test_default_process_run_supports_its_default_capabilities(
    tmp_path, source: str, expected: str | None
) -> None:
    result = _run_cli(tmp_path, source, "--no-check-examples")

    assert result.returncode == 0, result.stderr
    assert "Import of" not in result.stderr
    if expected is not None:
        assert expected in result.stdout


def test_default_process_run_supports_pure_json_builtin(tmp_path) -> None:
    source = """func main() -> Int
    match json_parse(text: "42") with
        | Ok(JsonInt(value)) -> return value
        | _ -> return 0
    end match
end func
"""
    result = _run_cli(tmp_path, source, "--no-check-examples")

    assert result.returncode == 0, result.stderr
    assert "=> 42" in result.stdout


def test_json_mode_uses_normal_default_print_capability(tmp_path) -> None:
    result = _run_cli(
        tmp_path,
        """func main() -> Unit\n    print(\"hello\")\nend func\n""",
        "--json",
        "--no-check-examples",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["output"] == "hello\n"


@pytest.mark.parametrize("module_name", ["codecs", "posixpath"])
def test_thread_sandbox_blocks_filesystem_helper_imports(module_name: str) -> None:
    from geno.sandbox import SandboxConfig, SecurityViolation, run_sandboxed

    with pytest.raises((SecurityViolation, RuntimeError), match="not allowed"):
        run_sandboxed(
            f"import {module_name}\n__result__ = {module_name}",
            SandboxConfig(strict=False),
        )


@pytest.mark.parametrize("module_name", ["codecs", "posixpath"])
def test_process_sandbox_blocks_filesystem_helper_imports(module_name: str) -> None:
    from geno.sandbox import ProcessSandboxConfig, run_in_process

    with pytest.raises(RuntimeError, match="not allowed"):
        run_in_process(
            f"import {module_name}\n__result__ = {module_name}",
            ProcessSandboxConfig(timeout=5.0, strict=False),
        )


@pytest.mark.parametrize(
    "pattern",
    [
        "(a|[a])*b",
        "(?:a|[a])*b",
        r"(a|\x61)*b",
        "((a|[a]))*b",
    ],
)
@pytest.mark.parametrize(
    "validator",
    [_validate_regex_pattern, compiled_runtime._validate_regex_pattern],
)
def test_equivalent_repeated_regex_alternatives_are_rejected(
    validator, pattern: str
) -> None:
    with pytest.raises(
        (GenoRuntimeError, RuntimeError), match="overlapping alternation"
    ):
        validator(pattern, "regex_match")


def test_in_process_compiled_runtime_receives_private_support_modules() -> None:
    from geno.compiler import compile_and_exec

    clock_env = compile_and_exec(
        """func main() -> Int with clock
    return clock_now()
end func
""",
        timeout=None,
        capabilities={"clock"},
    )
    random_env = compile_and_exec(
        """func main() -> Int with random
    return random_int(min: 1, max: 1)
end func
""",
        timeout=None,
        capabilities={"random"},
    )
    path_env = compile_and_exec(
        """func main() -> String
    return path_join("/base", "child")
end func
""",
        timeout=None,
    )

    assert isinstance(clock_env["main"](), int)
    assert random_env["main"]() == 1
    assert path_env["main"]() == "/base/child"


@pytest.mark.parametrize(
    "validator",
    [_validate_regex_pattern, compiled_runtime._validate_regex_pattern],
)
def test_nested_optional_regex_quantifier_is_rejected(validator) -> None:
    with pytest.raises((GenoRuntimeError, RuntimeError), match="nested quantifiers"):
        validator("(a?){25}a{25}", "regex_match")
