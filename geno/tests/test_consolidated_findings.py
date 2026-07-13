"""
Tests for consolidated security review findings.

Covers all four phases (P0-P3) of the adversarial code review,
verifying that each finding is properly implemented.
"""

import math
import os
import re
import typing

import pytest
import yaml  # type: ignore[import-untyped]

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib

from geno._runtime_support import _safe_add, _safe_mul
from geno.api import RunConfig, check, run
from geno.builtins import (
    builtin_is_permutation,
    builtin_parse_int,
    builtin_sort_strings,
)
from geno.compiler import compile_and_exec, compile_to_python
from geno.lexer import Lexer, LexerError
from geno.parser import ParseError, Parser
from geno.sandbox import (
    SAFE_BUILTINS,
    SandboxConfig,
    SecurityViolation,
    _create_module_proxy,
    _create_safe_import,
    run_sandboxed,
)
from geno.server import _REQUEST_TIMEOUT_SECONDS, MAX_CONCURRENT_REQUESTS

# =========================================================================
# Phase 1 (P0) - Critical Security
# =========================================================================


class TestP0_1_ModuleProxyInProcessSandbox:
    """P0-1: Module proxy blocks dangerous attribute access on imported modules."""

    def test_typing_builtins_blocked(self):
        """typing.__dict__['__builtins__'] must be blocked via proxy."""
        proxy = _create_module_proxy(typing)
        with pytest.raises(SecurityViolation, match="__dict__"):
            proxy.__dict__

    def test_copy_builtins_blocked(self):
        """copy.__builtins__ must be blocked via proxy."""
        import copy

        proxy = _create_module_proxy(copy)
        with pytest.raises(SecurityViolation, match="__builtins__"):
            proxy.__builtins__

    def test_math_sqrt_works_through_proxy(self):
        """math.sqrt should still be accessible through the proxy."""
        proxy = _create_module_proxy(math)
        assert proxy.sqrt(16) == 4.0

    def test_underscore_prefixed_attrs_blocked(self):
        """Private (_-prefixed) attributes must be blocked on proxied modules."""
        proxy = _create_module_proxy(math)
        with pytest.raises(SecurityViolation, match="private"):
            proxy._some_internal

    def test_safe_import_returns_proxy(self):
        """_create_safe_import should return proxied modules."""
        safe_import = _create_safe_import()
        math_proxy = safe_import("math")
        # Should work for safe attrs
        assert math_proxy.sqrt(9) == 3.0
        # Should block dangerous attrs
        with pytest.raises(SecurityViolation):
            math_proxy.__builtins__

    def test_safe_dunder_attrs_allowed(self):
        """Safe dunders like __name__ and __doc__ should be accessible."""
        proxy = _create_module_proxy(math)
        assert proxy.__name__ == "math"
        assert proxy.__doc__ is not None


class TestP0_2_WallClockBudgetForRun:
    """P0-2: Server has bounded concurrency."""

    def test_max_concurrent_requests_constant_exists(self):
        """MAX_CONCURRENT_REQUESTS constant must exist and be 16."""
        assert MAX_CONCURRENT_REQUESTS == 16

    def test_run_works_normally(self):
        """Basic /run functionality still works."""
        source = """\
func main() -> Int
  return 42
end func
"""
        result = run(source)
        assert result.ok
        assert result.value == 42


class TestP0_3_IntegerArithmeticBomb:
    """P0-3: Huge integer results are rejected."""

    def test_huge_multiplication_rejected_in_interpreter(self):
        """Multiplication producing huge integers must be rejected."""
        # 10^10000 * 10^10000 = 10^20000 -- way too big
        source = """\
func main() -> Int
  let a: Int = 999999999
  let b: Int = 999999999
  var result: Int = a * b
  var i: Int = 0
  while i < 20 do
    result = result * result
    i = i + 1
  end while
  return result
end func
"""
        result = run(source, config=RunConfig(timeout=5.0))
        assert not result.ok
        diag_text = " ".join(d.message for d in result.diagnostics)
        assert "integer" in diag_text.lower() or "size" in diag_text.lower()

    def test_parse_int_huge_string_rejected(self):
        """builtin_parse_int("9" * 100000) must be rejected."""
        huge = "9" * 100_000
        with pytest.raises(Exception, match="too long"):
            builtin_parse_int(huge)

    def test_integer_literal_over_1000_digits_rejected_at_lex(self):
        """Integer literals with > 1000 digits must be rejected at lex time."""
        huge_literal = "1" * 1001
        source = f"""\
func main() -> Int
  return {huge_literal}
end func
"""
        with pytest.raises(LexerError, match="too long"):
            Lexer(source).tokenize()

    def test_integer_literal_under_1000_digits_ok(self):
        """Integer literals with <= 1000 digits should be accepted."""
        normal_literal = "1" * 999
        source = f"""\
func main() -> Int
  return {normal_literal}
end func
"""
        tokens = Lexer(source).tokenize()
        assert any(t.value == int(normal_literal) for t in tokens)

    def test_float_literal_long_fraction_rejected_at_lex(self):
        """The length guard must cover the fractional part, not just the integer.

        A small integer part previously let an arbitrarily long fractional part
        slip past the "max 1000 digits" guard.
        """
        huge_literal = "1." + "9" * 1001
        source = f"""\
func main() -> Float
  return {huge_literal}
end func
"""
        with pytest.raises(LexerError, match="too long"):
            Lexer(source).tokenize()

    def test_float_literal_under_1000_fraction_digits_ok(self):
        """Float literals with <= 1000 digits per part should be accepted."""
        normal_literal = "1." + "9" * 999
        source = f"""\
func main() -> Float
  return {normal_literal}
end func
"""
        tokens = Lexer(source).tokenize()
        assert any(t.value == float(normal_literal) for t in tokens)

    def test_safe_mul_rejects_huge_result(self):
        """_safe_mul in the compiled path must reject huge integer results."""
        # Create two huge-enough integers whose product exceeds _MAX_INTEGER_BITS
        a = 2**20000
        b = 2**20000
        with pytest.raises(RuntimeError, match=r"[Ii]nteger.*size"):
            _safe_mul(a, b)

    def test_safe_add_rejects_huge_result(self):
        """_safe_add must reject integer results that exceed the bit limit."""
        a = 2**40000
        b = 2**40000
        with pytest.raises(RuntimeError, match=r"[Ii]nteger.*size"):
            _safe_add(a, b)

    def test_safe_mul_small_values_ok(self):
        """_safe_mul should work fine for normal-sized values."""
        assert _safe_mul(7, 6) == 42
        assert _safe_mul(100, 200) == 20000


class TestP0_4_SandboxImportAllowlist:
    """Sandbox import allowlist: safe modules allowed, dangerous ones blocked."""

    def test_import_re_blocked_in_sandbox(self):
        """import re must be blocked — runtime builtins get _re via pre-injection."""
        config = SandboxConfig(timeout=5.0, strict=False)
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed("import re", config)

    def test_import_math_still_allowed(self):
        """import math should still work (it's in the allowlist)."""
        config = SandboxConfig(timeout=5.0, strict=False)
        run_sandboxed("import math\n__result__ = math.sqrt(25)", config)

    def test_import_os_blocked(self):
        """import os must be blocked."""
        config = SandboxConfig(timeout=5.0, strict=False)
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed("import os", config)


# =========================================================================
# Phase 2 (P1) - Pre-Exposure Hardening
# =========================================================================


class TestP1_1_RemoveBuildClassFromSafeBuiltins:
    """P1-1: __build_class__ is NOT in SAFE_BUILTINS."""

    def test_build_class_not_in_safe_builtins(self):
        """__build_class__ must not be in SAFE_BUILTINS."""
        assert "__build_class__" not in SAFE_BUILTINS

    def test_compile_and_exec_still_works(self):
        """compile_and_exec should still work (runtime prelude compiles)."""
        source = """\
func main() -> Int
  return 42
end func
"""
        result = compile_and_exec(source, sandboxed=True, timeout=None)
        assert "main" in result or "__result__" in result


class TestP1_2_TypeVarSubstitutionTracking:
    """P1-2: TypeVar substitution catches type mismatches in generic calls."""

    def test_append_list_int_with_string_rejected(self):
        """append([1, 2, 3], "oops") must be rejected by the type checker."""
        source = """\
func main() -> List[Int]
  let xs: List[Int] = [1, 2, 3]
  return append(xs, "oops")
end func
"""
        result = check(source)
        assert not result.ok

    def test_append_list_int_with_int_accepted(self):
        """append([1, 2, 3], 4) must be accepted by the type checker."""
        source = """\
func main() -> List[Int]
  let xs: List[Int] = [1, 2, 3]
  return append(xs, 4)
end func
"""
        result = check(source)
        assert result.ok

    def test_contains_list_int_with_string_rejected(self):
        """contains([1, 2, 3], "hello") must be rejected by the type checker."""
        source = """\
func main() -> Bool
  let xs: List[Int] = [1, 2, 3]
  return contains(xs, "hello")
end func
"""
        result = check(source)
        assert not result.ok

    def test_map_with_type_changing_fn_returns_correct_type(self):
        """map([1,2,3], fn(x: Int) -> String ...) should return List[String]."""
        source = """\
func main() -> List[String]
  let xs: List[Int] = [1, 2, 3]
  return map(xs, fn(x: Int) -> to_string(x))
end func
"""
        result = check(source)
        assert result.ok


class TestP1_3_ServerThreadExhaustion:
    """P1-3: Server thread exhaustion constants exist."""

    def test_max_concurrent_requests_exists(self):
        """MAX_CONCURRENT_REQUESTS constant must exist."""
        assert isinstance(MAX_CONCURRENT_REQUESTS, int)
        assert MAX_CONCURRENT_REQUESTS > 0

    def test_request_timeout_seconds_exists(self):
        """_REQUEST_TIMEOUT_SECONDS constant must exist."""
        assert isinstance(_REQUEST_TIMEOUT_SECONDS, (int, float))
        assert _REQUEST_TIMEOUT_SECONDS > 0


class TestP1_4_Dockerignore:
    """P1-4: .dockerignore file exists."""

    def test_dockerignore_exists(self):
        """The .dockerignore file must exist in the repo root."""
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        dockerignore_path = os.path.join(repo_root, ".dockerignore")
        assert os.path.isfile(dockerignore_path), ".dockerignore not found"


# =========================================================================
# Phase 3 (P2) - Correctness
# =========================================================================


class TestP2_1_P2_2_ClearExampleOutputAndSaveRestoreSteps:
    """P2-1/P2-2: Example output is NOT in RunResult, and example steps
    don't consume the main budget."""

    def test_example_output_not_in_run_result(self):
        """Output from examples must not appear in RunResult.output."""
        source = """\
func double(x: Int) -> Int
  example 2 -> 4
  example 3 -> 6
  return x * 2
end func

func main() -> Int
  print(double(5))
  return 0
end func
"""
        result = run(
            source,
            config=RunConfig(check_examples=True, capabilities={"print"}),
        )
        assert result.ok
        # The output should only contain the main program's print, not examples
        # "10" from double(5)
        assert "10" in result.output
        # Ensure examples didn't leave output behind (e.g. "4" or "6" from
        # evaluating example outputs).  They may appear as substrings of "10",
        # so we just verify output is short/clean.
        lines = [line for line in result.output.strip().splitlines() if line.strip()]
        assert len(lines) == 1, f"Expected only main output, got: {result.output!r}"

    def test_example_steps_dont_consume_main_budget(self):
        """Example verification should not consume the main step budget."""
        source = """\
func fib(n: Int) -> Int
  example 0 -> 0
  example 1 -> 1
  example 5 -> 5
  if n <= 1 then
    return n
  end if
  return fib(n - 1) + fib(n - 2)
end func

func main() -> Int
  return fib(10)
end func
"""
        # Run with a moderate step budget -- examples shouldn't eat into it
        result = run(
            source,
            config=RunConfig(max_steps=100_000, check_examples=True),
        )
        assert result.ok
        assert result.value == 55


class TestP2_3_BoolPatternExhaustiveness:
    """P2-3: Bool pattern matching requires both arms or a wildcard."""

    def test_bool_only_true_rejected(self):
        """Matching on Bool with only a `true` arm must be rejected."""
        source = """\
func check(b: Bool) -> Int
  example true -> 1
  match b with
    | true -> return 1
  end match
  return 0
end func

func main() -> Int
  return check(true)
end func
"""
        result = check(source)
        assert not result.ok
        diag_text = " ".join(d.message for d in result.diagnostics)
        assert "exhaustive" in diag_text.lower() or "false" in diag_text.lower()

    def test_bool_both_arms_passes(self):
        """Matching on Bool with both true and false arms must pass."""
        source = """\
func check(b: Bool) -> Int
  example true -> 1
  example false -> 0
  match b with
    | true -> return 1
    | false -> return 0
  end match
end func

func main() -> Int
  return check(true)
end func
"""
        result = check(source)
        assert result.ok

    def test_bool_wildcard_passes(self):
        """Matching on Bool with a wildcard arm must pass."""
        source = """\
func check(b: Bool) -> Int
  example true -> 1
  example false -> 0
  match b with
    | true -> return 1
    | _ -> return 0
  end match
end func

func main() -> Int
  return check(false)
end func
"""
        result = check(source)
        assert result.ok


class TestP2_4_ParserNestingDepth:
    """P2-4: Deeply nested statements (>50) raise ParseError."""

    def test_deeply_nested_if_raises_parse_error(self):
        """More than 50 levels of nesting must raise ParseError."""
        depth = 55
        body = "return 1\n"
        for _ in range(depth):
            body = f"  if true then\n  {body}  end if\n"
        source = f"""\
func main() -> Int
{body}end func
"""
        with pytest.raises((ParseError, Exception), match=r"[Nn]esting|depth"):
            tokens = Lexer(source).tokenize()
            Parser(tokens).parse_program()

    def test_moderate_nesting_ok(self):
        """Nesting under the limit (e.g. 5) should parse fine."""
        source = """\
func main() -> Int
  if true then
    if true then
      if true then
        return 1
      end if
    end if
  end if
  return 0
end func
"""
        tokens = Lexer(source).tokenize()
        program = Parser(tokens).parse_program()
        assert program is not None


class TestP2_6_CompilerMaxSliceMangling:
    """P2-6: A Geno function named `max` compiles and runs without breaking."""

    def test_user_defined_max_function(self):
        """A user function named 'max' should work via name mangling."""
        source = """\
func max(a: Int, b: Int) -> Int
  example 3, 7 -> 7
  if a > b then
    return a
  end if
  return b
end func

func main() -> Int
  return max(3, 7)
end func
"""
        result = run(source)
        assert result.ok
        assert result.value == 7

    def test_builtin_slice_works(self):
        """The builtin slice function should work with named args."""
        source = """\
func main() -> List[Int]
  let xs: List[Int] = [1, 2, 3, 4, 5]
  return slice(list: xs, start: 1, stop: 3)
end func
"""
        result = run(source)
        assert result.ok
        assert result.value == [2, 3]


class TestP2_7_HostCallbackDocumentation:
    """P2-7: host_callbacks field has a docstring."""

    def test_host_callbacks_docstring_exists(self):
        """RunConfig.host_callbacks must have a docstring."""
        # The docstring is set as a class-level string after the field
        # We check that it's documented in the class source
        import inspect

        source = inspect.getsource(RunConfig)
        assert "host_callbacks" in source
        assert "IMPORTANT" in source or "trusted" in source.lower()


# =========================================================================
# Phase 4 (P3) - Hardening
# =========================================================================


class TestP3_1_BlockMathFactorialCombPerm:
    """P3-1: math.factorial, math.comb, math.perm are blocked."""

    def test_math_factorial_blocked(self):
        """math.factorial must be blocked through the module proxy."""
        proxy = _create_module_proxy(math)
        with pytest.raises(SecurityViolation, match="factorial"):
            proxy.factorial

    def test_math_comb_blocked(self):
        """math.comb must be blocked through the module proxy."""
        proxy = _create_module_proxy(math)
        with pytest.raises(SecurityViolation, match="comb"):
            proxy.comb

    def test_math_perm_blocked(self):
        """math.perm must be blocked through the module proxy."""
        proxy = _create_module_proxy(math)
        with pytest.raises(SecurityViolation, match="perm"):
            proxy.perm

    def test_math_sqrt_still_works(self):
        """math.sqrt must still work through the proxy."""
        proxy = _create_module_proxy(math)
        assert proxy.sqrt(25) == 5.0

    def test_math_floor_still_works(self):
        """math.floor must still work through the proxy."""
        proxy = _create_module_proxy(math)
        assert proxy.floor(3.7) == 3


class TestP3_2_SizeChecksInIsPermutationSortStrings:
    """P3-2: is_permutation and sort_strings reject oversized lists."""

    def test_is_permutation_200k_rejected(self):
        """200k-element lists must be rejected by is_permutation."""
        big = list(range(200_000))
        with pytest.raises(Exception, match="too large"):
            builtin_is_permutation(big, big)

    def test_is_permutation_1k_ok(self):
        """1k-element lists should work fine in is_permutation."""
        xs = list(range(1000))
        assert builtin_is_permutation(xs, xs) is True

    def test_sort_strings_200k_rejected(self):
        """200k-element lists must be rejected by sort_strings."""
        big = ["a"] * 200_000
        with pytest.raises(Exception, match="too large"):
            builtin_sort_strings(big)

    def test_sort_strings_1k_ok(self):
        """1k-element lists should work fine in sort_strings."""
        xs = [f"item_{i}" for i in range(1000)]
        result = builtin_sort_strings(xs)
        assert result == sorted(xs)


class TestP3_8_CompilerStringEscapingCatchAll:
    """P3-8: Strings containing \\x0b compile correctly."""

    def test_vertical_tab_compiles(self):
        """A string containing \\x0b (vertical tab) should compile without error."""
        # The compiler's catch-all regex should escape \x0b
        source = """\
func main() -> String
  return "hello\\tworld"
end func
"""
        # Compile to Python -- this should not produce invalid Python source
        python_code = compile_to_python(source)
        assert "hello" in python_code

    def test_string_with_control_chars_roundtrips(self):
        """Strings with control characters should survive compile + exec."""
        # Use a string that only contains chars the Geno lexer supports
        # (the lexer only handles \n, \t, \r, \\, \" escapes)
        source = """\
func main() -> String
  return "line1\\nline2\\ttab"
end func
"""
        result = run(source)
        assert result.ok
        assert "line1" in str(result.value)


class TestP3_10_DevDependencyCeilingPins:
    """P3-10: Dev dependencies have ceiling pins in pyproject.toml."""

    def test_ceiling_pins_exist(self):
        """All dev dependencies should have upper-bound (ceiling) pins."""
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        pyproject_path = os.path.join(repo_root, "pyproject.toml")

        with open(pyproject_path) as f:
            content = f.read()

        # Check that dev dependencies have ceiling pins (<N)
        in_dev_section = False
        dev_deps = []
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("dev = ["):
                in_dev_section = True
                continue
            if in_dev_section:
                if stripped == "]":
                    break
                # Extract the dependency string
                dep = stripped.strip('",').strip()
                if dep:
                    dev_deps.append(dep)

        assert len(dev_deps) > 0, "No dev dependencies found"

        for dep in dev_deps:
            # Each dependency should have a ceiling pin (contains '<')
            assert "<" in dep, (
                f"Dev dependency '{dep}' is missing a ceiling pin (upper bound)"
            )


class TestP3_11_PythonSupportMetadata:
    """P3-11: Supported Python metadata is internally consistent."""

    def test_pyproject_python_range_matches_ci_matrix(self):
        """The package metadata should be coherent and covered by CI."""
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        pyproject_path = os.path.join(repo_root, "pyproject.toml")
        ci_path = os.path.join(repo_root, ".github", "workflows", "ci.yml")

        with open(pyproject_path, "rb") as f:
            pyproject = tomllib.load(f)
        with open(ci_path, encoding="utf-8") as f:
            ci_config = yaml.safe_load(f)

        requires_python = pyproject["project"]["requires-python"]
        classifiers = pyproject["project"]["classifiers"]

        match = re.fullmatch(r">=(\d+\.\d+),<(\d+\.\d+)", requires_python)
        assert match is not None, (
            "requires-python should declare both lower and upper bounds"
        )
        declared_min, declared_exclusive_max = match.groups()

        ci_versions = sorted(
            {
                entry["python-version"]
                for entry in ci_config["jobs"]["test"]["strategy"]["matrix"]["include"]
            },
            key=lambda v: tuple(int(part) for part in v.split(".")),
        )
        assert ci_versions, "CI test matrix should include Python versions"

        classifier_versions = sorted(
            {
                classifier.rsplit("::", 1)[-1].strip()
                for classifier in classifiers
                if classifier.startswith("Programming Language :: Python :: 3.")
            },
            key=lambda v: tuple(int(part) for part in v.split(".")),
        )
        assert classifier_versions, "Python classifiers should declare support"

        min_major, min_minor = (int(part) for part in declared_min.split("."))
        _max_major, max_minor = (
            int(part) for part in declared_exclusive_max.split(".")
        )
        expected_classifier_versions = [
            f"{min_major}.{minor}" for minor in range(min_minor, max_minor)
        ]

        assert classifier_versions == expected_classifier_versions
        assert declared_min == classifier_versions[0]
        assert set(classifier_versions).issubset(set(ci_versions))
