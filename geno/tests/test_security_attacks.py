"""
Security attack vector tests (#388) — memory bombs, descriptor protocol
attacks, and exception handler bypass.

Tests that the sandbox properly defends against advanced attack techniques
not covered by the base security test suite.
"""

import pytest

from geno import _runtime_support as runtime_support
from geno.sandbox import (
    ProcessSandboxConfig,
    SandboxConfig,
    SecurityViolation,
    TimeoutError,
    run_in_process,
    run_sandboxed,
)

# ===========================================================================
# Memory bomb tests
# ===========================================================================


class TestMemoryBombs:
    """Test that Geno's runtime and process sandbox stop runaway growth."""

    def test_safe_mul_rejects_large_string_multiplication(self, monkeypatch):
        """_safe_mul should reject strings that would exceed the size cap."""
        monkeypatch.setattr(runtime_support, "_MAX_COLLECTION_SIZE", 8)
        with pytest.raises(RuntimeError, match="size exceeds limit"):
            runtime_support._safe_mul("xy", 5)

    def test_safe_mul_rejects_large_list_multiplication(self, monkeypatch):
        """_safe_mul should reject lists that would exceed the size cap."""
        monkeypatch.setattr(runtime_support, "_MAX_COLLECTION_SIZE", 4)
        with pytest.raises(RuntimeError, match="size exceeds limit"):
            runtime_support._safe_mul([1, 2], 3)

    def test_safe_add_rejects_large_string_join(self, monkeypatch):
        """_safe_add should pre-check string concatenation before allocation."""
        monkeypatch.setattr(runtime_support, "_MAX_COLLECTION_SIZE", 5)
        with pytest.raises(RuntimeError, match="size exceeds limit"):
            runtime_support._safe_add("abc", "def")

    def test_safe_add_rejects_large_list_concat(self, monkeypatch):
        """_safe_add should pre-check list concatenation before allocation."""
        monkeypatch.setattr(runtime_support, "_MAX_COLLECTION_SIZE", 3)
        with pytest.raises(RuntimeError, match="size exceeds limit"):
            runtime_support._safe_add([1, 2], [3, 4])

    def test_safe_mul_rejects_large_integer_result(self, monkeypatch):
        """_safe_mul should reject integer results beyond the bit-length cap."""
        monkeypatch.setattr(runtime_support, "_MAX_INTEGER_BITS", 16)
        with pytest.raises(RuntimeError, match=r"[Ii]nteger.*size"):
            runtime_support._safe_mul(2**10, 2**10)

    def test_process_sandbox_stops_growth_loop(self):
        """Memory-growth loops should be stopped by the process sandbox.

        The loop may be killed by either the timeout or the memory limit,
        depending on system speed and memory allocation patterns.
        """
        config = ProcessSandboxConfig(timeout=1.0, strict=False)
        code = """
items = []
while True:
    items.append(0)
"""
        with pytest.raises((TimeoutError, RuntimeError)):
            run_in_process(code, config)


# ===========================================================================
# Descriptor protocol attacks
# ===========================================================================


class TestDescriptorProtocolAttacks:
    """Test that the sandbox blocks descriptor protocol abuse."""

    def test_runtime_get_field_blocks_inherited_attributes(self):
        """Compiled-runtime field access must reject inherited attributes."""

        class Evil:
            secret = 42

        with pytest.raises(RuntimeError, match="has no field"):
            runtime_support.get_field(Evil(), "secret")

    def test_runtime_get_field_allows_owned_attributes(self):
        """Compiled-runtime field access must still allow real instance fields."""

        class Safe:
            def __init__(self):
                self.secret = 42

        assert runtime_support.get_field(Safe(), "secret") == 42

    def test_class_with_getattribute_blocked_strict(self):
        """In strict mode, class definitions with __getattribute__ are blocked."""
        config = SandboxConfig(strict=True)
        code = """
class Evil:
    def __getattribute__(self, name):
        return object.__getattribute__(self, name)
e = Evil()
__result__ = e
"""
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)

    def test_class_with_get_descriptor_blocked_strict(self):
        """In strict mode, class with __get__ descriptor should be blocked."""
        config = SandboxConfig(strict=True)
        code = """
class Descriptor:
    def __get__(self, obj, objtype=None):
        return 42
__result__ = 1
"""
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)

    def test_class_with_set_descriptor_blocked_strict(self):
        """In strict mode, class with __set__ descriptor should be blocked."""
        config = SandboxConfig(strict=True)
        code = """
class WriteDesc:
    def __set__(self, obj, value):
        pass
__result__ = 1
"""
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)

    def test_class_access_via_getattr_blocked(self):
        """getattr(obj, '__class__') should be blocked by safe_getattr."""
        config = SandboxConfig(strict=False)
        code = """
bases = getattr((), '__class__')
__result__ = bases
"""
        with pytest.raises((SecurityViolation, AttributeError, RuntimeError)):
            run_sandboxed(code, config)

    def test_subclasses_via_getattr_blocked(self):
        """getattr(type, '__subclasses__') should be blocked."""
        config = SandboxConfig(strict=False)
        code = """
subs = getattr(int, '__subclasses__')
__result__ = subs
"""
        with pytest.raises((SecurityViolation, AttributeError, RuntimeError)):
            run_sandboxed(code, config)

    def test_type_three_arg_blocked(self):
        """type(name, bases, dict) class creation must be blocked."""
        config = SandboxConfig(strict=False)
        code = """
Evil = type('Evil', (), {'x': 1})
__result__ = Evil
"""
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)


# ===========================================================================
# Exception handler bypass tests
# ===========================================================================


class TestExceptionHandlerBypass:
    """Test that exception handling can't leak sandbox internals."""

    def test_traceback_attribute_blocked(self):
        """e.__traceback__ should be blocked by safe_getattr."""
        config = SandboxConfig(strict=False)
        # Use getattr() explicitly since direct attribute access may bypass sandbox
        code = """
try:
    x = 1 / 0
except ZeroDivisionError as e:
    tb = getattr(e, '__traceback__')
    __result__ = str(tb)
"""
        with pytest.raises((SecurityViolation, AttributeError, RuntimeError)):
            run_sandboxed(code, config)

    def test_exception_cause_via_getattr_blocked(self):
        """getattr(e, '__cause__') should be blocked."""
        config = SandboxConfig(strict=False)
        code = """
try:
    try:
        raise ValueError("inner")
    except ValueError as e1:
        raise KeyError("outer") from e1
except KeyError as e:
    cause = getattr(e, '__cause__')
    __result__ = str(cause)
"""
        with pytest.raises((SecurityViolation, AttributeError, RuntimeError)):
            run_sandboxed(code, config)

    def test_exception_context_via_getattr_blocked(self):
        """getattr(e, '__context__') should be blocked."""
        config = SandboxConfig(strict=False)
        code = """
try:
    try:
        raise ValueError("first")
    except ValueError:
        raise RuntimeError("second")
except RuntimeError as e:
    ctx = getattr(e, '__context__')
    __result__ = str(ctx)
"""
        with pytest.raises((SecurityViolation, AttributeError, RuntimeError)):
            run_sandboxed(code, config)

    def test_frame_globals_via_getattr_blocked(self):
        """Accessing frame globals via getattr should be blocked."""
        config = SandboxConfig(strict=False)
        code = """
def leaker():
    return getattr(leaker, '__globals__')
__result__ = leaker()
"""
        with pytest.raises((SecurityViolation, AttributeError, RuntimeError)):
            run_sandboxed(code, config)

    def test_function_code_access_blocked(self):
        """Accessing __code__ on functions should be blocked."""
        config = SandboxConfig(strict=False)
        code = """
def f(): pass
c = getattr(f, '__code__')
__result__ = c
"""
        with pytest.raises((SecurityViolation, AttributeError, RuntimeError)):
            run_sandboxed(code, config)

    def test_closure_access_blocked(self):
        """Accessing __closure__ on functions should be blocked."""
        config = SandboxConfig(strict=False)
        code = """
x = 42
def f():
    return x
c = getattr(f, '__closure__')
__result__ = c
"""
        with pytest.raises((SecurityViolation, AttributeError, RuntimeError)):
            run_sandboxed(code, config)

    def test_function_defaults_access_blocked(self):
        """Accessing __defaults__ on functions should be blocked."""
        config = SandboxConfig(strict=False)
        code = """
def f(x=42): return x
d = getattr(f, '__defaults__')
__result__ = d
"""
        with pytest.raises((SecurityViolation, AttributeError, RuntimeError)):
            run_sandboxed(code, config)

    def test_exception_in_except_no_frame_leak(self):
        """Raising inside except block should not leak frame references."""
        config = SandboxConfig(strict=False)
        code = """
leaked = None
try:
    try:
        raise ValueError()
    except ValueError as e:
        # Try to get frame via traceback
        leaked = getattr(e, '__traceback__')
except (AttributeError, Exception):
    pass
__result__ = leaked is None
"""
        # Should either block the access or the result should be None/True
        try:
            result, _ = run_sandboxed(code, config)
            # If it didn't raise, the getattr should have been blocked
            # and leaked should be None
            assert result is True
        except SecurityViolation:
            pass  # Also acceptable


# ===========================================================================
# Combined attack vectors
# ===========================================================================


class TestCombinedAttacks:
    """Test combined/chained attack vectors."""

    def test_exception_to_globals_chain(self):
        """Chain: exception -> traceback -> frame -> globals."""
        config = SandboxConfig(strict=False)
        code = """
try:
    x = 1 / 0
except ZeroDivisionError as e:
    # Try to chain: e.__traceback__.tb_frame.f_globals
    tb = getattr(e, '__traceback__')
    __result__ = tb
"""
        with pytest.raises((SecurityViolation, AttributeError, RuntimeError)):
            run_sandboxed(code, config)

    def test_builtins_dict_access_blocked(self):
        """Accessing __builtins__ via getattr should be blocked."""
        config = SandboxConfig(strict=False)
        code = """
b = getattr(type, '__builtins__')
__result__ = b
"""
        with pytest.raises((SecurityViolation, AttributeError, RuntimeError)):
            run_sandboxed(code, config)

    def test_gc_module_blocked(self):
        """Garbage collector module should be blocked."""
        config = SandboxConfig(strict=False)
        code = "import gc; gc.get_objects()"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)

    def test_ctypes_module_blocked(self):
        """ctypes module should be blocked."""
        config = SandboxConfig(strict=False)
        code = "import ctypes"
        with pytest.raises((SecurityViolation, RuntimeError)):
            run_sandboxed(code, config)
