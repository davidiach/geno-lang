"""
Security Tests for Geno
=======================

Tests that the sandbox properly restricts dangerous operations.
"""

import importlib
import io
import os
import subprocess
import sys
import time
from typing import Any, cast

import pytest

from geno.compiler import compile_and_exec
from geno.interpreter import Interpreter
from geno.sandbox import (
    ResourceLimitExceeded,
    SandboxConfig,
    SandboxError,
    SecurityViolation,
    check_sandbox_escape,
    run_sandboxed,
    validate_code_safety,
)

_has_typing_extensions = importlib.util.find_spec("typing_extensions") is not None  # type: ignore[attr-defined]


class TestSandboxBasics:
    """Test basic sandbox functionality."""

    def test_safe_code_runs(self):
        """Safe code should execute without issues."""
        code = "x = 1 + 2; __result__ = x"
        result, _output = run_sandboxed(code)
        assert result == 3

    def test_print_captures_output(self):
        """Print should be captured to output buffer."""
        code = "print('hello')"
        _result, output = run_sandboxed(code)
        assert output == "hello\n"

    def test_multiple_prints(self):
        """Multiple prints should be captured."""
        code = "print('one'); print('two')"
        _result, output = run_sandboxed(code)
        assert output == "one\ntwo\n"


class TestSandboxBlockedOperations:
    """Test that dangerous operations are blocked."""

    def test_eval_blocked(self):
        """eval() should be blocked."""
        code = "eval('1+1')"
        with pytest.raises(SecurityViolation, match="eval"):
            run_sandboxed(code)

    def test_exec_blocked(self):
        """exec() should be blocked."""
        code = "exec('x=1')"
        with pytest.raises(SecurityViolation, match="exec"):
            run_sandboxed(code)

    def test_compile_blocked(self):
        """compile() should be blocked."""
        code = "compile('1+1', '', 'eval')"
        with pytest.raises(SecurityViolation, match="compile"):
            run_sandboxed(code)

    def test_open_blocked(self):
        """open() should be blocked."""
        code = "open('/etc/passwd')"
        with pytest.raises(SecurityViolation, match="open"):
            run_sandboxed(code)

    def test_io_open_blocked(self):
        """io.open() should be blocked by the module proxy."""
        config = SandboxConfig(strict=False)
        code = "import io; io.open('/etc/passwd')"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)

    def test_io_FileIO_blocked(self):
        """io.FileIO should be blocked by the module proxy."""
        config = SandboxConfig(strict=False)
        code = "import io; io.FileIO('/etc/passwd')"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)

    def test_input_blocked(self):
        """input() should be blocked."""
        code = "input()"
        with pytest.raises(SecurityViolation, match="input"):
            run_sandboxed(code)

    def test_import_blocked(self):
        """__import__ should be blocked."""
        code = "__import__('os')"
        with pytest.raises(SecurityViolation, match="__import__"):
            run_sandboxed(code)

    def test_builtins_module_import_blocked(self):
        """Importing builtins should not expose blocked functions."""
        code = """
import builtins
f = getattr(builtins, "open")
__result__ = f("README.md").read()
"""
        with pytest.raises(SecurityViolation, match="builtins"):
            run_sandboxed(code)

    def test_globals_blocked(self):
        """globals() should be blocked."""
        code = "globals()"
        with pytest.raises(SecurityViolation, match="globals"):
            run_sandboxed(code)

    def test_locals_blocked(self):
        """locals() should be blocked."""
        code = "locals()"
        with pytest.raises(SecurityViolation, match="locals"):
            run_sandboxed(code)

    def test_getattr_blocked(self):
        """getattr() on dangerous attributes should be blocked."""
        # Use a safe attribute with getattr to test it's available
        code = "x = [1,2,3]; getattr(x, '__class__')"
        # Blocked at runtime by safe_getattr.  In the process sandbox,
        # the child raises RuntimeError (can't propagate custom types).
        with pytest.raises((SecurityViolation, RuntimeError), match="__class__"):
            run_sandboxed(code)

    def test_setattr_blocked(self):
        """setattr() should be blocked."""
        code = "setattr(object, 'x', 1)"
        with pytest.raises(SecurityViolation, match="setattr"):
            run_sandboxed(code)

    def test_breakpoint_blocked(self):
        """breakpoint() should be blocked at static validation."""
        code = "breakpoint()"
        with pytest.raises(SecurityViolation, match="breakpoint"):
            run_sandboxed(code)

    def test_memoryview_blocked(self):
        """memoryview() should be blocked at static validation."""
        code = "memoryview(b'hello')"
        with pytest.raises(SecurityViolation, match="memoryview"):
            run_sandboxed(code)

    def test_bytearray_blocked(self):
        """bytearray() should be blocked at static validation."""
        code = "bytearray(10)"
        with pytest.raises(SecurityViolation, match="bytearray"):
            run_sandboxed(code)

    def test_bytes_blocked(self):
        """bytes() should be blocked at static validation."""
        code = "bytes(10)"
        with pytest.raises(SecurityViolation, match="bytes"):
            run_sandboxed(code)

    def test_breakpoint_blocked_at_runtime(self):
        """breakpoint() blocked at runtime even without static validation."""
        config = SandboxConfig(strict=False)
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed("breakpoint()", config)

    def test_memoryview_blocked_at_runtime(self):
        """memoryview() blocked at runtime even without static validation."""
        config = SandboxConfig(strict=False)
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed("memoryview(b'x')", config)

    def test_bytearray_blocked_at_runtime(self):
        """bytearray() blocked at runtime even without static validation."""
        config = SandboxConfig(strict=False)
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed("bytearray(10)", config)


class TestSandboxResourceLimits:
    """Test resource limit enforcement."""

    def test_output_limit(self):
        """Output should be limited."""
        config = SandboxConfig(max_output_length=100)
        code = "for i in range(1000): print('x' * 100)"
        with pytest.raises((ResourceLimitExceeded, RuntimeError)):
            run_sandboxed(code, config)


class TestSandboxAllowedOperations:
    """Test that safe operations are allowed."""

    def test_basic_math(self):
        """Basic math should work."""
        code = "__result__ = 2 + 3 * 4"
        result, _ = run_sandboxed(code)
        assert result == 14

    def test_list_operations(self):
        """List operations should work."""
        code = """
lst = [1, 2, 3]
__result__ = len(lst) + sum(lst)
"""
        result, _ = run_sandboxed(code)
        assert result == 9

    def test_dict_operations(self):
        """Dict operations should work."""
        code = """
d = {'a': 1, 'b': 2}
__result__ = d['a'] + d['b']
"""
        result, _ = run_sandboxed(code)
        assert result == 3

    def test_string_operations(self):
        """String operations should work."""
        code = "__result__ = 'hello' + ' ' + 'world'"
        result, _ = run_sandboxed(code)
        assert result == "hello world"

    def test_builtin_functions(self):
        """Safe builtin functions should work."""
        code = "__result__ = max(1, 2, 3) + min(4, 5, 6)"
        result, _ = run_sandboxed(code)
        assert result == 7

    def test_type_checking(self):
        """Type checking should work."""
        code = "__result__ = isinstance(42, int)"
        result, _ = run_sandboxed(code)
        assert result is True


class TestCodeSafetyValidation:
    """Test static code safety validation."""

    def test_detects_dangerous_import(self):
        """Should detect dangerous imports."""
        code = "import os"
        warnings = validate_code_safety(code)
        assert any("os" in w for w in warnings)

    def test_detects_dangerous_builtin(self):
        """Should detect dangerous builtins."""
        code = "eval('1+1')"
        warnings = validate_code_safety(code)
        assert any("eval" in w for w in warnings)

    def test_detects_dangerous_attribute(self):
        """Should detect dangerous attribute access."""
        code = "x.__class__.__bases__"
        warnings = validate_code_safety(code)
        assert any("__class__" in w or "__bases__" in w for w in warnings)

    def test_safe_code_no_warnings(self):
        """Safe code should have no warnings."""
        code = "x = 1 + 2"
        warnings = validate_code_safety(code)
        assert len(warnings) == 0

    def test_check_sandbox_escape(self):
        """check_sandbox_escape should return True for dangerous code."""
        assert check_sandbox_escape("import os") is True
        assert check_sandbox_escape("x = 1 + 2") is False

    # -- AST-based bypass detection (H4) --

    def test_detects_eval_alias_via_assignment(self):
        """AST analysis should catch aliasing: e = eval; e('x')."""
        code = "e = eval\ne('code')"
        warnings = validate_code_safety(code)
        assert any("eval" in w for w in warnings)

    def test_detects_exec_alias_via_assignment(self):
        """AST analysis should catch aliasing: f = exec."""
        code = "f = exec"
        warnings = validate_code_safety(code)
        assert any("exec" in w for w in warnings)

    def test_detects_from_import(self):
        """AST analysis should catch 'from os import path'."""
        code = "from os import path"
        warnings = validate_code_safety(code)
        assert any("os" in w for w in warnings)

    def test_syntax_error_returns_warning(self):
        """Unparseable code should fail closed during validation."""
        code = "def ("
        warnings = validate_code_safety(code)
        assert len(warnings) == 1
        assert "Invalid Python syntax" in warnings[0]


class TestIsSafeValue:
    """Test the allowlist-based is_safe_value()."""

    def test_os_system_is_unsafe(self):
        """os.system must be rejected by the allowlist."""
        import os

        from geno.sandbox import is_safe_value

        assert is_safe_value(os.system) is False

    def test_len_is_safe(self):
        """len is a safe builtin."""
        from geno.sandbox import is_safe_value

        assert is_safe_value(len) is True

    def test_lambda_is_safe(self):
        """Lambdas/closures should be accepted."""
        from geno.sandbox import is_safe_value

        assert is_safe_value(lambda x: x) is True

    def test_primitives_safe(self):
        """Primitive values should be safe."""
        from geno.sandbox import is_safe_value

        assert is_safe_value(42) is True
        assert is_safe_value("hello") is True
        assert is_safe_value(None) is True

    def test_subprocess_popen_is_unsafe(self):
        """subprocess.Popen must be rejected."""
        import subprocess

        from geno.sandbox import is_safe_value

        assert is_safe_value(subprocess.Popen) is False


class TestCompiledCodeSandbox:
    """Test sandbox in compiled code execution."""

    def test_compiled_code_runs(self):
        """Compiled code should run in sandbox."""
        source = """
func add(x: Int, y: Int) -> Int
    example 1, 2 -> 3

    return x + y
end func add
"""
        globals_dict = compile_and_exec(source, sandboxed=True, timeout=None)
        assert globals_dict["add"](1, 2) == 3

    def test_compiled_code_runs_with_hard_timeout(self):
        """Compiled code should run in ProcessSandbox with hard timeouts."""
        source = """
func main() -> Int
    return 42
end func main
"""
        result = compile_and_exec(source, sandboxed=True, timeout=5.0)
        assert result["__result__"] == 42
        assert result["__output__"] == ""

    def test_compiled_sandbox_blocks_dangerous_ops(self):
        """Compiled code sandbox should block dangerous operations."""
        source = """
func add(x: Int, y: Int) -> Int
    example 1, 2 -> 3

    return x + y
end func add
"""
        globals_dict = compile_and_exec(source, sandboxed=True, timeout=None)

        # The returned globals should have blocked builtins
        builtins = globals_dict.get("__builtins__", {})
        if hasattr(builtins, "__getitem__"):
            # If it's a dict
            if "eval" in builtins:
                with pytest.raises(
                    (RuntimeError, SecurityViolation), match="Blocked operation"
                ):
                    builtins["eval"]("1+1")
        elif hasattr(builtins, "eval"):
            # If it's a module
            with pytest.raises(
                (RuntimeError, SecurityViolation), match="Blocked operation"
            ):
                builtins.eval("1+1")

    def test_compiled_unsafe_mode(self):
        """Unsafe mode should allow all operations."""
        source = """
func identity(x: Int) -> Int
    example 5 -> 5

    return x
end func identity
"""
        globals_dict = compile_and_exec(source, sandboxed=False, timeout=None)
        assert globals_dict["identity"](42) == 42


class TestInterpreterSandbox:
    """Test sandbox in interpreter."""

    def test_interpreter_output_capture(self):
        """Interpreter should capture print output."""
        from geno.lexer import Lexer
        from geno.parser import Parser

        source = """
func greet(name: String) -> String
    example "world" -> "hello"

    return "hello"
end func greet

func main() -> Unit
    let greeting: String = greet("test")
    print(greeting)
    return ()
end func main
"""
        lexer = Lexer(source)
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()

        interp = Interpreter(check_examples=False)
        interp.run(program)
        # Check output was captured
        output = interp.get_output()
        assert "hello" in output


class TestRecursionDoSPrevention:
    """A recursive try/catch must not crash the host with RecursionError (#650)."""

    _NESTED_TRY_CATCH = """
    func nested_try(depth: Int) -> String
        example 0 -> "done"
        if depth <= 0 then
            return "done"
        end if
        try
            throw nested_try(depth - 1)
        catch e: String
            return e
        end try
    end func

    func main() -> String
        return nested_try(1000)
    end func
    """

    def test_nested_try_catch_returns_e502_not_host_crash(self):
        """The audit's repro: nested try/catch must surface E502, not crash."""
        from geno.api import RunConfig, run
        from geno.diagnostics import ErrorCode

        result = run(
            self._NESTED_TRY_CATCH,
            config=RunConfig(max_steps=10000, timeout=30.0),
        )
        assert result.ok is False, (
            f"Expected sandboxed failure but program succeeded with "
            f"value={result.value!r}"
        )
        codes = [d.code for d in result.diagnostics]
        assert ErrorCode.SANDBOX_RECURSION_LIMIT in codes, (
            f"Expected E502 (SANDBOX_RECURSION_LIMIT) but got "
            f"{[c.value for c in codes]}"
        )

    def test_try_catch_does_not_swallow_recursion_limit(self):
        """At lower depths, try/catch must not let the program complete by
        catching the recursion-limit error as if it were a normal runtime error."""
        from geno.api import RunConfig, run
        from geno.diagnostics import ErrorCode

        result = run(
            self._NESTED_TRY_CATCH,
            config=RunConfig(max_steps=100000, timeout=30.0, max_recursion_depth=100),
        )
        assert result.ok is False, (
            f"Recursion limit must not be catchable by user try/catch — "
            f"program returned ok=True with value={result.value!r}"
        )
        codes = [d.code for d in result.diagnostics]
        assert ErrorCode.SANDBOX_RECURSION_LIMIT in codes, (
            f"Expected E502 but got {[c.value for c in codes]}"
        )


class TestSandboxEscapeAttempts:
    """Test various sandbox escape attempts are blocked."""

    def test_class_escape_blocked(self):
        """Escape via __class__ should be detected."""
        code = "().__class__.__bases__[0].__subclasses__()"
        warnings = validate_code_safety(code)
        assert len(warnings) > 0

    def test_globals_escape_blocked(self):
        """Escape via __globals__ should be detected."""
        code = "func.__globals__"
        warnings = validate_code_safety(code)
        assert any("__globals__" in w for w in warnings)

    def test_code_escape_blocked(self):
        """Escape via __code__ should be detected."""
        code = "func.__code__"
        warnings = validate_code_safety(code)
        assert any("__code__" in w for w in warnings)

    def test_builtins_escape_blocked(self):
        """Escape via __builtins__ should be detected."""
        code = "func.__builtins__"
        warnings = validate_code_safety(code)
        assert any("__builtins__" in w for w in warnings)


class TestSandboxDunderRejection:
    """Sandbox must unconditionally reject __getattribute__/__getattr__."""

    def test_getattribute_method_rejected(self):
        """Class with __getattribute__ method is rejected even in non-strict."""
        config = SandboxConfig(strict=False)
        code = (
            "class Escape:\n"
            "    def __getattribute__(self, name):\n"
            "        return object.__getattribute__(self, name)\n"
        )
        with pytest.raises((SecurityViolation, RuntimeError), match="__getattribute__"):
            run_sandboxed(code, config)

    def test_getattr_method_rejected(self):
        """Class with __getattr__ method is rejected even in non-strict."""
        config = SandboxConfig(strict=False)
        code = "class Sneak:\n    def __getattr__(self, name):\n        return None\n"
        with pytest.raises((SecurityViolation, RuntimeError), match="__getattr__"):
            run_sandboxed(code, config)

    def test_getattribute_assigned_rejected(self):
        """__getattribute__ assigned as attribute inside class is rejected."""
        config = SandboxConfig(strict=False)
        code = "class Tricky:\n    __getattribute__ = lambda self, name: None\n"
        with pytest.raises((SecurityViolation, RuntimeError), match="__getattribute__"):
            run_sandboxed(code, config)

    def test_async_getattribute_rejected(self):
        """Async __getattribute__ is also rejected."""
        config = SandboxConfig(strict=False)
        code = (
            "class AsyncEscape:\n"
            "    async def __getattribute__(self, name):\n"
            "        return None\n"
        )
        with pytest.raises((SecurityViolation, RuntimeError), match="__getattribute__"):
            run_sandboxed(code, config)

    def test_safe_class_passes_dunder_check_non_strict(self):
        """A class without dangerous dunders passes the dunder check."""
        from geno.sandbox import _reject_dangerous_dunders

        code = (
            "class Safe:\n"
            "    def __init__(self):\n"
            "        self.x = 1\n"
            "    def __repr__(self):\n"
            "        return 'Safe()'\n"
        )
        # Should NOT raise — __init__ and __repr__ are safe dunders
        _reject_dangerous_dunders(code)  # no exception

    def test_dangerous_dunders_del_blocked(self):
        """__del__ in class body is rejected (destructor runs at GC time)."""
        from geno.sandbox import _reject_dangerous_dunders

        code = "class Evil:\n    def __del__(self):\n        pass\n"
        with pytest.raises(SecurityViolation, match="__del__"):
            _reject_dangerous_dunders(code)

    def test_dangerous_dunders_init_subclass_blocked(self):
        """__init_subclass__ in class body is rejected."""
        from geno.sandbox import _reject_dangerous_dunders

        code = "class Evil:\n    def __init_subclass__(cls):\n        pass\n"
        with pytest.raises(SecurityViolation, match="__init_subclass__"):
            _reject_dangerous_dunders(code)

    def test_dangerous_dunders_set_name_blocked(self):
        """__set_name__ in class body is rejected."""
        from geno.sandbox import _reject_dangerous_dunders

        code = "class Evil:\n    def __set_name__(self, owner, name):\n        pass\n"
        with pytest.raises(SecurityViolation, match="__set_name__"):
            _reject_dangerous_dunders(code)

    def test_dangerous_dunders_class_getitem_blocked(self):
        """__class_getitem__ in class body is rejected."""
        from geno.sandbox import _reject_dangerous_dunders

        code = "class Evil:\n    def __class_getitem__(cls, item):\n        pass\n"
        with pytest.raises(SecurityViolation, match="__class_getitem__"):
            _reject_dangerous_dunders(code)

    @pytest.mark.parametrize(
        "dunder",
        [
            "__new__",
            "__setattr__",
            "__delattr__",
            "__get__",
            "__set__",
            "__delete__",
            "__index__",
            "__length_hint__",
            "__mro_entries__",
            "__instancecheck__",
            "__subclasscheck__",
        ],
    )
    def test_dangerous_c_invoked_dunders_blocked(self, dunder: str):
        """C-invoked protocol hooks in class bodies are rejected."""
        from geno.sandbox import _reject_dangerous_dunders

        code = f"class Evil:\n    def {dunder}(self, *args):\n        return 0\n"
        with pytest.raises(SecurityViolation, match=dunder):
            _reject_dangerous_dunders(code)

    def test_strict_mode_still_blocks_all_classes(self):
        """In strict mode, all class definitions are still blocked."""
        config = SandboxConfig(strict=True)
        code = "class Innocent:\n    pass\n"
        with pytest.raises(SecurityViolation):
            run_sandboxed(code, config)


class TestRuntimeSecurityEscapes:
    """Test that actual runtime escapes are blocked, not just static detection."""

    def test_getattr_class_blocked_at_runtime(self):
        """Runtime access to __class__ via getattr should be blocked."""
        # In non-strict mode, this tests the runtime getattr wrapper
        config = SandboxConfig(strict=False)
        code = "x = [1,2,3]; result = getattr(x, '__class__')"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)

    def test_getattr_bases_blocked_at_runtime(self):
        """Runtime access to __bases__ via getattr should be blocked."""
        config = SandboxConfig(strict=False)
        code = "result = getattr(object, '__bases__')"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)

    def test_getattr_globals_blocked_at_runtime(self):
        """Runtime access to __globals__ via getattr should be blocked."""
        config = SandboxConfig(strict=False)
        code = "def f(): pass\nresult = getattr(f, '__globals__')"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)

    def test_direct_dunder_access_blocked_in_strict_mode(self):
        """Direct attribute access to dunder attributes should be caught in strict mode."""
        config = SandboxConfig(strict=True)
        # In strict mode, static validation catches __class__ access
        code = "x = ().__class__"
        with pytest.raises(SecurityViolation):
            run_sandboxed(code, config)

    def test_direct_non_allowlisted_dunder_access_blocked_in_strict_mode(self):
        """Direct dot syntax must reject dunders outside SAFE_DUNDERS."""
        code = "__result__ = (()).__reduce_ex__(2)"
        warnings = validate_code_safety(code)
        assert any("__reduce_ex__" in warning for warning in warnings)

        with pytest.raises(SecurityViolation, match="__reduce_ex__"):
            run_sandboxed(code, SandboxConfig(strict=True))

    def test_direct_safe_dunder_access_allowed_in_strict_mode(self):
        """Direct dot syntax may still use dunders from SAFE_DUNDERS."""
        code = "__result__ = [1, 2, 3].__len__()"
        assert validate_code_safety(code) == []

        result, _output = run_sandboxed(code, SandboxConfig(strict=True))
        assert result == 3

    def test_safe_getattr_allows_normal_access(self):
        """Safe getattr should allow normal attribute access."""
        config = SandboxConfig(strict=False)
        code = """
d = {'x': 42}
result = getattr(d, 'get')('x')
__result__ = result
"""
        result, _output = run_sandboxed(code, config)
        assert result == 42

    def test_safe_hasattr_blocks_dangerous(self):
        """Safe hasattr should return False for dangerous attributes."""
        config = SandboxConfig(strict=False)
        code = """
result = hasattr(dict, '__class__')
__result__ = result
"""
        result, _output = run_sandboxed(code, config)
        assert result is False

    def test_safe_hasattr_allows_normal(self):
        """Safe hasattr should work for normal attributes."""
        config = SandboxConfig(strict=False)
        code = """
d = {'x': 42}
result = hasattr(d, 'get')
__result__ = result
"""
        result, _output = run_sandboxed(code, config)
        assert result is True

    def test_safe_hasattr_allows_safe_dunders(self):
        """Safe hasattr must return True for SAFE_DUNDERS, matching getattr."""
        config = SandboxConfig(strict=False)
        code = """
result = hasattr([1, 2, 3], '__len__')
__result__ = result
"""
        result, _output = run_sandboxed(code, config)
        assert result is True

    def test_safe_hasattr_blocks_unsafe_dunders(self):
        """Safe hasattr must return False for dunders not in SAFE_DUNDERS."""
        config = SandboxConfig(strict=False)
        code = """
result = hasattr([1, 2, 3], '__subclasses__')
__result__ = result
"""
        result, _output = run_sandboxed(code, config)
        assert result is False

    def test_hasattr_getattr_consistency(self):
        """hasattr and getattr must agree on what's accessible."""
        config = SandboxConfig(strict=False)
        # If getattr succeeds for a safe dunder, hasattr must return True
        code = """
obj = [1, 2, 3]
getattr_ok = True
try:
    getattr(obj, '__len__')
except Exception:
    getattr_ok = False
hasattr_ok = hasattr(obj, '__len__')
__result__ = (getattr_ok == hasattr_ok)
"""
        result, _output = run_sandboxed(code, config)
        assert result is True


class TestProcessSandbox:
    """Test the process-based sandbox for hard timeouts."""

    def test_basic_execution(self):
        """Basic code should execute correctly in ProcessSandbox."""
        from geno.sandbox import ProcessSandboxConfig, run_in_process

        config = ProcessSandboxConfig(timeout=5.0)
        result, _output = run_in_process("x = 1 + 2; __result__ = x", config)
        assert result == 3

    def test_print_capture(self):
        """Print output should be captured in ProcessSandbox."""
        from geno.sandbox import ProcessSandboxConfig, run_in_process

        config = ProcessSandboxConfig(timeout=5.0)
        _result, output = run_in_process("print('hello world')", config)
        assert "hello world" in output

    def test_timeout_enforcement(self):
        """Infinite loops should be killed by timeout."""
        from geno.sandbox import ProcessSandboxConfig, TimeoutError, run_in_process

        config = ProcessSandboxConfig(timeout=1.0)
        with pytest.raises(TimeoutError):
            run_in_process("while True: pass", config)

    def test_blocked_operations(self):
        """Dangerous operations should be blocked in ProcessSandbox."""
        from geno.sandbox import ProcessSandboxConfig, SecurityViolation, run_in_process

        config = ProcessSandboxConfig(timeout=5.0)
        # eval is blocked at static validation
        with pytest.raises(SecurityViolation):
            run_in_process("eval('1+1')", config)

    def test_non_default_config(self):
        """ProcessSandbox with non-default config values should work via JSON env var."""
        from geno.sandbox import ProcessSandboxConfig, run_in_process

        config = ProcessSandboxConfig(
            timeout=10.0,
            max_memory_bytes=128 * 1024 * 1024,
            max_output_length=500,
            max_file_size_bytes=0,
            max_processes=1,
        )
        result, _output = run_in_process("__result__ = 42", config)
        assert result == 42

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="Windows venv redirector topology",
    )
    def test_windows_worker_command_uses_base_interpreter(self):
        """The Job must be assigned to the actual worker, not a venv shim."""
        from pathlib import Path

        from geno.sandbox import ProcessSandbox

        base_executable = getattr(sys, "_base_executable", None)
        assert isinstance(base_executable, str) and base_executable
        command = ProcessSandbox._create_worker_command()
        assert Path(command[0]).resolve() == Path(base_executable).resolve()
        if sys.prefix != sys.base_prefix:
            assert Path(command[0]).resolve() != Path(sys.executable).resolve()

    def test_worker_resource_limit_failure_fails_closed(self, monkeypatch):
        """A failed POSIX setrlimit must abort before user code executes."""
        if sys.platform == "win32":
            pytest.skip("Windows uses parent-installed Job Object limits")

        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        needle = "resource.setrlimit(which, (soft, hard))"
        worker_script = ProcessSandbox._WORKER_SCRIPT
        assert worker_script.count(needle) == 1
        monkeypatch.setattr(
            ProcessSandbox,
            "_WORKER_SCRIPT",
            worker_script.replace(
                needle,
                "raise OSError('forced setrlimit failure')",
                1,
            ),
        )

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=5.0))
        result, output, error = sandbox.execute("__result__ = 42")

        assert result is None
        assert output == ""
        assert error is not None
        assert "startup_error: failed to set RLIMIT_FSIZE" in error

    def test_resource_setup_failure_kills_worker_and_fails_closed(self, monkeypatch):
        """Parent-side resource setup errors must never run attacker code."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=5.0))

        def fail_job_setup(_process):
            raise OSError("forced job setup failure")

        monkeypatch.setattr(sandbox, "_create_windows_job", fail_job_setup)
        with pytest.raises(SandboxError, match="resource limits"):
            sandbox.execute("__result__ = 42")

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="Windows Job Object enforcement",
    )
    def test_windows_job_enforces_memory_limit(self):
        """Raw Python allocations must honor max_memory_bytes on Windows."""
        from geno.sandbox import ProcessSandboxConfig, run_in_process

        config = ProcessSandboxConfig(
            timeout=5.0,
            max_memory_bytes=64 * 1024 * 1024,
        )
        result, _output = run_in_process("__result__ = 42", config)
        assert result == 42

        with pytest.raises(RuntimeError):
            run_in_process(
                "payload = 'x' * 80_000_000\n__result__ = len(payload)",
                config,
            )

    def test_process_sandbox_forwards_max_integer_bits(self):
        """MED-04: SandboxConfig.max_integer_bits is forwarded through the
        GENO_MAX_INTEGER_BITS env var and injected into the worker as the
        _GENO_MAX_INTEGER_BITS global the runtime prelude reads.
        """
        from geno.sandbox import ProcessSandboxConfig, run_in_process

        tight = ProcessSandboxConfig(timeout=5.0, strict=False, max_integer_bits=64)
        result, _ = run_in_process("__result__ = _GENO_MAX_INTEGER_BITS", tight)
        assert result == 64

        loose = ProcessSandboxConfig(timeout=5.0, strict=False)
        result, _ = run_in_process("__result__ = _GENO_MAX_INTEGER_BITS", loose)
        assert result == 33_219

    def test_process_sandbox_blocks_unsafe_dunders(self):
        """Process sandbox should block unsafe dunder access via getattr."""
        from geno.sandbox import ProcessSandboxConfig, run_in_process

        config = ProcessSandboxConfig(timeout=5.0, strict=False)
        with pytest.raises(RuntimeError, match="__class__"):
            run_in_process("getattr([], '__class__')", config)

    def test_process_sandbox_blocks_direct_non_allowlisted_dunders(self):
        """Worker AST validation must match safe_getattr dunder policy."""
        from geno.sandbox import ProcessSandboxConfig, run_in_process

        config = ProcessSandboxConfig(timeout=5.0, strict=False)
        with pytest.raises(RuntimeError, match="__reduce_ex__"):
            run_in_process("__result__ = (()).__reduce_ex__(2)", config)

    def test_stdout_spoofing_does_not_affect_result(self):
        """User print of JSON-like output must not spoof the result.

        Regression test: the worker writes result JSON to stderr, so user
        output on stdout cannot inject a fake result.
        """
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        code = (
            'print(\'{"result": "hacked", "success": true, "error": null}\')\n'
            "__result__ = 42\n"
        )
        config = ProcessSandboxConfig(timeout=5.0, strict=False)
        sandbox = ProcessSandbox(config)
        result, output, error = sandbox.execute(code)
        assert error is None
        assert result == 42
        assert "hacked" in output  # user output is captured

    def test_process_sandbox_blocks_io_open(self):
        """Process sandbox should block io.open() file access."""
        from geno.sandbox import ProcessSandboxConfig, run_in_process

        config = ProcessSandboxConfig(timeout=5.0, strict=False)
        with pytest.raises(RuntimeError, match=r"io\.open"):
            run_in_process(
                "import io\n__result__ = io.open('/etc/passwd').read()", config
            )

    def test_process_sandbox_blocks_re_import(self):
        """Process sandbox should block 'import re' to match thread sandbox policy."""
        from geno.sandbox import ProcessSandboxConfig, run_in_process

        config = ProcessSandboxConfig(timeout=5.0, strict=False)
        with pytest.raises(RuntimeError, match="not allowed"):
            run_in_process("import re\n__result__ = re.match('a', 'a')", config)

    def test_process_sandbox_blocks_getattr_class_definition(self):
        """Process sandbox should reject classes that define __getattr__."""
        from geno.sandbox import ProcessSandboxConfig, run_in_process

        config = ProcessSandboxConfig(timeout=5.0, strict=False)
        code = (
            "class Sneak:\n"
            "    def __getattr__(self, name):\n"
            "        return __builtins__\n"
            "__result__ = 'should not reach'\n"
        )
        with pytest.raises(RuntimeError, match="__getattr__"):
            run_in_process(code, config)

    def test_process_sandbox_blocks_getattribute_class_definition(self):
        """Process sandbox should reject classes that define __getattribute__."""
        from geno.sandbox import ProcessSandboxConfig, run_in_process

        config = ProcessSandboxConfig(timeout=5.0, strict=False)
        code = (
            "class Sneak:\n"
            "    def __getattribute__(self, name):\n"
            "        return object.__getattribute__(self, name)\n"
            "__result__ = 'should not reach'\n"
        )
        with pytest.raises(RuntimeError, match="__getattribute__"):
            run_in_process(code, config)

    def test_process_sandbox_allows_safe_dunders(self):
        """Process sandbox should allow safe dunders like __len__, __bool__."""
        from geno.sandbox import ProcessSandboxConfig, run_in_process

        config = ProcessSandboxConfig(timeout=5.0, strict=False)
        result, _ = run_in_process("__result__ = getattr([1,2,3], '__len__')()", config)
        assert result == 3

    def test_process_sandbox_streams_code_over_stdin(self, monkeypatch):
        """The process sandbox should avoid persisting user code on disk."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        captured: dict[str, Any] = {}

        class CapturingStdin:
            def __init__(self):
                self.data = ""
                self.closed = False

            def write(self, data):
                self.data += data

            def close(self):
                self.closed = True

        class FakeProcess:
            def __init__(self, cmd, **kwargs):
                captured["cmd"] = cmd
                captured["env"] = kwargs.get("env")
                self.stdin = CapturingStdin()
                self.stdout = io.StringIO("")
                self.stderr = io.StringIO(
                    '{"result": 42, "success": true, "error": null}\n'
                )
                self.returncode = 0

            def wait(self, timeout=None):
                captured["input"] = self.stdin.data
                captured["stdin_closed"] = self.stdin.closed
                return self.returncode

            def kill(self):
                captured["killed"] = True

        def fake_popen(cmd, **kwargs):
            captured["popen_kwargs"] = kwargs
            return FakeProcess(cmd, **kwargs)

        import threading

        real_timer = threading.Timer

        def capturing_timer(interval, function, *args, **kwargs):
            # The hard timeout is enforced by a watchdog timer that kills
            # the worker; capture its interval to assert the configured
            # timeout reaches it.
            captured["timeout"] = interval
            return real_timer(interval, function, *args, **kwargs)

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=5.0, strict=False))
        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr(sandbox, "_create_windows_job", lambda _process: None)
        monkeypatch.setattr(
            sandbox,
            "_wait_for_worker_tree",
            lambda process, *_args: process.wait(),
        )
        monkeypatch.setattr(threading, "Timer", capturing_timer)

        result, output, error = sandbox.execute("__result__ = 42")

        assert result == 42
        assert output == ""
        assert error is None
        assert captured["cmd"] == sandbox._create_worker_command()
        assert captured["cmd"][1] == "-I"
        assert len(captured["cmd"][3]) < 256
        assert captured["input"] == sandbox._frame_worker_input(
            sandbox._create_worker_script(),
            "__result__ = 42",
        )
        assert captured["input"].endswith("__result__ = 42")
        assert captured["stdin_closed"] is True
        assert 0.0 < captured["timeout"] <= 5.0
        assert captured["popen_kwargs"]["start_new_session"] is (os.name == "posix")

    def test_process_sandbox_bounds_unstructured_stderr(self, monkeypatch):
        """Malformed worker stderr should be retained only up to a fixed bound."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        huge_stderr = ("x" * 200_000) + "tail"

        class CapturingStdin:
            def write(self, _data):
                pass

            def close(self):
                pass

        class FakeProcess:
            def __init__(self, _cmd, **_kwargs):
                self.stdin = CapturingStdin()
                self.stdout = io.StringIO("")
                self.stderr = io.StringIO(huge_stderr)
                self.returncode = 1

            def wait(self, timeout=None):
                return self.returncode

            def kill(self):
                pass

        def fake_popen(cmd, **kwargs):
            return FakeProcess(cmd, **kwargs)

        sandbox = ProcessSandbox(
            ProcessSandboxConfig(
                timeout=5.0,
                strict=False,
                max_output_length=1000,
            )
        )
        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr(sandbox, "_create_windows_job", lambda _process: None)
        monkeypatch.setattr(
            sandbox,
            "_wait_for_worker_tree",
            lambda process, *_args: process.wait(),
        )

        _result, output, error = sandbox.execute("__result__ = 42")

        assert output == ""
        assert error is not None
        assert "stderr truncated by ProcessSandbox" in error
        assert error.endswith("tail")
        assert len(error) <= sandbox._stderr_capture_limit() + 80

    def test_kqueue_fallback_observes_exit_without_reaping(self, monkeypatch):
        """BSD kqueue should supervise workers when waitid(WNOWAIT) is absent."""
        import geno.sandbox as sandbox_module
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=1.0, strict=False))
        registrations = []

        class FakeObserver:
            def __init__(self):
                self.closed = False
                self.controls = []

            def control(self, changes, max_events, timeout):
                self.controls.append((changes, max_events, timeout))
                if changes is not None:
                    registrations.extend(changes)
                    return []
                return [object()]

            def close(self):
                self.closed = True

        class FakeProcess:
            pid = 123

        observers = []

        def fake_kqueue():
            observer = FakeObserver()
            observers.append(observer)
            return observer

        def fake_kevent(ident, **kwargs):
            return ident, kwargs

        monkeypatch.setattr(sandbox_module.os, "name", "posix")
        monkeypatch.setitem(sandbox_module.os.__dict__, "killpg", lambda *_args: None)
        monkeypatch.setattr(
            ProcessSandbox, "_waitid_supervision_available", staticmethod(lambda: False)
        )
        monkeypatch.setattr(sandbox_module.select, "kqueue", fake_kqueue, raising=False)
        monkeypatch.setattr(sandbox_module.select, "kevent", fake_kevent, raising=False)
        monkeypatch.setattr(sandbox_module.select, "KQ_FILTER_PROC", 1, raising=False)
        monkeypatch.setattr(sandbox_module.select, "KQ_EV_ADD", 2, raising=False)
        monkeypatch.setattr(sandbox_module.select, "KQ_EV_ENABLE", 4, raising=False)
        monkeypatch.setattr(sandbox_module.select, "KQ_NOTE_EXIT", 8, raising=False)

        sandbox._require_posix_worker_supervision()
        observer = sandbox._create_posix_exit_observer(
            cast(subprocess.Popen[str], FakeProcess())
        )

        assert observer is observers[0]
        assert registrations == [
            (
                123,
                {
                    "filter": 1,
                    "flags": 6,
                    "fflags": 8,
                },
            )
        ]
        assert sandbox._posix_worker_exit_observable(
            cast(subprocess.Popen[str], FakeProcess()), observer
        )
        sandbox._close_posix_exit_observer(observer)
        assert observers[0].closed is True

    def test_kqueue_registration_interrupt_closes_and_propagates(self, monkeypatch):
        """Cancellation during BSD observer setup must retain its original type."""
        import geno.sandbox as sandbox_module
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=1.0, strict=False))

        class InterruptingObserver:
            def __init__(self):
                self.closed = False

            def control(self, _changes, _max_events, _timeout):
                raise KeyboardInterrupt

            def close(self):
                self.closed = True

        class FakeProcess:
            pid = 123

        observer = InterruptingObserver()
        monkeypatch.setattr(sandbox_module.os, "name", "posix")
        monkeypatch.setattr(
            ProcessSandbox, "_waitid_supervision_available", staticmethod(lambda: False)
        )
        monkeypatch.setattr(
            sandbox_module.select, "kqueue", lambda: observer, raising=False
        )
        monkeypatch.setattr(
            sandbox_module.select,
            "kevent",
            lambda *_args, **_kwargs: object(),
            raising=False,
        )
        monkeypatch.setattr(sandbox_module.select, "KQ_FILTER_PROC", 1, raising=False)
        monkeypatch.setattr(sandbox_module.select, "KQ_EV_ADD", 2, raising=False)
        monkeypatch.setattr(sandbox_module.select, "KQ_EV_ENABLE", 4, raising=False)
        monkeypatch.setattr(sandbox_module.select, "KQ_NOTE_EXIT", 8, raising=False)

        with pytest.raises(KeyboardInterrupt):
            sandbox._create_posix_exit_observer(
                cast(subprocess.Popen[str], FakeProcess())
            )

        assert observer.closed is True

    def test_kqueue_fallback_kills_group_before_reaping(self, monkeypatch):
        """A BSD exit event must leave the leader waitable through group cleanup."""
        import threading

        import geno.sandbox as sandbox_module
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=1.0, strict=False))
        lifecycle = []

        class FakeObserver:
            def control(self, changes, max_events, timeout):
                assert changes is None
                assert max_events == 1
                assert timeout is None
                lifecycle.append("exit-observed")
                return [object()]

        class FakeProcess:
            pid = 123

            def wait(self):
                lifecycle.append("leader-reaped")
                return 0

        monkeypatch.setattr(sandbox_module.os, "name", "posix")
        monkeypatch.setattr(sandbox, "_waitid_supervision_available", lambda: False)
        monkeypatch.setattr(
            sandbox,
            "_kill_posix_process_group",
            lambda _process: lifecycle.append("group-killed"),
        )

        returncode = sandbox._wait_for_worker_tree(
            cast(subprocess.Popen[str], FakeProcess()),
            threading.Lock(),
            threading.Event(),
            threading.Event(),
            FakeObserver(),
        )

        assert returncode == 0
        assert lifecycle == ["exit-observed", "group-killed", "leader-reaped"]

    def test_timeout_observer_failure_kills_worker_group_fail_closed(self, monkeypatch):
        """A failed BSD exit query must not disable hard timeout enforcement."""
        import threading

        import geno.sandbox as sandbox_module
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=1.0, strict=False))
        killed = False

        class FailingObserver:
            def control(self, _changes, _max_events, _timeout):
                raise OSError("kqueue failed")

        class FakeProcess:
            pid = 123

        def record_kill(_process):
            nonlocal killed
            killed = True

        monkeypatch.setattr(sandbox_module.os, "name", "posix")
        monkeypatch.setattr(
            ProcessSandbox, "_waitid_supervision_available", staticmethod(lambda: False)
        )
        monkeypatch.setattr(sandbox, "_kill_posix_process_group", record_kill)
        timed_out = threading.Event()
        termination_errors: list[SandboxError] = []

        sandbox._terminate_worker_on_timeout(
            cast(subprocess.Popen[str], FakeProcess()),
            threading.Lock(),
            threading.Event(),
            threading.Event(),
            timed_out,
            termination_errors,
            FailingObserver(),
        )

        assert killed is True
        assert timed_out.is_set() is True
        assert len(termination_errors) == 1
        assert "Lost POSIX sandbox worker state" in str(termination_errors[0])

    @pytest.mark.skipif(os.name != "posix", reason="POSIX process groups only")
    def test_late_timeout_callback_does_not_kill_exited_worker(self, monkeypatch):
        import threading

        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=1.0, strict=False))
        killed = False

        class FakeProcess:
            def poll(self):
                raise AssertionError("an exited POSIX leader must not be polled")

        def unexpected_kill(_process):
            nonlocal killed
            killed = True

        monkeypatch.setattr(sandbox, "_kill_posix_process_group", unexpected_kill)
        lifecycle_lock = threading.Lock()
        exit_observed = threading.Event()
        exit_observed.set()
        timed_out = threading.Event()
        sandbox._terminate_worker_on_timeout(
            cast(subprocess.Popen[str], FakeProcess()),
            lifecycle_lock,
            exit_observed,
            threading.Event(),
            timed_out,
            [],
        )

        assert killed is False
        assert timed_out.is_set() is False

    @pytest.mark.skipif(os.name != "posix", reason="POSIX process groups only")
    def test_timeout_observes_exited_leader_before_marking_timeout(self, monkeypatch):
        import threading

        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=1.0, strict=False))
        killed = False

        class FakeProcess:
            pid = 123

        monkeypatch.setattr(
            sandbox, "_posix_worker_exit_observable", lambda _process: True
        )

        def record_kill(_process):
            nonlocal killed
            killed = True

        monkeypatch.setattr(sandbox, "_kill_posix_process_group", record_kill)
        exit_observed = threading.Event()
        timed_out = threading.Event()
        sandbox._terminate_worker_on_timeout(
            cast(subprocess.Popen[str], FakeProcess()),
            threading.Lock(),
            exit_observed,
            threading.Event(),
            timed_out,
            [],
        )

        assert killed is True
        assert exit_observed.is_set() is True
        assert timed_out.is_set() is False

    def test_setup_interrupt_kills_and_reaps_worker(self, monkeypatch):
        import threading

        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=5.0, strict=False))
        real_popen = subprocess.Popen
        processes = []

        def capture_popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        def interrupt_thread_start(_thread):
            raise KeyboardInterrupt

        monkeypatch.setattr(subprocess, "Popen", capture_popen)
        monkeypatch.setattr(threading.Thread, "start", interrupt_thread_start)

        with pytest.raises(KeyboardInterrupt):
            sandbox._run_worker(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                "",
            )

        assert len(processes) == 1
        assert processes[0].returncode is not None
        assert processes[0].stdin is not None and processes[0].stdin.closed
        assert processes[0].stdout is not None and processes[0].stdout.closed
        assert processes[0].stderr is not None and processes[0].stderr.closed

    @pytest.mark.skipif(os.name != "posix", reason="POSIX process groups only")
    def test_interrupted_wait_kills_worker_group(self, monkeypatch):
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=5.0, strict=False))
        real_kill = sandbox._kill_posix_process_group
        killed = False

        def interrupt_wait(*_args, **_kwargs):
            raise KeyboardInterrupt

        def record_kill(process):
            nonlocal killed
            killed = True
            real_kill(process)

        monkeypatch.setattr(sandbox, "_wait_for_worker_tree", interrupt_wait)
        monkeypatch.setattr(sandbox, "_kill_posix_process_group", record_kill)
        with pytest.raises(KeyboardInterrupt):
            sandbox._run_worker(
                [sys.executable, "-c", "import time; time.sleep(30)"], ""
            )
        assert killed is True

    @pytest.mark.skipif(os.name != "posix", reason="POSIX process groups only")
    def test_worker_descendants_are_killed_after_normal_exit(self, tmp_path):
        """A child inheriting worker pipes must not survive a normal worker exit."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        marker = tmp_path / "descendant-survived-normal-exit"
        ready = tmp_path / "descendant-started-normal-exit"
        child_script = (
            "import pathlib, time; "
            f"pathlib.Path({str(ready)!r}).write_text('ready'); "
            "time.sleep(0.5); "
            f"pathlib.Path({str(marker)!r}).write_text('survived')"
        )
        worker_script = (
            "import pathlib, subprocess, sys, time\n"
            f"ready = pathlib.Path({str(ready)!r})\n"
            f"subprocess.Popen([sys.executable, '-c', {child_script!r}])\n"
            "deadline = time.monotonic() + 5\n"
            "while not ready.exists() and time.monotonic() < deadline:\n"
            "    time.sleep(0.01)\n"
            "if not ready.exists():\n"
            "    raise RuntimeError('descendant did not start')\n"
        )
        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=5.0, strict=False))
        returncode, _stdout, _stderr, _truncated = sandbox._run_worker(
            [sys.executable, "-c", worker_script], ""
        )

        assert returncode == 0
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if marker.exists():
                pytest.fail("sandbox worker descendant survived normal exit")
            time.sleep(0.05)
        assert not marker.exists(), "sandbox worker descendant survived normal exit"

    @pytest.mark.skipif(os.name != "posix", reason="POSIX process groups only")
    def test_worker_descendants_are_killed_after_timeout(self, tmp_path):
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        marker = tmp_path / "descendant-survived"
        child_script = (
            "import pathlib, time; "
            "time.sleep(1); "
            f"pathlib.Path({str(marker)!r}).write_text('survived')"
        )
        worker_script = (
            "import subprocess, sys, time; "
            f"subprocess.Popen([sys.executable, '-c', {child_script!r}]); "
            "time.sleep(30)"
        )
        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=0.2, strict=False))

        with pytest.raises(subprocess.TimeoutExpired):
            sandbox._run_worker([sys.executable, "-c", worker_script], "")

        time.sleep(1.1)
        assert not marker.exists()

    @pytest.mark.skipif(os.name != "nt", reason="Windows Job Objects only")
    def test_windows_worker_descendants_end_with_normal_leader(self, tmp_path):
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        marker = tmp_path / "windows-descendant-survived"
        child_script = (
            "import pathlib, time; "
            "time.sleep(2); "
            f"pathlib.Path({str(marker)!r}).write_text('survived')"
        )
        worker_script = (
            "import subprocess, sys; "
            f"subprocess.Popen([sys.executable, '-c', {child_script!r}])"
        )
        sandbox = ProcessSandbox(
            ProcessSandboxConfig(
                timeout=1.0,
                strict=False,
                max_memory_bytes=None,
                max_processes=3,
            )
        )

        started_at = time.monotonic()
        returncode, _stdout, _stderr, _truncated = sandbox._run_worker(
            [sys.executable, "-c", worker_script], ""
        )
        assert returncode == 0, _stderr
        assert time.monotonic() - started_at < 1.5
        time.sleep(2.1)
        assert not marker.exists()

    def test_process_sandbox_bounds_worker_exception_messages(self):
        """Worker JSON errors should not serialize unbounded exception text."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(
            ProcessSandboxConfig(
                timeout=5.0,
                strict=False,
                max_output_length=1000,
            )
        )

        _result, output, error = sandbox.execute("raise RuntimeError('x' * 200_000)")

        assert output == ""
        assert error is not None
        assert len(error) <= 1000
        assert "[truncated]" in error

    def test_process_sandbox_reads_stdin_before_memory_limit(self):
        """Worker startup should not apply RLIMIT_AS before reading stdin."""
        from geno.sandbox import ProcessSandbox

        script = ProcessSandbox._WORKER_SCRIPT
        assert script.index("code = sys.stdin.read()") < script.index("RLIMIT_AS")

    def test_geno_frontend_applies_memory_limit_before_compiler_import(self):
        """Geno source processing starts only after the early RLIMIT_AS gate."""
        from geno.sandbox import ProcessSandbox

        script = ProcessSandbox._WORKER_SCRIPT
        frontend = script[
            script.index('if config.get("worker_mode") == "geno_cli"') : script.index(
                "# ---- Worker-side AST validation"
            )
        ]
        assert frontend.index("setrlimit(") < frontend.index("from geno.cli.run import")

    def test_geno_worker_requires_compiled_runtime_mode(self):
        """The special request path cannot run without generated-code guards."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig, SandboxError

        sandbox = ProcessSandbox(ProcessSandboxConfig(compiled_runtime_prelude=False))

        with pytest.raises(SandboxError, match="compiled_runtime_prelude"):
            sandbox.execute_geno_request({})

    def test_geno_worker_ignores_cwd_package_shadow(self, tmp_path, monkeypatch):
        """The isolated frontend must import Geno only from its trusted root."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        marker = tmp_path / "shadow-imported"
        package = tmp_path / "geno"
        package.mkdir()
        (package / "__init__.py").write_text(
            f"open({str(marker)!r}, 'w').close()\n",
            encoding="utf-8",
        )
        source = tmp_path / "main.geno"
        source.write_text(
            "func main() -> Int\n    return 1\nend func main\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        config = ProcessSandboxConfig(
            timeout=15.0,
            strict=False,
            compiled_runtime_prelude=True,
        )
        request = {
            "filename": str(source),
            "target": None,
            "check_examples": False,
            "timeout": config.timeout,
            "max_recursion_depth": config.max_recursion_depth,
            "max_output_length": config.max_output_length,
            "max_collection_size": config.max_collection_size,
            "max_integer_bits": config.max_integer_bits,
        }

        result, output, error = ProcessSandbox(config).execute_geno_request(request)

        assert error is None
        assert output == ""
        assert result == 1
        assert not marker.exists()

    def test_process_sandbox_worker_has_structured_startup_error_envelope(self):
        """Pre-exec worker failures should be reported as JSON categories."""
        from geno.sandbox import ProcessSandbox

        script = ProcessSandbox._WORKER_SCRIPT
        assert '"error_type": error_type' in script
        assert '"startup_error"' in script
        assert '"resource_limit"' in script

    def test_process_sandbox_blocks_breakpoint(self):
        """Process sandbox should block breakpoint()."""
        from geno.sandbox import ProcessSandboxConfig, run_in_process

        config = ProcessSandboxConfig(timeout=5.0, strict=False)
        with pytest.raises(RuntimeError, match=r"breakpoint\(\).*not allowed"):
            run_in_process("breakpoint()\n__result__ = 1", config)

    def test_process_sandbox_blocks_memoryview(self):
        """Process sandbox should block memoryview()."""
        from geno.sandbox import ProcessSandboxConfig, run_in_process

        config = ProcessSandboxConfig(timeout=5.0, strict=False)
        with pytest.raises(RuntimeError, match=r"memoryview\(\).*not allowed"):
            run_in_process("memoryview(b'x')\n__result__ = 1", config)

    def test_process_sandbox_blocks_bytearray(self):
        """Process sandbox should block bytearray()."""
        from geno.sandbox import ProcessSandboxConfig, run_in_process

        config = ProcessSandboxConfig(timeout=5.0, strict=False)
        with pytest.raises(RuntimeError, match=r"bytearray\(\).*not allowed"):
            run_in_process("bytearray(10)\n__result__ = 1", config)

    def test_process_sandbox_blocks_bytes(self):
        """Process sandbox should block bytes()."""
        from geno.sandbox import ProcessSandboxConfig, run_in_process

        config = ProcessSandboxConfig(timeout=5.0, strict=False)
        with pytest.raises(RuntimeError, match=r"bytes\(\).*not allowed"):
            run_in_process("bytes(10)\n__result__ = 1", config)


class TestSandboxConstantConsistency:
    """Verify that security-critical constants are consistent across locations."""

    def test_blocked_field_names_match_blocked_attributes(self):
        """_runtime_support._BLOCKED_FIELD_NAMES must equal sandbox.BLOCKED_ATTRIBUTES."""
        from geno._runtime_support import _BLOCKED_FIELD_NAMES
        from geno.sandbox import BLOCKED_ATTRIBUTES

        assert _BLOCKED_FIELD_NAMES == BLOCKED_ATTRIBUTES, (
            f"DRIFT DETECTED: _BLOCKED_FIELD_NAMES has "
            f"{_BLOCKED_FIELD_NAMES - BLOCKED_ATTRIBUTES} extra, "
            f"missing {BLOCKED_ATTRIBUTES - _BLOCKED_FIELD_NAMES}"
        )

    def test_exported_safe_hasattr_allows_safe_dunders(self):
        """The module-level safe_hasattr must allow SAFE_DUNDERS like safe_getattr."""
        from geno.sandbox import SAFE_DUNDERS, safe_getattr, safe_hasattr

        obj = [1, 2, 3]
        for dunder in SAFE_DUNDERS:
            if hasattr(obj, dunder):
                # If getattr succeeds, hasattr must return True
                try:
                    safe_getattr(obj, dunder)
                    getattr_ok = True
                except Exception:
                    getattr_ok = False
                hasattr_ok = safe_hasattr(obj, dunder)
                assert getattr_ok == hasattr_ok, (
                    f"Policy mismatch for '{dunder}': "
                    f"safe_getattr={'allowed' if getattr_ok else 'blocked'}, "
                    f"safe_hasattr={'allowed' if hasattr_ok else 'blocked'}"
                )

    def test_exported_safe_hasattr_blocks_unsafe_dunders(self):
        """The module-level safe_hasattr must block dunders not in SAFE_DUNDERS."""
        from geno.sandbox import safe_hasattr

        assert safe_hasattr([1, 2, 3], "__class__") is False
        assert safe_hasattr([1, 2, 3], "__subclasses__") is False

    def test_safe_dunders_and_blocked_attributes_do_not_overlap(self):
        """No attribute should appear in both SAFE_DUNDERS and BLOCKED_ATTRIBUTES."""
        from geno.sandbox import BLOCKED_ATTRIBUTES, SAFE_DUNDERS

        overlap = SAFE_DUNDERS & BLOCKED_ATTRIBUTES
        assert not overlap, (
            f"Overlap between SAFE_DUNDERS and BLOCKED_ATTRIBUTES: {overlap}"
        )

    def test_id_not_in_safe_builtins(self):
        """id() leaks memory addresses and must not be available."""
        from geno.sandbox import SAFE_BUILTINS

        assert "id" not in SAFE_BUILTINS

    def test_pow_not_in_safe_builtins(self):
        """pow() can create arbitrary-precision integer bombs."""
        from geno.sandbox import SAFE_BUILTINS

        assert "pow" not in SAFE_BUILTINS

    def test_object_not_in_safe_builtins(self):
        """object enables arbitrary class creation with type()."""
        from geno.sandbox import SAFE_BUILTINS

        assert "object" not in SAFE_BUILTINS

    def test_removed_builtins_blocked_in_sandbox(self):
        """Removed builtins should not be callable in sandboxed exec."""
        config = SandboxConfig()
        output: list[str] = []
        from geno.sandbox import create_safe_globals

        safe_globals = create_safe_globals(config, output)
        builtins_dict = safe_globals["__builtins__"]
        assert "id" not in builtins_dict
        assert "pow" not in builtins_dict
        assert "object" not in builtins_dict

    def test_worker_script_excludes_dangerous_builtins(self):
        """ProcessSandbox worker script must not include removed builtins."""
        from geno.sandbox import ProcessSandbox

        worker = ProcessSandbox._WORKER_SCRIPT
        # Parse the safe_names set from the worker script
        # The dangerous builtins must not appear as entries in safe_names
        assert (
            "'id'" not in worker
            or "'id'" not in worker.split("safe_names")[1].split("}")[0]
        )
        assert (
            "'pow'" not in worker
            or "'pow'" not in worker.split("safe_names")[1].split("}")[0]
        )
        assert (
            "'object'" not in worker
            or "'object'" not in worker.split("safe_names")[1].split("}")[0]
        )

    def test_worker_safe_names_synced_from_config(self):
        """Worker script safe_names are populated from SAFE_BUILTINS via config.

        The worker reads 'safe_builtins' from the GENO_SANDBOX_CONFIG env
        var, which is populated by _create_restricted_env from SAFE_BUILTINS.
        """
        import json

        from geno.sandbox import SAFE_BUILTINS, ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig())
        env = sandbox._create_restricted_env()
        config = json.loads(env["GENO_SANDBOX_CONFIG"])
        worker_names = set(config["safe_builtins"])
        expected = set(SAFE_BUILTINS.keys()) | {"__build_class__"}
        assert worker_names == expected, (
            f"Worker safe_builtins mismatch.\n"
            f"  Missing: {expected - worker_names}\n"
            f"  Extra:   {worker_names - expected}"
        )

    def test_process_sandbox_receives_safe_dunders(self):
        """The process sandbox config must include safe_dunders."""
        import json

        from geno.sandbox import SAFE_DUNDERS, ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig())
        env = sandbox._create_restricted_env()
        config = json.loads(env["GENO_SANDBOX_CONFIG"])
        assert set(config["safe_dunders"]) == SAFE_DUNDERS

    def test_process_sandbox_worker_ast_blocklist_is_curated_subset(self):
        """Worker AST blocking should stay narrower than public blocked builtins.

        The process worker runs this AST filter even in strict=False mode, so
        it must not reject harmless local shadowing of informational builtin
        names like help().
        """
        import json

        from geno.sandbox import (
            _WORKER_AST_BLOCKED_BUILTINS,
            ProcessSandbox,
            ProcessSandboxConfig,
        )

        sandbox = ProcessSandbox(ProcessSandboxConfig())
        env = sandbox._create_restricted_env()
        config = json.loads(env["GENO_SANDBOX_CONFIG"])
        assert (
            set(config["worker_ast_blocked_builtins"]) == _WORKER_AST_BLOCKED_BUILTINS
        )

    def test_process_sandbox_module_blocklists_are_synced_from_config(self):
        """Worker module blocklists must come from sandbox.py's parent policy."""
        import json

        from geno.sandbox import (
            _MODULE_BLOCKED_ATTRIBUTES,
            _MODULE_BLOCKED_FUNCTIONS,
            ProcessSandbox,
            ProcessSandboxConfig,
        )

        sandbox = ProcessSandbox(ProcessSandboxConfig())
        env = sandbox._create_restricted_env()
        config = json.loads(env["GENO_SANDBOX_CONFIG"])

        worker_blocked_functions = {
            module: set(names)
            for module, names in config["module_blocked_functions"].items()
        }
        expected_blocked_functions = {
            module: set(names) for module, names in _MODULE_BLOCKED_FUNCTIONS.items()
        }
        assert worker_blocked_functions == expected_blocked_functions

        worker_blocked_attributes = {
            module: set(names)
            for module, names in config["module_blocked_attributes"].items()
        }
        expected_blocked_attributes = {
            module: set(names) for module, names in _MODULE_BLOCKED_ATTRIBUTES.items()
        }
        assert worker_blocked_attributes == expected_blocked_attributes

    def test_process_sandbox_allows_local_help_function_in_non_strict_mode(self):
        """Non-strict process sandbox should not reject harmless local shadowing."""
        from geno.sandbox import ProcessSandboxConfig, run_in_process

        code = "def help():\n    return 7\n__result__ = help()\n"
        result, output = run_in_process(
            code, ProcessSandboxConfig(timeout=5.0, strict=False)
        )
        assert result == 7
        assert output == ""


class TestModuleProxySandboxEscape:
    """Verify that imported modules are wrapped and operator is blocked
    to prevent sandbox escape via C-level attribute access.
    """

    def test_operator_import_blocked(self):
        """operator module must not be importable — attrgetter/itemgetter
        perform C-level attribute access that bypasses safe_getattr."""
        code = "import operator\n"
        config = SandboxConfig(strict=False)
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)

    def test_attrgetter_class_hierarchy_escape_blocked(self):
        """The class-hierarchy escape via attrgetter must be blocked."""
        code = "import operator\nag = operator.attrgetter\ncls = ag('__class__')(42)\n"
        config = SandboxConfig(strict=False)
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)

    def test_direct_dunder_builtins_blocked_on_proxy(self):
        """Accessing __builtins__ via dot on a proxied module must fail."""
        code = "import math\nb = math.__builtins__\n"
        config = SandboxConfig(strict=False)
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)

    def test_proxy_allows_safe_public_attrs(self):
        """Public attributes like math.sqrt must still work through the proxy."""
        code = "import math\nresult = math.sqrt(16)\nprint(int(result))\n"
        config = SandboxConfig(strict=False)
        _, output = run_sandboxed(code, config)
        assert "4" in output

    def test_proxy_blocks_private_attrs(self):
        """Private (_-prefixed) attributes on proxied modules must be blocked."""
        code = "import math\nx = math._some_internal\n"
        config = SandboxConfig(strict=False)
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)

    def test_types_import_blocked(self):
        """types module must not be importable — CodeType + FunctionType
        enable arbitrary bytecode construction and sandbox escape."""
        code = "import types\n"
        config = SandboxConfig(strict=False)
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)

    def test_types_codetype_escape_blocked(self):
        """Bytecode-level escape via types.CodeType + types.FunctionType
        must be blocked by preventing types import."""
        code = "import types\nFT = types.FunctionType\nCT = types.CodeType\n"
        config = SandboxConfig(strict=False)
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)


class TestTypeBuiltinRestriction:
    """Verify that type() is restricted to single-arg (query) form only."""

    def test_single_arg_type_allowed(self):
        """type(obj) must still work for type queries."""
        code = "result = type(42)\nprint(result.__name__)\n"
        config = SandboxConfig(strict=False)
        _, output = run_sandboxed(code, config)
        assert "int" in output

    def test_three_arg_type_blocked(self):
        """type(name, bases, ns) must be blocked — it creates classes
        that can override __getattribute__ to bypass safe_getattr."""
        code = "X = type('X', (), {})\n"
        config = SandboxConfig(strict=False)
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)

    def test_type_getattribute_escape_blocked(self):
        """Class with custom __getattribute__ via type() must be blocked."""
        code = (
            "def ga(self, name):\n"
            "    return 'escaped'\n"
            "X = type('X', (), {'__getattribute__': ga})\n"
        )
        config = SandboxConfig(strict=False)
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)


class TestFormatStringEscape:
    """Verify that str.format() C-level attribute traversal is blocked."""

    def test_format_globals_leak_blocked_strict(self):
        """str.format() with __globals__ traversal must be caught by validator
        in strict mode."""
        code = 'leaked = "{0.__globals__}".format(print)\n'
        with pytest.raises(SecurityViolation):
            run_sandboxed(code, SandboxConfig(strict=True))

    def test_format_map_blocked_strict(self):
        """str.format_map() must also be flagged by the validator."""
        code = '"{x.__globals__}".format_map({"x": print})\n'
        with pytest.raises(SecurityViolation):
            run_sandboxed(code, SandboxConfig(strict=True))

    def test_format_builtin_removed(self):
        """The format() builtin must not be available in the sandbox."""
        code = "f = format\n"
        config = SandboxConfig(strict=False)
        with pytest.raises(Exception):
            run_sandboxed(code, config)

    def test_validator_detects_format_call(self):
        """validate_code_safety must flag .format() calls."""
        warnings = validate_code_safety('"{0.__globals__}".format(print)')
        assert any("format" in w for w in warnings)

    def test_validator_detects_format_map_call(self):
        """validate_code_safety must flag .format_map() calls."""
        warnings = validate_code_safety('"{x}".format_map(d)')
        assert any("format_map" in w for w in warnings)


class TestProcessSandboxErrorHandling:
    """Verify that the process sandbox correctly reports errors,
    including exceptions with empty messages."""

    def test_assert_false_raises(self):
        """assert False must raise, not silently succeed."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=5.0))
        _result, _output, error = sandbox.execute("assert False")
        assert error is not None, "assert False was silently swallowed"

    def test_empty_assertion_error_raises(self):
        """raise AssertionError() (empty message) must not be treated as success."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=5.0))
        _result, _output, error = sandbox.execute("raise AssertionError()")
        assert error is not None, "Empty AssertionError was silently swallowed"

    def test_empty_runtime_error_raises(self):
        """raise RuntimeError() (empty message) must not be treated as success."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=5.0))
        _result, _output, error = sandbox.execute("raise RuntimeError()")
        assert error is not None, "Empty RuntimeError was silently swallowed"

    def test_run_in_process_raises_on_empty_error(self):
        """run_in_process must raise RuntimeError for empty-message exceptions."""
        from geno.sandbox import ProcessSandboxConfig, run_in_process

        with pytest.raises(RuntimeError):
            run_in_process("assert False", ProcessSandboxConfig(timeout=5.0))


class TestProcessSandboxResultChannel:
    """H-09 regression: a lost worker result must never be reported as
    success-with-None."""

    @staticmethod
    def _fake_popen_for(stderr_text: str, returncode: int):
        class FakeStdin:
            def write(self, _data):
                pass

            def close(self):
                pass

        class FakeProcess:
            def __init__(self, _cmd, **_kwargs):
                self.stdin = FakeStdin()
                self.stdout = io.StringIO("user output\n")
                self.stderr = io.StringIO(stderr_text)
                self.returncode = returncode

            def wait(self, timeout=None):
                return self.returncode

            def kill(self):
                pass

        return lambda cmd, **kwargs: FakeProcess(cmd, **kwargs)

    @staticmethod
    def _isolate_result_channel_from_process_supervision(sandbox, monkeypatch):
        monkeypatch.setattr(
            sandbox,
            "_wait_for_worker_tree",
            lambda process, *_args: process.wait(),
        )

    def test_exit_zero_without_result_json_raises(self, monkeypatch, caplog):
        """Worker exits 0 with no result line: SandboxError, logged at ERROR."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=5.0, strict=False))
        monkeypatch.setattr(
            subprocess, "Popen", self._fake_popen_for("just some text\n", 0)
        )
        monkeypatch.setattr(sandbox, "_create_windows_job", lambda _process: None)
        self._isolate_result_channel_from_process_supervision(sandbox, monkeypatch)

        with caplog.at_level("ERROR", logger="geno.sandbox"):
            with pytest.raises(SandboxError, match="wrote no result JSON"):
                sandbox.execute("__result__ = 42")
        assert any("no result JSON" in rec.message for rec in caplog.records)

    def test_exit_zero_with_unparseable_result_json_raises(self, monkeypatch):
        """Worker exits 0 with a corrupt result line: SandboxError, not None."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=5.0, strict=False))
        monkeypatch.setattr(
            subprocess, "Popen", self._fake_popen_for('{"result": 42, "succ\n', 0)
        )
        monkeypatch.setattr(sandbox, "_create_windows_job", lambda _process: None)
        self._isolate_result_channel_from_process_supervision(sandbox, monkeypatch)

        with pytest.raises(SandboxError, match="unparseable result JSON"):
            sandbox.execute("__result__ = 42")

    def test_exit_zero_truncated_stderr_mentions_capture_limit(self, monkeypatch):
        """When stderr was truncated, the error should say the result may
        have been lost to the capture bound."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(
            ProcessSandboxConfig(timeout=5.0, strict=False, max_output_length=1000)
        )
        huge_stderr = "x" * 50_000  # no JSON line survives, forces truncation
        monkeypatch.setattr(subprocess, "Popen", self._fake_popen_for(huge_stderr, 0))
        monkeypatch.setattr(sandbox, "_create_windows_job", lambda _process: None)
        self._isolate_result_channel_from_process_supervision(sandbox, monkeypatch)

        with pytest.raises(SandboxError, match="capture limit"):
            sandbox.execute("__result__ = 42")

    def test_nonzero_exit_without_json_still_reports_stderr(self, monkeypatch):
        """The rc != 0 fallback path is unchanged: stderr becomes the error."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=5.0, strict=False))
        monkeypatch.setattr(
            subprocess, "Popen", self._fake_popen_for("boom without json\n", 1)
        )
        monkeypatch.setattr(sandbox, "_create_windows_job", lambda _process: None)
        self._isolate_result_channel_from_process_supervision(sandbox, monkeypatch)

        result, _output, error = sandbox.execute("__result__ = 42")
        assert result is None
        assert error is not None
        assert "boom without json" in error

    def test_nonzero_exit_rejects_success_envelope(self, monkeypatch):
        """A crash or resource-limit kill after result output cannot report success."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=5.0, strict=False))
        success = '{"result": 42, "success": true, "error": null}\n'
        monkeypatch.setattr(
            subprocess,
            "Popen",
            self._fake_popen_for(success, 1),
        )
        monkeypatch.setattr(sandbox, "_create_windows_job", lambda _process: None)
        self._isolate_result_channel_from_process_supervision(sandbox, monkeypatch)

        result, _output, error = sandbox.execute("__result__ = 42")
        assert result is None
        assert error is not None
        assert "success" in error

    def test_oversized_result_reports_explicit_error(self):
        """A result too large for the stderr capture window must surface as an
        explicit worker error, not silent success-with-None (H-09 repro)."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(
            ProcessSandboxConfig(timeout=15.0, max_output_length=1000)
        )
        result, _output, error = sandbox.execute("__result__ = 'a' * 50_000")
        assert result is None
        assert error is not None
        assert "result_too_large" in error

    def test_large_result_within_limit_round_trips(self):
        """A result that fits the capture window still round-trips intact."""
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=15.0))
        result, _output, error = sandbox.execute("__result__ = 'a' * 50_000")
        assert error is None
        assert result == "a" * 50_000

    def test_run_in_process_surfaces_oversized_result_error(self):
        """The geno-run path (run_in_process) raises with the explicit
        diagnostic instead of returning None."""
        from geno.sandbox import ProcessSandboxConfig, run_in_process

        with pytest.raises(RuntimeError, match="result_too_large"):
            run_in_process(
                "__result__ = 'a' * 50_000",
                ProcessSandboxConfig(timeout=15.0, max_output_length=1000),
            )


class TestProcessSandboxWorkerErrorDetail:
    """M-19: an unexpected exception type from the worker (an internal
    toolchain/codegen defect) must be tagged with its exception type, and a
    traceback must be available under an opt-in debug switch."""

    def test_unexpected_exception_type_is_tagged(self):
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=5.0, strict=False))
        _r, _o, error = sandbox.execute("assert False, 'boom'")
        assert error is not None
        # AssertionError is not a sanctioned RuntimeError -> tagged with type.
        assert error.startswith("AssertionError: ")
        assert "boom" in error

    def test_sanctioned_runtime_error_is_not_tagged(self):
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=5.0, strict=False))
        _r, _o, error = sandbox.execute("raise RuntimeError('plain message')")
        # Sanctioned runtime errors are user-facing and keep their bare message.
        assert error == "plain message"

    def test_debug_env_attaches_worker_traceback_to_log(self, monkeypatch, caplog):
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        monkeypatch.setenv("GENO_SANDBOX_DEBUG", "1")
        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=5.0, strict=False))
        with caplog.at_level("ERROR", logger="geno.sandbox"):
            _r, _o, error = sandbox.execute("assert False, 'debugme'")

        assert error is not None and error.startswith("AssertionError")
        assert any(
            "Sandbox worker error" in rec.message
            and "Traceback (most recent call last)" in rec.message
            for rec in caplog.records
        )

    def test_no_debug_env_means_no_traceback_log(self, monkeypatch, caplog):
        from geno.sandbox import ProcessSandbox, ProcessSandboxConfig

        monkeypatch.delenv("GENO_SANDBOX_DEBUG", raising=False)
        sandbox = ProcessSandbox(ProcessSandboxConfig(timeout=5.0, strict=False))
        with caplog.at_level("ERROR", logger="geno.sandbox"):
            _r, _o, error = sandbox.execute("assert False, 'quiet'")

        assert error is not None
        assert not any("Sandbox worker error" in rec.message for rec in caplog.records)


class TestClassDefDetection:
    """Verify that the static validator flags class definitions."""

    def test_validator_flags_class_definition(self):
        """A class statement must be flagged — it can define __getattribute__."""
        warnings = validate_code_safety("class Evil:\n    pass")
        assert any("class" in w.lower() or "Class" in w for w in warnings)

    def test_validator_flags_class_with_getattribute(self):
        """Class with __getattribute__ override is the concrete escape vector."""
        code = (
            "class Bypass:\n"
            "    def __getattribute__(self, name):\n"
            "        return object.__getattribute__(self, name)\n"
        )
        warnings = validate_code_safety(code)
        assert len(warnings) >= 1
        assert any("Bypass" in w for w in warnings)

    def test_strict_sandbox_rejects_class_definition(self):
        """In strict mode, class definitions must cause SecurityViolation."""
        config = SandboxConfig(strict=True)
        with pytest.raises(SecurityViolation, match=r"[Cc]lass"):
            run_sandboxed("class Foo:\n    pass", config)


class TestWorkerGetattributeCheck:
    """Verify that the ProcessSandbox worker rejects __getattribute__ overrides
    even in non-strict mode."""

    def test_worker_blocks_getattribute_method(self):
        """Class with __getattribute__ method must be rejected by the worker."""
        config = SandboxConfig(strict=False)
        code = (
            "class Evil:\n"
            "    def __getattribute__(self, name):\n"
            "        return object.__getattribute__(self, name)\n"
        )
        with pytest.raises(RuntimeError, match="__getattribute__"):
            run_sandboxed(code, config, use_process=True)

    def test_worker_blocks_getattribute_assignment(self):
        """Class with __getattribute__ assigned must be rejected."""
        config = SandboxConfig(strict=False)
        code = "class Evil:\n    __getattribute__ = lambda self, name: None\n"
        with pytest.raises(RuntimeError, match="__getattribute__"):
            run_sandboxed(code, config, use_process=True)

    def test_worker_blocks_nested_getattribute(self):
        """__getattribute__ hidden inside a conditional must be caught."""
        config = SandboxConfig(strict=False)
        code = (
            "class Evil:\n"
            "    if True:\n"
            "        def __getattribute__(self, name):\n"
            "            return object.__getattribute__(self, name)\n"
        )
        with pytest.raises(RuntimeError, match="__getattribute__"):
            run_sandboxed(code, config, use_process=True)

    def test_worker_blocks_descriptor_get_method(self):
        """Descriptor __get__ must not run through C-level attribute access."""
        config = SandboxConfig(strict=False)
        code = (
            "class Desc:\n"
            "    def __get__(self, obj, objtype=None):\n"
            "        return 7\n"
            "class Holder:\n"
            "    x = Desc()\n"
            "__result__ = Holder().x\n"
        )
        with pytest.raises(RuntimeError, match="__get__"):
            run_sandboxed(code, config, use_process=True)

    def test_worker_blocks_index_method(self):
        """__index__ must not run through Python's list indexing internals."""
        config = SandboxConfig(strict=False)
        code = (
            "class I:\n"
            "    def __index__(self):\n"
            "        return 0\n"
            "__result__ = [99][I()]\n"
        )
        with pytest.raises(RuntimeError, match="__index__"):
            run_sandboxed(code, config, use_process=True)

    def test_worker_allows_normal_class(self):
        """Classes without __getattribute__ must be allowed in non-strict mode."""
        config = SandboxConfig(strict=False)
        code = (
            "class Point:\n"
            "    def __init__(self, x):\n"
            "        self.x = x\n"
            "p = Point(42)\n"
            "__result__ = p.x\n"
        )
        result, _ = run_sandboxed(code, config, use_process=True)
        assert result == 42


class TestModuleProxyLeakRegression:
    """Regression tests for F1: module proxy must not leak non-allowlisted modules."""

    def test_typing_sys_blocked_thread(self):
        """typing.sys must not leak the raw sys module (thread sandbox)."""
        config = SandboxConfig(strict=False)
        code = "import typing\n__result__ = typing.sys"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config, use_process=False)

    def test_typing_sys_blocked_process(self):
        """typing.sys must not leak the raw sys module (process sandbox)."""
        config = SandboxConfig(strict=False)
        code = "import typing\n__result__ = typing.sys"
        with pytest.raises(RuntimeError):
            run_sandboxed(code, config, use_process=True)

    def test_dataclasses_sys_blocked(self):
        """dataclasses.sys must not leak the raw sys module."""
        config = SandboxConfig(strict=False)
        code = "import dataclasses\n__result__ = dataclasses.sys"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config, use_process=False)

    def test_typing_sys_modules_builtins_open_blocked(self):
        """typing.sys.modules['builtins'].open() must be unreachable."""
        config = SandboxConfig(strict=False)
        code = (
            "import typing\n"
            "__result__ = typing.sys.modules['builtins'].open('/etc/hostname').read()"
        )
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config, use_process=False)

    def test_dataclasses_inspect_blocked(self):
        """dataclasses.inspect must not leak the raw inspect module."""
        config = SandboxConfig(strict=False)
        code = "import dataclasses\n__result__ = dataclasses.inspect"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config, use_process=False)

    def test_allowlisted_submodule_access_allowed(self):
        """Attribute access that returns an allowlisted module should work."""
        config = SandboxConfig(strict=False)
        # math.e is a float, not a module — ensure basic access still works
        code = "import math\n__result__ = math.e"
        result, _ = run_sandboxed(code, config, use_process=False)
        assert isinstance(result, float)

    @pytest.mark.parametrize(
        ("module_name", "attribute"),
        [
            ("dataclasses", "make_dataclass"),
            ("dataclasses", "replace"),
            ("functools", "update_wrapper"),
            ("functools", "wraps"),
        ],
    )
    @pytest.mark.parametrize("use_process", [False, True])
    def test_hidden_attribute_helpers_are_blocked(
        self, module_name, attribute, use_process
    ):
        """Trusted helpers that perform hidden traversal must not be exposed."""
        code = f"import {module_name}\n__result__ = {module_name}.{attribute}\n"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(
                code,
                SandboxConfig(strict=False),
                use_process=use_process,
            )

    @pytest.mark.parametrize("use_process", [False, True])
    def test_callable_gadgets_cannot_leak_function_globals(self, use_process):
        """Composable stdlib helpers must not expose raw function globals."""
        code = """
import dataclasses
import functools
name = ''.join(['__glo', 'bals__'])
def init(self, **changes):
    self.public = changes[next(iter(changes))]
Leak = dataclasses.make_dataclass(
    'Leak',
    [name],
    namespace={'__init__': init},
)
value = Leak(**{name: None})
functools.update_wrapper(
    value,
    dataclasses.dataclass,
    assigned=(name,),
    updated=(),
)
leaked = dataclasses.replace(value).public
__result__ = leaked['sys'].modules['builtins'].open('pyproject.toml').read(1)
"""

        assert validate_code_safety(code) == []
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(
                code,
                SandboxConfig(strict=True),
                use_process=use_process,
            )

    @pytest.mark.parametrize(
        "constructor",
        ["abc.ABCMeta", "typing.ABCMeta", "typing.NamedTupleMeta"],
    )
    @pytest.mark.parametrize("use_process", [False, True])
    def test_metaclass_constructors_are_blocked(self, constructor, use_process):
        module = constructor.partition(".")[0]
        code = (
            f"import {module}\n"
            "name = ''.join(['__get', 'attribute__'])\n"
            f"C = {constructor}('C', (dict,), {{name: dict.__getitem__}})\n"
            "__result__ = C(public='escaped').public\n"
        )

        assert validate_code_safety(code) == []
        with pytest.raises((SecurityViolation, RuntimeError), match="metaclass"):
            run_sandboxed(
                code,
                SandboxConfig(strict=True),
                use_process=use_process,
            )

    @pytest.mark.parametrize(
        "code",
        [
            (
                "name = ''.join(['__get', 'attribute__'])\n"
                "T = type(type(0))\n"
                "C = T('C', (dict,), {name: dict.__getitem__})\n"
                "__result__ = C(public='escaped').public\n"
            ),
            (
                "import typing\n"
                "name = ''.join(['__get', 'attribute__'])\n"
                "T = typing.get_origin(typing.Type[int])\n"
                "C = T('C', (dict,), {name: dict.__getitem__})\n"
                "__result__ = C(public='escaped').public\n"
            ),
        ],
    )
    @pytest.mark.parametrize("use_process", [False, True])
    def test_metaclass_results_are_blocked(self, code, use_process):
        assert validate_code_safety(code) == []
        with pytest.raises((SecurityViolation, RuntimeError), match="metaclass"):
            run_sandboxed(
                code,
                SandboxConfig(strict=True),
                use_process=use_process,
            )

    @pytest.mark.skipif(
        not _has_typing_extensions,
        reason="typing_extensions not installed",
    )
    @pytest.mark.parametrize("use_process", [False, True])
    def test_typing_extensions_metaclass_alias_is_blocked(self, use_process):
        import typing_extensions

        if not hasattr(typing_extensions, "GenericMeta"):
            pytest.skip("typing_extensions.GenericMeta is unavailable")
        code = (
            "import typing_extensions\n"
            "name = ''.join(['__get', 'attribute__'])\n"
            "C = typing_extensions.GenericMeta("
            "'C', (dict,), {name: dict.__getitem__})\n"
            "__result__ = C(public='escaped').public\n"
        )

        assert validate_code_safety(code) == []
        with pytest.raises((SecurityViolation, RuntimeError), match="metaclass"):
            run_sandboxed(
                code,
                SandboxConfig(strict=True),
                use_process=use_process,
            )


class TestForwardRefEvalRegression:
    """Regression tests for F2: typing.ForwardRef._evaluate() must not reach eval()."""

    def test_forward_ref_blocked_thread(self):
        """typing.ForwardRef must be blocked at the module proxy level."""
        config = SandboxConfig(strict=False)
        code = "import typing\nfr = typing.ForwardRef('int')"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config, use_process=False)

    def test_forward_ref_blocked_process(self):
        """typing.ForwardRef must be blocked in process sandbox."""
        config = SandboxConfig(strict=False)
        code = "import typing\nfr = typing.ForwardRef('int')"
        with pytest.raises(RuntimeError):
            run_sandboxed(code, config, use_process=True)

    def test_get_type_hints_blocked(self):
        """typing.get_type_hints must be blocked (evaluates annotations via eval)."""
        config = SandboxConfig(strict=False)
        code = "import typing\n__result__ = typing.get_type_hints"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config, use_process=False)

    def test_validator_flags_private_attr_access(self):
        """AST validator must flag _evaluate-style private attribute access."""
        warnings = validate_code_safety("fr._evaluate({}, {})")
        assert any("_evaluate" in w for w in warnings)

    def test_safe_typing_access_still_works(self):
        """Normal typing usage (Optional, List, etc.) must still work."""
        config = SandboxConfig(strict=False)
        code = "import typing\n__result__ = str(typing.Optional)"
        result, _ = run_sandboxed(code, config, use_process=False)
        assert "Optional" in result

    @pytest.mark.skipif(
        not _has_typing_extensions,
        reason="typing_extensions not installed — cannot test sandbox blocking",
    )
    def test_typing_extensions_forward_ref_blocked(self):
        """typing_extensions.ForwardRef must be blocked (same eval risk)."""
        config = SandboxConfig(strict=False)
        code = "import typing_extensions\nfr = typing_extensions.ForwardRef('int')"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config, use_process=False)

    @pytest.mark.skipif(
        not _has_typing_extensions,
        reason="typing_extensions not installed — cannot test sandbox blocking",
    )
    def test_typing_extensions_get_type_hints_blocked(self):
        """typing_extensions.get_type_hints must be blocked."""
        config = SandboxConfig(strict=False)
        code = "import typing_extensions\n__result__ = typing_extensions.get_type_hints"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config, use_process=False)

    @pytest.mark.skipif(
        not _has_typing_extensions,
        reason="typing_extensions not installed — cannot test sandbox blocking",
    )
    def test_typing_extensions_evaluate_forward_ref_blocked(self):
        """typing_extensions.evaluate_forward_ref must be blocked."""
        config = SandboxConfig(strict=False)
        code = "import typing_extensions\n__result__ = typing_extensions.evaluate_forward_ref"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config, use_process=False)


class TestCapabilityFailClosedRegression:
    """Regression tests for F3: host callbacks must not be installed without explicit
    capability opt-in."""

    def test_host_callback_denied_without_capabilities(self):
        """host_callbacks with capabilities=None must NOT install gated callbacks."""
        from geno.api import RunConfig, run

        source = """
        func main() -> String
            return fs_read_text(path: "/secret")
        end func
        """
        result = run(
            source,
            config=RunConfig(
                host_callbacks={"fs_read_text": lambda path: "LEAK:" + path},
            ),
        )
        # Must fail — capabilities=None should not grant fs access
        assert result.ok is False

    def test_host_callback_works_with_explicit_capability(self):
        """host_callbacks WITH explicit fs capability should work."""
        from geno.api import RunConfig, run

        source = """
        func main() -> String
            return fs_read_text(path: "/test.txt")
        end func
        """
        result = run(
            source,
            config=RunConfig(
                capabilities={"fs"},
                host_callbacks={"fs_read_text": lambda path: "content:" + path},
            ),
        )
        assert result.ok is True
        assert result.value == "content:/test.txt"


class TestServerEmptyCapabilitiesRegression:
    """Regression tests for F4: allowed_capabilities=set() must truly disable all."""

    def test_empty_set_disables_all(self):
        """create_handler with allowed_capabilities=set() must allow nothing."""
        from geno.monitoring import RuntimeMetricsCollector
        from geno.server import create_handler

        collector = RuntimeMetricsCollector()
        handler = create_handler(collector, allowed_capabilities=set())
        assert callable(handler)
        # The handler is a closure; verify the bound 'allowed' set is empty
        # by checking the code path through _coerce_capabilities
        from geno.server import _coerce_capabilities

        result = _coerce_capabilities({}, set())
        assert result == set()

    def test_none_allows_explicit_default_capabilities(self):
        """create_handler with allowed_capabilities=None must allow explicit defaults."""
        from geno.server import DEFAULT_ALLOWED_CAPABILITIES, _coerce_capabilities

        result = _coerce_capabilities(
            {"capabilities": ["print"]}, DEFAULT_ALLOWED_CAPABILITIES
        )
        assert result == {"print"}


class TestHtmlEscapeRegression:
    """Regression tests for F5: compile_to_html must escape title."""

    def test_title_escaped(self):
        """XSS payload in title must be escaped in output HTML."""
        from geno.js_compiler import compile_to_html

        xss_title = "</title><script>alert(1)</script><title>"
        source = """
        func main() -> Int
            return 0
        end func
        """
        html = compile_to_html(source, title=xss_title)
        # The injected script must not appear verbatim in the <title> area
        assert "</title><script>alert(1)</script>" not in html
        # The escaped form must be present
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html

    def test_normal_title_works(self):
        """Normal title should appear correctly."""
        from geno.js_compiler import compile_to_html

        source = """
        func main() -> Int
            return 0
        end func
        """
        html = compile_to_html(source, title="My App")
        assert "<title>My App</title>" in html

    def test_canvas_dimensions_reject_attribute_injection(self):
        """Canvas dimensions must not be able to break out of attributes."""
        from typing import Any

        from geno.js_compiler import compile_to_html

        source = """
        func main() -> Int
            return 0
        end func
        """
        bad_dimension: Any = '1" autofocus onfocus="alert(1)'

        with pytest.raises(ValueError, match="width must be an integer"):
            compile_to_html(source, width=bad_dimension)

        with pytest.raises(ValueError, match="height must be an integer"):
            compile_to_html(source, height=bad_dimension)

    def test_inline_script_breakout_escaped_for_single_file_html(self):
        """Compiled single-file HTML must not expose raw closing script tags."""
        from geno.js_compiler import compile_to_html

        payload = "</script><script>alert(1)</script>"
        source = f'''
        func main() -> String
            return "{payload}"
        end func
        '''

        html = compile_to_html(source)

        assert payload not in html
        assert "\\x3C/script>\\x3Cscript>alert(1)\\x3C/script>" in html

    def test_fstring_inline_script_breakout_escaped_for_single_file_html(self) -> None:
        """F-string literal segments must get the same script-context escaping."""
        from geno.js_compiler import compile_to_html

        payload = "</script><script>alert(1)</script>"
        source = f'''
        func main() -> String
            return f"{payload}"
        end func
        '''

        html = compile_to_html(source)

        assert payload not in html
        assert "\\x3C/script>\\x3Cscript>alert(1)\\x3C/script>" in html

    def test_inline_script_breakout_escaped_for_project_html(self, tmp_path):
        """Project HTML builds must also escape compiled script content."""
        from geno.dependency_graph import DependencyGraph
        from geno.js_compiler import compile_project_to_html
        from geno.project_graph import ProjectGraph

        payload = "</script><script>alert(1)</script>"
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Payload"]\n'
        )
        (tmp_path / "Payload.geno").write_text(
            f"export func message() -> String\n"
            f'  example () -> "{payload}"\n'
            f'  return "{payload}"\n'
            f"end func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Payload\nfunc main() -> String\n  return message()\nend func\n"
        )

        project = ProjectGraph.discover(tmp_path)
        dependency_graph = DependencyGraph.resolve(project)
        html = compile_project_to_html(dependency_graph)

        assert payload not in html
        assert "\\x3C/script>\\x3Cscript>alert(1)\\x3C/script>" in html

    def test_fstring_inline_script_breakout_escaped_for_project_html(
        self, tmp_path
    ) -> None:
        """Project HTML builds must escape f-string script terminators."""
        from geno.dependency_graph import DependencyGraph
        from geno.js_compiler import compile_project_to_html
        from geno.project_graph import ProjectGraph

        payload = "</script><script>alert(1)</script>"
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main", "Payload"]\n'
        )
        (tmp_path / "Payload.geno").write_text(
            f"export func message() -> String\n"
            f'  example () -> "{payload}"\n'
            f'  return f"{payload}"\n'
            f"end func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Payload\nfunc main() -> String\n  return message()\nend func\n"
        )

        project = ProjectGraph.discover(tmp_path)
        dependency_graph = DependencyGraph.resolve(project)
        html = compile_project_to_html(dependency_graph)

        assert payload not in html
        assert "\\x3C/script>\\x3Cscript>alert(1)\\x3C/script>" in html


class TestBuiltinCapabilityClassification:
    """Every builtin with a type signature must be either always-available or
    gated under a specific capability.  No builtin should fall through the
    cracks — that causes silent capability-denied errors on pure functions."""

    def test_every_builtin_is_classified(self):
        """No builtin should be neither always-available nor gated."""
        from geno.builtin_registry import (
            ALWAYS_AVAILABLE_BUILTINS,
            CAPABILITY_MAP,
            build_builtin_signatures,
        )

        gated: set[str] = set()
        for builtins in CAPABILITY_MAP.values():
            gated.update(builtins)

        sigs = build_builtin_signatures()
        unclassified = set(sigs.keys()) - ALWAYS_AVAILABLE_BUILTINS - gated
        assert unclassified == set(), (
            f"Builtins with type signatures but no capability classification: "
            f"{sorted(unclassified)}"
        )

    def test_short_aliases_match_long_forms(self):
        """Short-form aliases must have the same capability classification as
        their long-form wrappers."""
        from geno.builtin_registry import ALWAYS_AVAILABLE_BUILTINS

        alias_pairs = [
            ("to_upper", "string_to_upper"),
            ("to_lower", "string_to_lower"),
            ("ends_with", "string_ends_with"),
            ("starts_with", "string_starts_with"),
            ("replace", "string_replace"),
            ("contains_substring", "string_contains"),
            ("split", "string_split"),
            ("join", "string_join"),
            ("trim", "string_trim"),
        ]
        for short, long in alias_pairs:
            assert (short in ALWAYS_AVAILABLE_BUILTINS) == (
                long in ALWAYS_AVAILABLE_BUILTINS
            ), f"Alias mismatch: {short} vs {long}"

    def test_pure_alias_works_under_capability_restriction(self):
        """Short-form pure builtins must work when capabilities are restricted."""
        from geno.api import RunConfig, run

        source = """
        func main() -> String
            return to_upper("hello")
        end func
        """
        result = run(source, config=RunConfig(capabilities={"print"}))
        assert result.ok is True, (
            f"to_upper failed under capability restriction: "
            f"{[str(d) for d in result.diagnostics]}"
        )
        assert result.value == "HELLO"

    def test_ends_with_works_under_capability_restriction(self):
        """ends_with must work under capability restriction (geno-check uses it)."""
        from geno.api import RunConfig, run

        source = """
        func main() -> Bool
            return ends_with("hello.geno", ".geno")
        end func
        """
        result = run(source, config=RunConfig(capabilities={"print"}))
        assert result.ok is True, (
            f"ends_with failed under capability restriction: "
            f"{[str(d) for d in result.diagnostics]}"
        )
        assert result.value is True

    def test_replace_works_under_capability_restriction(self):
        """replace must work under capability restriction (pure string op)."""
        from geno.api import RunConfig, run

        source = """
        func main() -> String
            return replace(text: "hello world", old: "world", new: "geno")
        end func
        """
        result = run(source, config=RunConfig(capabilities={"print"}))
        assert result.ok is True, (
            f"replace failed under capability restriction: "
            f"{[str(d) for d in result.diagnostics]}"
        )
        assert result.value == "hello geno"
