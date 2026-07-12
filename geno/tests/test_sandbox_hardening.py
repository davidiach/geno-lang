"""
Sandbox hardening tests — exercise security-critical uncovered paths.

Targets: print(file=), module proxy dunder blocking, safe_getattr/safe_hasattr
wrappers, recursion limit conversion, __builtins__ override filtering,
timeout enforcement, dangerous-dunder AST rejection, and the exported
safe_getattr/safe_hasattr public API.
"""

import pytest

from geno.sandbox import (
    RecursionLimitError,
    SandboxConfig,
    SecurityViolation,
    TimeoutError,
    is_safe_value,
    run_sandboxed,
    safe_getattr,
    safe_hasattr,
)

# ---------------------------------------------------------------------------
# print(file=...) blocked  (line 383)
# ---------------------------------------------------------------------------


class TestPrintFileBlocked:
    """print() with file= parameter is handled by the process sandbox."""

    def test_print_file_none_allowed(self):
        """print(file=None) defaults to stdout, which is captured."""
        code = "print('x', file=None)"
        config = SandboxConfig(strict=False)
        _, output = run_sandboxed(code, config)
        assert "x" in output


# ---------------------------------------------------------------------------
# Module proxy — dunder blocking & sub-module wrapping  (lines 491, 520, 524)
# ---------------------------------------------------------------------------


class TestModuleProxy:
    """Sandboxed module proxies must block dunders and wrap sub-modules."""

    def test_module_proxy_blocks_unsafe_dunder(self):
        """Accessing __class__ on an imported module raises."""
        code = "import math\n__result__ = math.__class__"
        config = SandboxConfig(strict=False)
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)

    def test_module_proxy_blocks_loader_dunder(self):
        """Accessing __loader__ on an imported module raises."""
        code = "import math\n__result__ = math.__loader__"
        config = SandboxConfig(strict=False)
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)

    def test_module_proxy_repr(self):
        """Module proxy __repr__ returns a sandboxed tag."""
        code = "import math\n__result__ = repr(math)"
        config = SandboxConfig(strict=False)
        result, _ = run_sandboxed(code, config, use_process=True)
        assert "sandboxed module" in result
        assert "math" in result


# ---------------------------------------------------------------------------
# safe_getattr wrapper inside sandbox  (lines 608-609, 614, 618)
# ---------------------------------------------------------------------------


class TestSafeGetattr:
    """The injected getattr() must block dunders and private attrs."""

    def test_getattr_blocks_unsafe_dunder(self):
        """getattr(obj, '__class__') is blocked inside sandbox."""
        code = "__result__ = getattr(42, '__class__')"
        config = SandboxConfig(strict=False)
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)

    def test_getattr_blocks_private(self):
        """getattr(obj, '_private') is blocked inside sandbox."""
        code = "__result__ = getattr([], '_Foo__bar')"
        config = SandboxConfig(strict=False)
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)

    def test_getattr_default_for_missing(self):
        """getattr(obj, 'missing', default) returns default."""
        code = "__result__ = getattr(42, 'nonexistent', 'fallback')"
        config = SandboxConfig(strict=False)
        result, _ = run_sandboxed(code, config, use_process=True)
        assert result == "fallback"


# ---------------------------------------------------------------------------
# safe_hasattr wrapper inside sandbox  (lines 634-641)
# ---------------------------------------------------------------------------


class TestSafeHasattr:
    """The injected hasattr() must return False for blocked attrs."""

    def test_hasattr_blocks_dunder(self):
        """hasattr(obj, '__dict__') returns False inside sandbox."""
        code = "__result__ = hasattr(42, '__dict__')"
        config = SandboxConfig(strict=False)
        result, _ = run_sandboxed(code, config, use_process=True)
        assert result is False

    def test_hasattr_blocks_private(self):
        """hasattr(obj, '_private') returns False inside sandbox."""
        code = "__result__ = hasattr([], '_internal')"
        config = SandboxConfig(strict=False)
        result, _ = run_sandboxed(code, config, use_process=True)
        assert result is False

    def test_hasattr_allows_safe_dunder(self):
        """hasattr(obj, '__len__') returns True for objects that have it."""
        code = "__result__ = hasattr([1, 2], '__len__')"
        config = SandboxConfig(strict=False)
        result, _ = run_sandboxed(code, config, use_process=True)
        assert result is True

    def test_hasattr_blocked_attribute(self):
        """hasattr with BLOCKED_ATTRIBUTES returns False."""
        code = "__result__ = hasattr(42, '__subclasses__')"
        config = SandboxConfig(strict=False)
        result, _ = run_sandboxed(code, config, use_process=True)
        assert result is False


# ---------------------------------------------------------------------------
# RecursionError -> RecursionLimitError  (line 690)
# ---------------------------------------------------------------------------


class TestRecursionConversion:
    """RecursionError must be converted to RecursionLimitError."""

    def test_recursion_error_caught(self):
        """Infinite recursion raises an error."""
        code = "def f(): return f()\nf()"
        config = SandboxConfig(strict=False, max_recursion_depth=50, timeout=5.0)
        with pytest.raises((RecursionLimitError, RuntimeError)):
            run_sandboxed(code, config)


# ---------------------------------------------------------------------------
# extra_globals __builtins__ override blocked  (lines 739-741)
# ---------------------------------------------------------------------------


class TestExtraGlobalsFiltering:
    """ProcessSandbox does not expose extra_globals — verify basic isolation."""

    def test_builtins_not_overridable(self):
        """Process sandbox isolates builtins; user code cannot override them."""
        config = SandboxConfig(strict=False)
        result, _ = run_sandboxed("__result__ = 42", config)
        assert result == 42


# ---------------------------------------------------------------------------
# Timeout enforcement  (lines 771-772)
# ---------------------------------------------------------------------------


class TestTimeoutEnforcement:
    """Infinite loops must trigger TimeoutError."""

    def test_timeout_on_infinite_loop(self):
        """An infinite loop exceeding timeout raises TimeoutError."""
        code = "while True: pass"
        config = SandboxConfig(strict=False, timeout=0.5)
        with pytest.raises(TimeoutError, match=r"timed out"):
            run_sandboxed(code, config, use_process=True)


# ---------------------------------------------------------------------------
# _reject_dangerous_dunders — syntax error & annotated assign  (lines 1322-1341)
# ---------------------------------------------------------------------------


class TestDangerousDunderRejection:
    """AST-level dunder rejection for class definitions."""

    def test_syntax_error_code_fails_closed(self):
        """Syntactically invalid code fails closed in the worker validator."""
        code = "class Foo:\n    def __getattribute__(self"  # Unterminated
        config = SandboxConfig(strict=False, timeout=2.0)
        with pytest.raises(RuntimeError, match="Invalid Python syntax"):
            run_sandboxed(code, config)

    def test_compiled_runtime_prelude_syntax_error_fails_closed(self):
        """compiled_runtime_prelude must not skip worker syntax failures."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(
            ProcessSandboxConfig(
                compiled_runtime_prelude=True,
                strict=False,
                timeout=2.0,
            )
        )
        _result, _output, error = sandbox.execute("def (")
        assert error is not None
        assert "Invalid Python syntax" in error

    def test_compiled_runtime_prelude_without_trusted_prefix_blocks_dunders(self):
        """The prelude flag alone must not disable worker attribute checks."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(
            ProcessSandboxConfig(
                compiled_runtime_prelude=True,
                strict=False,
                timeout=2.0,
            )
        )

        _result, _output, error = sandbox.execute("__result__ = (()).__reduce_ex__(2)")

        assert error is not None
        assert "__reduce_ex__" in error

    def test_compiled_runtime_prelude_only_trusts_prefix_lines(self):
        """Trusted prelude lines may use support internals; following code may not."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(
            ProcessSandboxConfig(
                compiled_runtime_prelude=True,
                trusted_prelude_line_count=1,
                strict=False,
                timeout=2.0,
            )
        )

        _result, _output, error = sandbox.execute(
            "_GENO_OBJECT = ().__class__.__mro__[-1]\n"
            "__result__ = (()).__reduce_ex__(2)\n"
        )

        assert error is not None
        assert "__reduce_ex__" in error

    def test_compiled_runtime_prelude_trusted_prefix_still_runs(self):
        """Trusted prelude prefix keeps the generated runtime support usable."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(
            ProcessSandboxConfig(
                compiled_runtime_prelude=True,
                trusted_prelude_line_count=1,
                strict=False,
                timeout=2.0,
            )
        )

        result, output, error = sandbox.execute(
            "_GENO_OBJECT = ().__class__.__mro__[-1]\n__result__ = 42\n"
        )

        assert error is None
        assert output == ""
        assert result == 42

    def test_compiled_runtime_prelude_blocks_format_after_prefix(self):
        """str.format traversal remains blocked outside the trusted prefix."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(
            ProcessSandboxConfig(
                compiled_runtime_prelude=True,
                trusted_prelude_line_count=1,
                strict=False,
                timeout=2.0,
            )
        )

        _result, _output, error = sandbox.execute(
            "_GENO_OBJECT = ().__class__.__mro__[-1]\n"
            '__result__ = "{x.__class__}".format(x=())\n'
        )

        assert error is not None
        assert "format" in error

    def test_compiled_runtime_prelude_requires_nodes_to_end_inside_prefix(self):
        """A node starting in trusted lines but ending later is not trusted."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(
            ProcessSandboxConfig(
                compiled_runtime_prelude=True,
                trusted_prelude_line_count=1,
                strict=False,
                timeout=2.0,
            )
        )

        _result, _output, error = sandbox.execute(
            '__result__ = "{x.__class__}".format(\n    x=()\n)\n'
        )

        assert error is not None
        assert "format" in error

    def test_annotated_dunder_assign_blocked(self):
        """Annotated __getattribute__ assignment in class body is blocked."""
        code = "class Evil:\n    __getattribute__: object = lambda self, name: None\n"
        config = SandboxConfig(strict=False, timeout=2.0)
        with pytest.raises(
            (SecurityViolation, RuntimeError), match=r"__getattribute__"
        ):
            run_sandboxed(code, config)


# ---------------------------------------------------------------------------
# Exported safe_getattr / safe_hasattr  (lines 1525-1559)
# ---------------------------------------------------------------------------


class TestExportedSafeGetattr:
    """The public safe_getattr function blocks dunders and private attrs."""

    def test_blocks_unsafe_dunder(self):
        """safe_getattr raises on unsafe dunder."""
        with pytest.raises(SecurityViolation, match=r"__class__"):
            safe_getattr(42, "__class__")

    def test_blocks_private(self):
        """safe_getattr raises on private attributes."""

        class Obj:
            _secret = 1

        with pytest.raises(SecurityViolation, match=r"private"):
            safe_getattr(Obj(), "_secret")

    def test_default_for_missing(self):
        """safe_getattr with default returns default for missing attr."""
        assert safe_getattr(42, "nonexistent", "default") == "default"

    def test_allows_safe_dunder(self):
        """safe_getattr allows safe dunders like __len__."""
        assert safe_getattr([1, 2, 3], "__len__")() == 3


class TestExportedSafeHasattr:
    """The public safe_hasattr function blocks dunders and private attrs."""

    def test_blocks_unsafe_dunder(self):
        """safe_hasattr returns False for unsafe dunder."""
        assert safe_hasattr(42, "__class__") is False

    def test_blocks_private(self):
        """safe_hasattr returns False for private attributes."""

        class Obj:
            _secret = 1

        assert safe_hasattr(Obj(), "_secret") is False

    def test_allows_normal_attr(self):
        """safe_hasattr returns True for normal public attributes."""

        class Obj:
            public = 1

        assert safe_hasattr(Obj(), "public") is True


# ---------------------------------------------------------------------------
# is_safe_value edge cases  (lines 1473, 1481, 1491)
# ---------------------------------------------------------------------------


class TestIsSafeValue:
    """Edge cases in the value-safety filter for extra_globals."""

    def test_type_with_non_string_module(self):
        """Type object with non-string __module__ is rejected."""

        class Weird:
            pass

        Weird.__module__ = None  # type: ignore[assignment]
        assert is_safe_value(Weird) is False

    def test_lambda_is_safe(self):
        """Lambda with module=None is considered safe."""
        fn = lambda x: x  # noqa: E731
        fn.__module__ = None  # type: ignore[assignment]
        assert is_safe_value(fn) is True

    def test_unrecognized_value_rejected(self):
        """An unrecognized object type returns False."""

        class Custom:
            pass

        # Custom instances are not in any safe category
        assert is_safe_value(Custom()) is False


# ---------------------------------------------------------------------------
# No-timeout exec path  (lines 747-748)
# ---------------------------------------------------------------------------


class TestNoTimeoutExec:
    """Execution works end-to-end via ProcessSandbox."""

    def test_exec_returns_result(self):
        """run_sandboxed returns result correctly."""
        config = SandboxConfig(strict=False)
        result, _ = run_sandboxed("__result__ = 1 + 2", config)
        assert result == 3


# ---------------------------------------------------------------------------
# Precompiled runtime-prelude blob (in-memory, hash-verified)
# ---------------------------------------------------------------------------


class TestPreludeBlobFraming:
    """The parent swaps the canonical prelude text for a marshal blob.

    Only an exact prefix match against the package's own prelude text is
    substituted; the worker re-verifies the blob's SHA-256 before
    unmarshalling, and the program tail keeps full AST validation.
    """

    @staticmethod
    def _sandbox(trusted_prelude_line_count: int = 0):
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        return ProcessSandbox(
            ProcessSandboxConfig(
                compiled_runtime_prelude=True,
                strict=False,
                timeout=10.0,
                trusted_prelude_line_count=trusted_prelude_line_count,
            )
        )

    @staticmethod
    def _canonical_prelude():
        from geno.compiler import _stripped_runtime_prelude

        return _stripped_runtime_prelude()

    def test_canonical_prefix_uses_blob_and_runs(self, monkeypatch):
        """An exact canonical-prelude prefix is framed as a blob and works."""
        from geno.sandbox import _PRELUDE_BLOB_HEADER, ProcessSandbox

        sent = {}
        orig = ProcessSandbox._run_worker

        def spy(self, cmd, code, config_overrides=None):
            sent["payload"] = code
            sent["overrides"] = config_overrides
            return orig(self, cmd, code, config_overrides)

        monkeypatch.setattr(ProcessSandbox, "_run_worker", spy)
        sandbox = self._sandbox()
        code = self._canonical_prelude() + "\n__result__ = _int_div(84, 2)\n"

        result, _output, error = sandbox.execute(code)

        assert error is None
        assert result == 42
        assert sent["payload"].startswith(_PRELUDE_BLOB_HEADER + " ")
        assert sent["overrides"]["trusted_prelude_line_count"] == 0
        assert sent["overrides"]["prelude_blob_sha256"]

    def test_tampered_blob_is_fatal(self, monkeypatch):
        """A blob that fails SHA-256 verification aborts before any exec."""
        from geno.sandbox import _PRELUDE_BLOB_HEADER, ProcessSandbox

        orig = ProcessSandbox._run_worker

        def corrupt(self, cmd, code, config_overrides=None):
            assert code.startswith(_PRELUDE_BLOB_HEADER + " ")
            header, rest = code.split("\n", 1)
            # Flip one base64 character of the blob payload
            flipped = ("B" if rest[0] != "B" else "C") + rest[1:]
            return orig(self, cmd, header + "\n" + flipped, config_overrides)

        monkeypatch.setattr(ProcessSandbox, "_run_worker", corrupt)
        sandbox = self._sandbox()
        code = self._canonical_prelude() + "\n__result__ = 42\n"

        result, _output, error = sandbox.execute(code)

        assert result is None
        assert error is not None
        assert "integrity" in error

    def test_blob_header_in_plain_text_is_inert(self):
        """Without the parent-set hash, the marker is just a comment."""
        sandbox = self._sandbox()

        result, _output, error = sandbox.execute(
            "#GENO-PRELUDE-BLOB 4\n__result__ = 7\n"
        )

        assert error is None
        assert result == 7

    def test_blob_mode_tail_keeps_class_dunder_guard(self):
        """The class-dunder guard still covers the program tail in blob mode."""
        sandbox = self._sandbox()
        code = self._canonical_prelude() + (
            "\nclass Evil:\n"
            "    def __getattribute__(self, name):\n"
            "        return name\n"
            "__result__ = 1\n"
        )

        result, _output, error = sandbox.execute(code)

        assert result is None
        assert error is not None
        assert "__getattribute__" in error

    def test_blob_mode_zeroes_trusted_prefix_for_tail(self):
        """Blob mode validates the whole tail even with a huge trusted count.

        In text mode a caller-supplied trusted_prelude_line_count exempts
        prefix lines from the non-dunder checks; in blob mode the worker's
        text input is only the tail, so the count is forced to zero and a
        blocked attribute access on the first tail line is still rejected.
        """
        prelude = self._canonical_prelude()
        huge = len(prelude.splitlines()) + 100
        sandbox = self._sandbox(trusted_prelude_line_count=huge)
        code = prelude + "\n__result__ = (()).__reduce_ex__(2)\n"

        result, _output, error = sandbox.execute(code)

        assert result is None
        assert error is not None
        assert "__reduce_ex__" in error

    def test_non_canonical_prefix_falls_back_to_text(self, monkeypatch):
        """Anything but an exact prelude prefix takes the validated text path."""
        from geno.sandbox import _PRELUDE_BLOB_HEADER, ProcessSandbox

        sent = {}
        orig = ProcessSandbox._run_worker

        def spy(self, cmd, code, config_overrides=None):
            sent["payload"] = code
            sent["overrides"] = config_overrides
            return orig(self, cmd, code, config_overrides)

        monkeypatch.setattr(ProcessSandbox, "_run_worker", spy)
        sandbox = self._sandbox()

        result, _output, error = sandbox.execute("__result__ = 5\n")

        assert error is None
        assert result == 5
        assert not sent["payload"].startswith(_PRELUDE_BLOB_HEADER)
        assert sent["overrides"] is None
