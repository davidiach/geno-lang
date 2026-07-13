"""
Dedicated tests for sandbox.py — configuration options, blocked operations,
and security boundaries not covered by test_security.py.

Focuses on: SandboxConfig toggles, blocked builtins coverage, blocked module
coverage, output buffer management, and safe_getattr/safe_hasattr.
"""

import importlib.util
import json
import os

import pytest

import geno.sandbox as sandbox_module
from geno.execution_limits import DEFAULT_INTERPRETER_MAX_STEPS
from geno.sandbox import (
    SAFE_BUILTINS,
    ProcessSandbox,
    ProcessSandboxConfig,
    ResourceLimitExceeded,
    SandboxConfig,
    SecurityViolation,
    _sandbox_config_to_process_config,
    create_safe_globals,
    is_safe_value,
    run_sandboxed,
    safe_getattr,
    safe_hasattr,
    validate_code_safety,
)

# ---------------------------------------------------------------------------
# SandboxConfig.allow_print
# ---------------------------------------------------------------------------


class TestAllowPrint:
    """Print behaviour in process sandbox."""

    def test_print_captures_output(self):
        config = SandboxConfig(strict=False)
        code = "__result__ = 'ok'\nprint('hello')"
        result, output = run_sandboxed(code, config)
        assert result == "ok"
        assert "hello" in output

    def test_print_disabled_raises(self):
        config = SandboxConfig(strict=False, allow_print=False)
        code = "__result__ = 'ok'\nprint('hello')"
        with pytest.raises(RuntimeError, match="print\\(\\) is disabled"):
            run_sandboxed(code, config)


# ---------------------------------------------------------------------------
# Output length limits
# ---------------------------------------------------------------------------


class TestOutputLimits:
    def test_output_exceeds_limit_raises(self):
        config = SandboxConfig(strict=False, max_output_length=10)
        code = "print('x' * 100)"
        with pytest.raises((ResourceLimitExceeded, RuntimeError)):
            run_sandboxed(code, config)


# ---------------------------------------------------------------------------
# SandboxConfig validation
# ---------------------------------------------------------------------------


class TestSandboxConfigValidation:
    def test_sandbox_config_default_has_step_budget(self):
        cfg = SandboxConfig()
        assert cfg.max_steps == DEFAULT_INTERPRETER_MAX_STEPS

    def test_sandbox_config_allows_documented_sentinels(self):
        cfg = SandboxConfig(
            timeout=None,
            max_memory_bytes=None,
            max_cpu_time=None,
            max_file_size_bytes=0,
            max_processes=1,
            max_output_length=0,
            max_collection_size=0,
            max_steps=None,
            strict=False,
        )
        assert cfg.timeout is None
        assert cfg.max_memory_bytes is None
        assert cfg.max_cpu_time is None
        assert cfg.max_file_size_bytes == 0
        assert cfg.max_processes == 1
        assert cfg.max_output_length == 0
        assert cfg.max_collection_size == 0
        assert cfg.max_steps is None

    @pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf")])
    def test_sandbox_config_rejects_invalid_timeout(self, timeout):
        with pytest.raises(ValueError, match=r"SandboxConfig\.timeout"):
            SandboxConfig(timeout=timeout)

    @pytest.mark.parametrize("max_memory_bytes", [0, -1, True, 1.5])
    def test_sandbox_config_rejects_invalid_memory_limit(self, max_memory_bytes):
        with pytest.raises(ValueError, match=r"SandboxConfig\.max_memory_bytes"):
            SandboxConfig(max_memory_bytes=max_memory_bytes)

    @pytest.mark.parametrize("max_cpu_time", [0, -1, True, float("nan"), float("inf")])
    def test_sandbox_config_rejects_invalid_cpu_limit(self, max_cpu_time):
        with pytest.raises(ValueError, match=r"SandboxConfig\.max_cpu_time"):
            SandboxConfig(max_cpu_time=max_cpu_time)

    @pytest.mark.parametrize("max_file_size_bytes", [-1, True, 1.5])
    def test_sandbox_config_rejects_invalid_file_limit(self, max_file_size_bytes):
        with pytest.raises(ValueError, match=r"SandboxConfig\.max_file_size_bytes"):
            SandboxConfig(max_file_size_bytes=max_file_size_bytes)

    @pytest.mark.parametrize("max_processes", [0, -1, True, 1.5])
    def test_sandbox_config_rejects_invalid_process_limit(self, max_processes):
        with pytest.raises(ValueError, match=r"SandboxConfig\.max_processes"):
            SandboxConfig(max_processes=max_processes)

    @pytest.mark.parametrize("max_steps", [0, -1, True, 1.5])
    def test_sandbox_config_rejects_invalid_max_steps(self, max_steps):
        with pytest.raises(ValueError, match=r"SandboxConfig\.max_steps"):
            SandboxConfig(max_steps=max_steps)

    @pytest.mark.parametrize("max_recursion_depth", [0, -1, True, 1.5])
    def test_sandbox_config_rejects_invalid_recursion_depth(self, max_recursion_depth):
        with pytest.raises(ValueError, match=r"SandboxConfig\.max_recursion_depth"):
            SandboxConfig(max_recursion_depth=max_recursion_depth)

    @pytest.mark.parametrize("max_output_length", [-1, True, 1.5])
    def test_sandbox_config_rejects_invalid_output_length(self, max_output_length):
        with pytest.raises(ValueError, match=r"SandboxConfig\.max_output_length"):
            SandboxConfig(max_output_length=max_output_length)

    @pytest.mark.parametrize("max_collection_size", [-1, True, 1.5])
    def test_sandbox_config_rejects_invalid_collection_size(self, max_collection_size):
        with pytest.raises(ValueError, match=r"SandboxConfig\.max_collection_size"):
            SandboxConfig(max_collection_size=max_collection_size)

    @pytest.mark.parametrize("max_integer_bits", [0, -1, True, 1.5])
    def test_sandbox_config_rejects_invalid_integer_bits(self, max_integer_bits):
        with pytest.raises(ValueError, match=r"SandboxConfig\.max_integer_bits"):
            SandboxConfig(max_integer_bits=max_integer_bits)

    @pytest.mark.parametrize(
        ("field", "kwargs"),
        [
            ("SandboxConfig.allow_print", {"allow_print": "yes"}),
            ("SandboxConfig.strict", {"strict": 1}),
            ("SandboxConfig.compiled_runtime_prelude", {"compiled_runtime_prelude": 1}),
            (
                "SandboxConfig.trusted_prelude_line_count",
                {"trusted_prelude_line_count": -1},
            ),
            (
                "SandboxConfig.trusted_prelude_line_count",
                {"trusted_prelude_line_count": True},
            ),
            (
                "SandboxConfig.trusted_prelude_line_count",
                {"trusted_prelude_line_count": 1.5},
            ),
        ],
    )
    def test_sandbox_config_rejects_invalid_values(self, field, kwargs):
        with pytest.raises(ValueError, match=field):
            SandboxConfig(**kwargs)


class TestProcessSandboxConfigValidation:
    def test_process_sandbox_config_allows_documented_sentinels(self):
        cfg = ProcessSandboxConfig(
            timeout=None,
            max_memory_bytes=None,
            max_cpu_time=None,
            max_file_size_bytes=0,
            max_output_length=0,
            max_collection_size=0,
            strict=False,
        )
        assert cfg.timeout is None
        assert cfg.max_memory_bytes is None
        assert cfg.max_cpu_time is None
        assert cfg.max_file_size_bytes == 0
        assert cfg.max_output_length == 0
        assert cfg.max_collection_size == 0

    @pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf")])
    def test_process_sandbox_config_rejects_invalid_timeout(self, timeout):
        with pytest.raises(ValueError, match=r"ProcessSandboxConfig\.timeout"):
            ProcessSandboxConfig(timeout=timeout)

    @pytest.mark.parametrize("max_memory_bytes", [0, -1, True, 1.5])
    def test_process_sandbox_config_rejects_invalid_memory_limit(
        self, max_memory_bytes
    ):
        with pytest.raises(ValueError, match=r"ProcessSandboxConfig\.max_memory_bytes"):
            ProcessSandboxConfig(max_memory_bytes=max_memory_bytes)

    @pytest.mark.parametrize("max_cpu_time", [0, -1, True, float("nan"), float("inf")])
    def test_process_sandbox_config_rejects_invalid_cpu_limit(self, max_cpu_time):
        with pytest.raises(ValueError, match=r"ProcessSandboxConfig\.max_cpu_time"):
            ProcessSandboxConfig(max_cpu_time=max_cpu_time)

    @pytest.mark.parametrize("max_file_size_bytes", [-1, True, 1.5])
    def test_process_sandbox_config_rejects_invalid_file_limit(
        self, max_file_size_bytes
    ):
        with pytest.raises(
            ValueError, match=r"ProcessSandboxConfig\.max_file_size_bytes"
        ):
            ProcessSandboxConfig(max_file_size_bytes=max_file_size_bytes)

    @pytest.mark.parametrize("max_processes", [0, -1, True, 1.5])
    def test_process_sandbox_config_rejects_invalid_process_limit(self, max_processes):
        with pytest.raises(ValueError, match=r"ProcessSandboxConfig\.max_processes"):
            ProcessSandboxConfig(max_processes=max_processes)

    @pytest.mark.parametrize("trusted_prefix", [-1, True, 1.5])
    def test_process_sandbox_config_rejects_invalid_trusted_prefix(
        self, trusted_prefix
    ):
        with pytest.raises(
            ValueError, match=r"ProcessSandboxConfig\.trusted_prelude_line_count"
        ):
            ProcessSandboxConfig(trusted_prelude_line_count=trusted_prefix)

    def test_fractional_cpu_limit_rounds_up_for_worker_env(self):
        env = ProcessSandbox(
            ProcessSandboxConfig(max_cpu_time=0.5)
        )._create_restricted_env()
        worker_config = json.loads(env["GENO_SANDBOX_CONFIG"])
        assert worker_config["max_cpu_time"] == 1

    def test_trusted_prelude_line_count_forwarded_to_worker_env(self):
        env = ProcessSandbox(
            ProcessSandboxConfig(
                compiled_runtime_prelude=True,
                trusted_prelude_line_count=7,
            )
        )._create_restricted_env()
        worker_config = json.loads(env["GENO_SANDBOX_CONFIG"])
        assert worker_config["trusted_prelude_line_count"] == 7

    def test_windows_worker_env_preserves_python_startup_vars(self, monkeypatch):
        required = {
            "SystemRoot": r"C:\Windows",
            "WINDIR": r"C:\Windows",
            "COMSPEC": r"C:\Windows\System32\cmd.exe",
            "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        }
        for name, value in required.items():
            monkeypatch.setenv(name, value)

        sandbox = ProcessSandbox(ProcessSandboxConfig(compiled_runtime_prelude=True))
        monkeypatch.setattr(sandbox, "system", "Windows")

        env = sandbox._create_restricted_env()

        for name, value in required.items():
            assert env[name] == value
        assert env["PATH"] == os.environ["PATH"]

    def test_sandbox_config_to_process_config_forwards_resource_limits(self):
        sandbox_config = SandboxConfig(
            timeout=2.5,
            max_memory_bytes=64 * 1024 * 1024,
            max_cpu_time=1.5,
            max_file_size_bytes=1024,
            max_processes=2,
            max_recursion_depth=42,
            max_output_length=123,
            allow_print=False,
            max_collection_size=456,
            max_integer_bits=789,
            strict=False,
            compiled_runtime_prelude=True,
            trusted_prelude_line_count=3,
        )

        process_config = _sandbox_config_to_process_config(sandbox_config)

        assert process_config.timeout == 2.5
        assert process_config.max_memory_bytes == 64 * 1024 * 1024
        assert process_config.max_cpu_time == 1.5
        assert process_config.max_file_size_bytes == 1024
        assert process_config.max_processes == 2
        assert process_config.max_recursion_depth == 42
        assert process_config.max_output_length == 123
        assert process_config.allow_print is False
        assert process_config.max_collection_size == 456
        assert process_config.max_integer_bits == 789
        assert process_config.strict is False
        assert process_config.compiled_runtime_prelude is True
        assert process_config.trusted_prelude_line_count == 3


# ---------------------------------------------------------------------------
# Blocked builtins — test they raise when *called*
# ---------------------------------------------------------------------------


class TestBlockedBuiltins:
    """Blocked builtins are replaced with functions that raise on call."""

    @pytest.mark.parametrize(
        "name",
        [
            "eval",
            "exec",
            "compile",
            "open",
            "input",
            "globals",
            "locals",
            "setattr",
            "delattr",
            "dir",
        ],
    )
    def test_blocked_builtin_raises_on_call(self, name):
        config = SandboxConfig(strict=False)
        # Call the builtin — should raise SecurityViolation
        code = f"{name}()"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)

    def test_import_builtin_blocked_for_dangerous_module(self):
        config = SandboxConfig(strict=False)
        code = "__import__('os')"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)


# ---------------------------------------------------------------------------
# Blocked modules
# ---------------------------------------------------------------------------


class TestBlockedModules:
    """Critical blocked modules should raise on import."""

    @pytest.mark.parametrize(
        "module",
        [
            "os",
            "sys",
            "subprocess",
            "socket",
            "http",
            "pickle",
            "ctypes",
            "importlib",
            "multiprocessing",
            "tempfile",
            "shutil",
            "signal",
        ],
    )
    def test_blocked_module_raises(self, module):
        config = SandboxConfig(strict=False)
        code = f"import {module}"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)


# ---------------------------------------------------------------------------
# Crypto modules — allowed for legitimate use (hashlib, hmac, secrets)
# ---------------------------------------------------------------------------


class TestCryptoModulesAllowed:
    """hashlib, hmac, and secrets are safe to use inside the sandbox."""

    def test_hashlib_sha256(self):
        code = "import hashlib\n__result__ = hashlib.sha256(b'hello').hexdigest()"
        result, _ = run_sandboxed(code, SandboxConfig(strict=False))
        assert (
            result == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        )

    def test_hmac_signing(self):
        code = (
            "import hmac, hashlib\n"
            "__result__ = hmac.new(b'key', b'msg', hashlib.sha256).hexdigest()"
        )
        result, _ = run_sandboxed(code, SandboxConfig(strict=False))
        assert isinstance(result, str) and len(result) == 64

    def test_secrets_token_hex(self):
        code = "import secrets\n__result__ = len(secrets.token_hex(16))"
        result, _ = run_sandboxed(code, SandboxConfig(strict=False))
        assert result == 32

    def test_cryptography_still_blocked(self):
        code = "import cryptography"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, SandboxConfig(strict=False))


class TestProcessImportAllowlist:
    """The process worker should honor the shared safe-import allowlist."""

    @pytest.mark.parametrize(
        "code",
        [
            "from collections import deque\n__result__ = deque([1, 2]).pop() == 2",
            (
                "import collections.abc\n"
                "__result__ = hasattr(collections.abc, 'Mapping')"
            ),
            "import abc\n__result__ = abc.ABC.__name__ == 'ABC'",
            (
                "import itertools\n"
                "__result__ = list(itertools.islice([1, 2, 3], 2)) == [1, 2]"
            ),
        ],
    )
    def test_process_worker_allows_shared_stdlib_allowlist(self, code):
        result, _ = run_sandboxed(code, SandboxConfig(strict=False))
        assert result is True

    def test_process_worker_blocks_enum_import(self):
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed("import enum", SandboxConfig(strict=False))

    @pytest.mark.skipif(
        importlib.util.find_spec("typing_extensions") is None,
        reason="typing_extensions is optional",
    )
    def test_process_worker_allows_typing_extensions(self):
        code = (
            "import typing_extensions\n"
            "__result__ = hasattr(typing_extensions, 'Protocol')"
        )
        result, _ = run_sandboxed(code, SandboxConfig(strict=False))
        assert result is True

    @pytest.mark.skipif(
        importlib.util.find_spec("tomllib") is None,
        reason="tomllib is available on Python 3.11+",
    )
    def test_process_worker_allows_tomllib(self):
        code = (
            "import tomllib\n__result__ = tomllib.loads('answer = 42')['answer'] == 42"
        )
        result, _ = run_sandboxed(code, SandboxConfig(strict=False))
        assert result is True

    @pytest.mark.skipif(
        importlib.util.find_spec("tomli") is None,
        reason="tomli is optional when tomllib is available",
    )
    def test_process_worker_allows_tomli_when_installed(self):
        code = "import tomli\n__result__ = tomli.loads('answer = 42')['answer'] == 42"
        result, _ = run_sandboxed(code, SandboxConfig(strict=False))
        assert result is True


class TestCryptoModuleEscapeAttempts:
    """Attribute-chain escape attempts through crypto modules are blocked."""

    def test_hashlib_private_attr_blocked(self):
        code = "import hashlib\n__result__ = hashlib._hashlib"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, SandboxConfig(strict=False))

    def test_secrets_private_attr_blocked(self):
        code = "import secrets\n__result__ = secrets._sysrand"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, SandboxConfig(strict=False))

    def test_secrets_class_dunder_blocked(self):
        code = "import secrets\n__result__ = secrets.__class__"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, SandboxConfig(strict=False))

    def test_hmac_dunder_blocked(self):
        code = "import hmac\n__result__ = hmac.__dict__"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, SandboxConfig(strict=False))

    def test_hashlib_scrypt_blocked(self):
        code = (
            "import hashlib\n"
            "__result__ = hashlib.scrypt(b'pass', salt=b'salt', n=2, r=1, p=1)"
        )
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, SandboxConfig(strict=False))

    def test_hashlib_pbkdf2_hmac_blocked(self):
        code = (
            "import hashlib\n"
            "__result__ = hashlib.pbkdf2_hmac('sha256', b'pass', b'salt', 1)"
        )
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, SandboxConfig(strict=False))


class TestCryptoModuleResourceLimits:
    """Crypto helpers must still respect sandbox collection limits."""

    def test_secrets_token_hex_honors_collection_limit(self):
        code = "import secrets\n__result__ = secrets.token_hex(20)"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(
                code,
                SandboxConfig(strict=False, max_collection_size=16),
            )

    def test_hashlib_shake_hexdigest_honors_collection_limit(self):
        code = "import hashlib\n__result__ = hashlib.shake_128(b'x').hexdigest(20)"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(
                code,
                SandboxConfig(strict=False, max_collection_size=16),
            )

    def test_hashlib_new_shake_digest_honors_collection_limit(self):
        code = "import hashlib\n__result__ = hashlib.new('shake_128', b'x').digest(20)"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(
                code,
                SandboxConfig(strict=False, max_collection_size=16),
                use_process=False,
            )


# ---------------------------------------------------------------------------
# Safe builtins structure
# ---------------------------------------------------------------------------


class TestSafeBuiltins:
    """Verify the safe builtins dict has the expected structure."""

    def test_safe_builtins_includes_core_types(self):
        for name in ["int", "float", "str", "list", "dict", "bool"]:
            assert name in SAFE_BUILTINS

    def test_safe_builtins_excludes_dangerous(self):
        for name in ["eval", "exec", "compile", "__import__", "open"]:
            assert name not in SAFE_BUILTINS

    def test_type_single_arg_allowed(self):
        config = SandboxConfig(strict=False)
        code = "__result__ = type(42).__name__"
        result, _ = run_sandboxed(code, config)
        assert result == "int"

    def test_type_multi_arg_blocked(self):
        config = SandboxConfig(strict=False)
        code = "Evil = type('Evil', (), {})"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)


# ---------------------------------------------------------------------------
# safe_getattr / safe_hasattr
# ---------------------------------------------------------------------------


class TestSafeAttrAccess:
    """safe_getattr must block dangerous attribute names."""

    def test_safe_getattr_blocks_dunder_class(self):
        with pytest.raises(SecurityViolation):
            safe_getattr("hello", "__class__")

    def test_safe_getattr_blocks_dunder_globals(self):
        def f():
            pass

        with pytest.raises(SecurityViolation):
            safe_getattr(f, "__globals__")

    def test_safe_getattr_allows_normal_attrs(self):
        assert safe_getattr("hello", "upper")() == "HELLO"

    def test_safe_hasattr_returns_false_for_dangerous(self):
        # safe_hasattr returns False (not raises) for blocked attrs
        assert safe_hasattr("hello", "__class__") is False
        assert safe_hasattr("hello", "__globals__") is False

    def test_safe_hasattr_returns_true_for_normal(self):
        assert safe_hasattr("hello", "upper") is True


# ---------------------------------------------------------------------------
# validate_code_safety
# ---------------------------------------------------------------------------


class TestCodeValidation:
    """Static code validation catches obvious dangerous patterns."""

    def test_import_os_detected(self):
        issues = validate_code_safety("import os")
        assert len(issues) > 0

    def test_safe_code_passes(self):
        issues = validate_code_safety("x = 1 + 2")
        assert len(issues) == 0


# ---------------------------------------------------------------------------
# is_safe_value
# ---------------------------------------------------------------------------


class TestIsSafeValue:
    """is_safe_value should accept primitives and reject dangerous types."""

    def test_primitives_safe(self):
        assert is_safe_value(42) is True
        assert is_safe_value(3.14) is True
        assert is_safe_value("hello") is True
        assert is_safe_value(True) is True
        assert is_safe_value(None) is True

    def test_collections_safe(self):
        assert is_safe_value([1, 2, 3]) is True
        assert is_safe_value({"a": 1}) is True
        assert is_safe_value((1, 2)) is True


# ---------------------------------------------------------------------------
# SandboxContext
# ---------------------------------------------------------------------------


class TestSandboxContext:
    """Sandbox execute and output behaviour (via ProcessSandbox)."""

    def test_execute_returns_result(self):
        config = SandboxConfig(strict=False)
        result, _ = run_sandboxed("__result__ = 42", config)
        assert result == 42

    def test_output_captured(self):
        config = SandboxConfig(strict=False)
        _, output = run_sandboxed("print('hello')", config)
        assert "hello" in output


class TestRunSandboxedCompatibility:
    """Compatibility shims must preserve SandboxConfig semantics."""

    def test_timeout_none_forwarded_to_process_config(self, monkeypatch):
        captured: dict[str, ProcessSandboxConfig] = {}

        def fake_run_in_process(code, config: ProcessSandboxConfig):
            captured["config"] = config
            return (42, "")

        monkeypatch.setattr(sandbox_module, "run_in_process", fake_run_in_process)

        result, output = run_sandboxed(
            "__result__ = 42", SandboxConfig(strict=False, timeout=None)
        )

        assert result == 42
        assert output == ""
        process_config = captured["config"]
        assert process_config.timeout is None


# ---------------------------------------------------------------------------
# create_safe_globals
# ---------------------------------------------------------------------------


class TestCreateSafeGlobals:
    """Verify safe globals dict has the right structure."""

    def test_includes_safe_builtins(self):
        config = SandboxConfig(strict=False)
        output_buffer: list = []
        globals_dict = create_safe_globals(config, output_buffer)
        builtins = globals_dict["__builtins__"]
        assert "int" in builtins
        assert "str" in builtins
        assert "print" in builtins  # print should be present (as safe_print)

    def test_excludes_dangerous_builtins_from_safe_dict(self):
        config = SandboxConfig(strict=False)
        output_buffer: list = []
        globals_dict = create_safe_globals(config, output_buffer)
        builtins = globals_dict["__builtins__"]
        # Blocked builtins should exist as callable stubs that raise
        for name in ["eval", "exec", "compile", "open"]:
            if name in builtins:
                with pytest.raises(SecurityViolation):
                    builtins[name]()
