"""
Geno Sandbox
================

Provides a secure execution environment for Geno programs.
Restricts access to dangerous operations like file I/O, network,
and system commands.
"""

import ast
import base64
import hashlib
import json
import logging
import math
import os
import platform
import signal
import subprocess
import sys
import threading
import types as _types
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Optional

from .execution_limits import DEFAULT_INTERPRETER_MAX_STEPS

logger = logging.getLogger(__name__)


def _sandbox_debug_enabled() -> bool:
    """Return True when GENO_SANDBOX_DEBUG requests worker traceback capture."""
    return os.environ.get("GENO_SANDBOX_DEBUG", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class SandboxError(Exception):
    """Base exception for sandbox violations."""

    pass


class SecurityViolation(SandboxError):
    """Raised when code attempts a blocked operation."""

    pass


class ResourceLimitExceeded(SandboxError):
    """Raised when resource limits are exceeded."""

    pass


class TimeoutError(ResourceLimitExceeded):
    """Raised when execution exceeds time limit."""

    pass


class RecursionLimitError(ResourceLimitExceeded):
    """Raised when recursion limit is exceeded."""

    pass


class StepLimitExceeded(ResourceLimitExceeded):
    """Raised when the step budget is exhausted."""

    pass


def _validate_optional_positive_number(
    value: float | int | None,
    field_name: str,
) -> None:
    if value is None:
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{field_name} must be a positive finite number or None")


def _validate_optional_positive_int(value: int | None, field_name: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer or None")


def _validate_positive_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def _validate_non_negative_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _validate_bool(value: bool, field_name: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")


@dataclass
class SandboxConfig:
    """Configuration for sandbox execution."""

    # Time limit in seconds (None = no limit)
    timeout: float | None = 5.0

    # ProcessSandbox-only address-space limit in bytes (None = no limit)
    max_memory_bytes: int | None = 256 * 1024 * 1024

    # ProcessSandbox-only CPU time limit in seconds (None = no limit)
    max_cpu_time: float | None = None

    # ProcessSandbox-only maximum file size in bytes (0 = no file writing)
    max_file_size_bytes: int = 0

    # ProcessSandbox-only maximum number of processes/threads
    max_processes: int = 1

    # Maximum recursion depth
    max_recursion_depth: int = 500

    # Maximum output length (characters)
    max_output_length: int = 100000

    # Allow print statements (captured to buffer)
    allow_print: bool = True

    # Maximum number of interpreter steps (None = no cooperative step limit).
    # Applies only to the interpreter / thread-sandbox path. The
    # ProcessSandbox path does not step-meter compiled Python — it
    # relies on wall-clock, memory, collection-size, integer-bit, and
    # recursion bounds instead.
    max_steps: int | None = DEFAULT_INTERPRETER_MAX_STEPS

    # Maximum size for strings and lists (characters / elements)
    max_collection_size: int = 10_000_000

    # Maximum bit length for integer arithmetic results (~10,000 digits)
    max_integer_bits: int = 33_219

    # Strict mode: raise on any suspicious operation
    strict: bool = True

    # Compiler-generated Python needs selected runtime-prelude symbols
    # pre-injected when the sandbox strips the leading import block.
    #
    # SECURITY: enabling this relaxes the worker's AST validator
    # (blocked-attribute access, format/format_map calls, blocked-builtin
    # calls) on the assumption that the input is the Geno compiler's
    # output. Set to True *only* for compiler-emitted Python; there is
    # no structural proof that incoming code actually is.
    compiled_runtime_prelude: bool = False
    # Number of leading source lines trusted as compiler-emitted runtime
    # prelude when compiled_runtime_prelude is true.  Worker AST validation
    # still applies to all following generated program-body code.
    trusted_prelude_line_count: int = 0

    def __post_init__(self) -> None:
        _validate_optional_positive_number(self.timeout, "SandboxConfig.timeout")
        _validate_optional_positive_int(
            self.max_memory_bytes, "SandboxConfig.max_memory_bytes"
        )
        _validate_optional_positive_number(
            self.max_cpu_time, "SandboxConfig.max_cpu_time"
        )
        _validate_non_negative_int(
            self.max_file_size_bytes, "SandboxConfig.max_file_size_bytes"
        )
        _validate_positive_int(self.max_processes, "SandboxConfig.max_processes")
        _validate_positive_int(
            self.max_recursion_depth, "SandboxConfig.max_recursion_depth"
        )
        _validate_non_negative_int(
            self.max_output_length, "SandboxConfig.max_output_length"
        )
        _validate_bool(self.allow_print, "SandboxConfig.allow_print")
        _validate_optional_positive_int(self.max_steps, "SandboxConfig.max_steps")
        _validate_non_negative_int(
            self.max_collection_size, "SandboxConfig.max_collection_size"
        )
        _validate_positive_int(self.max_integer_bits, "SandboxConfig.max_integer_bits")
        _validate_bool(self.strict, "SandboxConfig.strict")
        _validate_bool(
            self.compiled_runtime_prelude,
            "SandboxConfig.compiled_runtime_prelude",
        )
        _validate_non_negative_int(
            self.trusted_prelude_line_count,
            "SandboxConfig.trusted_prelude_line_count",
        )


# =============================================================================
# Safe Builtins
# =============================================================================


def _safe_type(*args):
    """Restricted type() that only allows single-argument (type query) form.

    The three-argument form ``type(name, bases, namespace)`` creates a new
    class.  A class with a custom ``__getattribute__`` would bypass the
    sandbox's ``safe_getattr`` wrapper at the C level, enabling full escape.
    """
    if len(args) == 1:
        return type(args[0])
    raise SecurityViolation(
        "type() with multiple arguments is not allowed in sandbox "
        "(class creation could bypass attribute access controls)"
    )


# These builtins are safe for sandboxed execution
SAFE_BUILTINS = {
    # Types
    "bool": bool,
    "int": int,
    "float": float,
    "str": str,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "frozenset": frozenset,
    "type": _safe_type,
    # Type checking
    "isinstance": isinstance,
    "issubclass": issubclass,
    "callable": callable,
    # Iteration
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "reversed": reversed,
    "sorted": sorted,
    # Aggregation
    "len": len,
    "sum": sum,
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
    "all": all,
    "any": any,
    # String/repr
    "repr": repr,
    "ascii": ascii,
    "chr": chr,
    "ord": ord,
    # "format" deliberately excluded: format() calls __format__ which is
    # mostly safe, but keeping it out reduces the attack surface.  The
    # dangerous vector is str.format() / str.format_map() which perform
    # C-level attribute traversal (bypassing safe_getattr).
    # Math
    "divmod": divmod,
    # Boolean
    "True": True,
    "False": False,
    "None": None,
    # Exceptions (safe subset)
    "Exception": Exception,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "IndexError": IndexError,
    "KeyError": KeyError,
    "RuntimeError": RuntimeError,
    "StopIteration": StopIteration,
    "AssertionError": AssertionError,
    "AttributeError": AttributeError,
    "ZeroDivisionError": ZeroDivisionError,
    "NameError": NameError,
    # Other safe operations
    "hash": hash,
    "iter": iter,
    "next": next,
    "slice": slice,
    # __build_class__ deliberately excluded from SAFE_BUILTINS:
    # In non-strict mode, `class Foo: __getattribute__ = ...` bypasses
    # safe_getattr at the C level.  Only compile_and_exec injects it
    # into its own globals_dict for the runtime prelude.
}

# These builtins are explicitly BLOCKED
BLOCKED_BUILTINS = {
    # Code execution
    "eval",
    "exec",
    "compile",
    "__import__",
    # File operations
    "open",
    "input",
    # Dangerous introspection
    "globals",
    "locals",
    "vars",
    "dir",
    "setattr",
    "delattr",
    # Note: getattr and hasattr are replaced with safe versions, not blocked
    # Memory/object manipulation
    "memoryview",
    "bytearray",
    "bytes",
    # Other dangerous
    "breakpoint",
    "help",
    "license",
    "credits",
    "copyright",
    "quit",
    "exit",
}

# These modules are explicitly BLOCKED from import
BLOCKED_MODULES = {
    # System access
    "os",
    "sys",
    "subprocess",
    "shutil",
    "pathlib",
    "builtins",
    # Network
    "socket",
    "http",
    "urllib",
    "requests",
    "aiohttp",
    "ftplib",
    "smtplib",
    "poplib",
    "imaplib",
    "telnetlib",
    # Code execution
    "code",
    "codeop",
    "compileall",
    "py_compile",
    "importlib",
    "pkgutil",
    "modulefinder",
    # Process/threading
    "multiprocessing",
    "threading",
    "concurrent",
    "_thread",
    "queue",
    # File/IO
    # "io" deliberately excluded: it is in _SAFE_IMPORT_ALLOWLIST with
    # dangerous functions (open, FileIO, etc.) blocked by the module proxy
    # via _MODULE_BLOCKED_FUNCTIONS.  Listing it here causes a false
    # positive in strict-mode static validation.
    "tempfile",
    "fileinput",
    "linecache",
    "pickle",
    "shelve",
    "marshal",
    "dbm",
    # Dangerous stdlib
    "ctypes",
    "gc",
    "inspect",
    "traceback",
    "atexit",
    "signal",
    "resource",
    # Crypto C-extension library (large, hard-to-audit attack surface)
    "cryptography",
}

# Dangerous attribute names that could be used to escape the sandbox
BLOCKED_ATTRIBUTES = frozenset(
    {
        "__class__",
        "__bases__",
        "__mro__",
        "__subclasses__",
        "__globals__",
        "__code__",
        "__builtins__",
        "__dict__",
        "__self__",
        "__func__",
        "__closure__",
        "__annotations__",
        "__kwdefaults__",
        "__defaults__",
        "__module__",
        "__qualname__",
        "__wrapped__",
        "__init_subclass__",
        "__set_name__",
        "__getattribute__",
        "__subclasshook__",
        # str.format() and str.format_map() perform C-level attribute
        # traversal that bypasses safe_getattr.  Blocking them here
        # prevents getattr(str_obj, "format") from succeeding.
        "format",
        "format_map",
        "gi_frame",
        "gi_code",
        "cr_frame",
        "cr_code",
        "ag_frame",
        "ag_code",
        "f_globals",
        "f_locals",
        "f_builtins",
        "f_code",
        # Traceback/exception chain attributes — e.__traceback__ gives access
        # to frame objects (tb_frame → f_globals → sandbox internals).
        "__traceback__",
        "__cause__",
        "__context__",
        "__suppress_context__",
        "tb_frame",
        "tb_next",
        "tb_lineno",
    }
)


# Dunder attributes that are safe to access inside the sandbox.
# This single constant is the source of truth used by the thread sandbox,
# the module-level safe_getattr, and the process sandbox worker script.
SAFE_DUNDERS = frozenset(
    {
        "__init__",
        "__str__",
        "__repr__",
        "__eq__",
        "__hash__",
        "__len__",
        "__iter__",
        "__next__",
        "__getitem__",
        "__contains__",
        "__add__",
        "__sub__",
        "__mul__",
        "__truediv__",
        "__floordiv__",
        "__mod__",
        "__pow__",
        "__neg__",
        "__pos__",
        "__abs__",
        "__bool__",
        "__lt__",
        "__le__",
        "__gt__",
        "__ge__",
        "__ne__",
        "__call__",
        "__name__",
        "__doc__",
    }
)


def _create_blocked_function(name: str) -> Callable:
    """Create a function that raises SecurityViolation when called."""

    def blocked(*args: object, **kwargs: object) -> None:
        raise SecurityViolation(
            f"Blocked operation: {name}() is not allowed in sandbox"
        )

    blocked.__name__ = name
    blocked.__doc__ = f"BLOCKED: {name} is not available in sandboxed execution"
    return blocked


def _create_safe_print(config: SandboxConfig, output_buffer: list) -> Callable:
    """Create a sandboxed print function that captures output."""
    total_length = [0]  # Use list for mutable closure

    def safe_print(
        *args: object, sep: str = " ", end: str = "\n", **kwargs: object
    ) -> None:
        if not config.allow_print:
            raise SecurityViolation("print() is disabled in this sandbox")

        # Ignore file parameter
        if "file" in kwargs:
            raise SecurityViolation("print() with file parameter is not allowed")

        output = sep.join(str(arg) for arg in args) + end
        total_length[0] += len(output)

        if total_length[0] > config.max_output_length:
            raise ResourceLimitExceeded(
                f"Output limit exceeded ({config.max_output_length} characters)"
            )

        output_buffer.append(output)

    return safe_print


_SAFE_IMPORT_ALLOWLIST = {
    # "asyncio" deliberately excluded: exposes subprocess/networking via
    # asyncio.create_subprocess_exec and asyncio.open_connection.
    "csv",
    "dataclasses",
    "hashlib",
    "hmac",
    "io",
    "typing",
    "math",
    "secrets",
    "copy",
    "functools",
    "collections",
    "abc",
    # "types" deliberately excluded: types.CodeType + types.FunctionType allow
    # constructing arbitrary bytecode and functions with custom globals dicts,
    # enabling complete sandbox escape at the VM level.
    # "re" deliberately excluded: sandboxed compiled execution pre-injects
    # the runtime's private _re binding when needed, so user code never
    # needs to import re directly. Keeping it out prevents bypassing the
    # builtin-level ReDoS mitigations (pattern/text caps, nested quantifier
    # and backreference rejection).
    # "operator" deliberately excluded: operator.attrgetter / itemgetter /
    # methodcaller perform C-level attribute access that bypasses safe_getattr
    # and the _ModuleProxy, enabling class-hierarchy sandbox escapes.
    "itertools",
    "typing_extensions",
    "tomllib",
    "tomli",
}


# Functions on specific modules that are blocked due to denial-of-service
# risk (e.g. math.factorial on huge inputs runs at C level indefinitely).
_MODULE_BLOCKED_FUNCTIONS: dict[str, frozenset[str]] = {
    "math": frozenset({"factorial", "comb", "perm"}),
    "hashlib": frozenset({"scrypt", "pbkdf2_hmac"}),
    "io": frozenset(
        {
            "open",
            "open_code",
            "FileIO",
            "OpenWrapper",
            "BufferedReader",
            "BufferedWriter",
            "BufferedRandom",
            "BufferedRWPair",
            "RawIOBase",
            "BufferedIOBase",
        }
    ),
}

# Attributes on specific modules that are blocked because the returned
# objects can reach dangerous builtins (e.g. eval) through internal methods
# that bypass the sandbox's getattr wrapper.
_MODULE_BLOCKED_ATTRIBUTES: dict[str, frozenset[str]] = {
    "typing": frozenset(
        {
            "ForwardRef",  # _evaluate() calls real eval()
            "get_type_hints",  # evaluates string annotations via eval()
        }
    ),
    "typing_extensions": frozenset(
        {
            "ForwardRef",  # same as typing.ForwardRef; _evaluate() calls eval()
            "get_type_hints",  # evaluates string annotations via eval()
            "evaluate_forward_ref",  # calls typing._eval_type -> eval()
            "get_annotations",  # calls eval() on string annotations
        }
    ),
}


def _create_module_proxy(mod: Any, max_collection_size: int = 10_000_000) -> Any:
    """Wrap a module so attribute access is filtered even by C-level functions.

    ``operator.attrgetter`` and similar C helpers bypass the Python-level
    ``safe_getattr`` wrapper installed in the sandbox builtins.  By wrapping
    every imported module in a proxy whose ``__getattribute__`` enforces the
    same policy, we close that escape path.  The real module is captured by
    closure — not stored as an instance attribute — so sandbox code cannot
    reach it.
    """

    def _estimate_urlsafe_len(nbytes: int) -> int:
        return ((nbytes + 2) // 3) * 4

    def _wrap_shake_xof(obj: Any) -> Any:
        class _ShakeProxy:
            __slots__ = ()

            def __getattribute__(self, name: str) -> Any:
                if name in BLOCKED_ATTRIBUTES:
                    raise SecurityViolation(
                        f"Access to attribute '{name}' is not allowed in sandbox"
                    )
                if name.startswith("__") and name.endswith("__"):
                    if name not in SAFE_DUNDERS:
                        raise SecurityViolation(
                            f"Access to attribute '{name}' is not allowed in sandbox"
                        )
                elif name.startswith("_"):
                    raise SecurityViolation(
                        f"Access to private attribute '{name}' is not allowed in sandbox"
                    )
                value = getattr(obj, name)
                if name == "digest" and callable(value):

                    def guarded_digest(length: int, *args: Any, **kwargs: Any) -> Any:
                        if isinstance(length, int) and length > max_collection_size:
                            raise SecurityViolation(
                                "hashlib shake digest output exceeds sandbox "
                                "collection limit"
                            )
                        return value(length, *args, **kwargs)

                    return guarded_digest
                if name == "hexdigest" and callable(value):

                    def guarded_hexdigest(
                        length: int, *args: Any, **kwargs: Any
                    ) -> Any:
                        if isinstance(length, int) and length * 2 > max_collection_size:
                            raise SecurityViolation(
                                "hashlib shake hex output exceeds sandbox "
                                "collection limit"
                            )
                        return value(length, *args, **kwargs)

                    return guarded_hexdigest
                if name == "copy" and callable(value):

                    def guarded_copy(*args: Any, **kwargs: Any) -> Any:
                        return _wrap_shake_xof(value(*args, **kwargs))

                    return guarded_copy
                return value

            def __repr__(self) -> str:
                return "<sandboxed hashlib shake object>"

        return _ShakeProxy()

    def _wrap_module_callable(mod_name: str, attr_name: str, value: Any) -> Any:
        if mod_name == "secrets" and callable(value):
            if attr_name == "token_bytes":

                def guarded_token_bytes(*args: Any, **kwargs: Any) -> Any:
                    nbytes = args[0] if args else kwargs.get("nbytes", 32)
                    if nbytes is None:
                        nbytes = 32
                    if isinstance(nbytes, int) and nbytes > max_collection_size:
                        raise SecurityViolation(
                            "secrets.token_bytes output exceeds sandbox "
                            "collection limit"
                        )
                    return value(*args, **kwargs)

                return guarded_token_bytes
            if attr_name == "token_hex":

                def guarded_token_hex(*args: Any, **kwargs: Any) -> Any:
                    nbytes = args[0] if args else kwargs.get("nbytes", 32)
                    if nbytes is None:
                        nbytes = 32
                    if isinstance(nbytes, int) and nbytes * 2 > max_collection_size:
                        raise SecurityViolation(
                            "secrets.token_hex output exceeds sandbox collection limit"
                        )
                    return value(*args, **kwargs)

                return guarded_token_hex
            if attr_name == "token_urlsafe":

                def guarded_token_urlsafe(*args: Any, **kwargs: Any) -> Any:
                    nbytes = args[0] if args else kwargs.get("nbytes", 32)
                    if nbytes is None:
                        nbytes = 32
                    if (
                        isinstance(nbytes, int)
                        and _estimate_urlsafe_len(nbytes) > max_collection_size
                    ):
                        raise SecurityViolation(
                            "secrets.token_urlsafe output exceeds sandbox "
                            "collection limit"
                        )
                    return value(*args, **kwargs)

                return guarded_token_urlsafe
        if mod_name == "hashlib" and callable(value):
            if attr_name in {"shake_128", "shake_256"}:

                def guarded_shake_factory(*args: Any, **kwargs: Any) -> Any:
                    return _wrap_shake_xof(value(*args, **kwargs))

                return guarded_shake_factory
            if attr_name == "new":

                def guarded_new(*args: Any, **kwargs: Any) -> Any:
                    algorithm = args[0] if args else kwargs.get("name")
                    result = value(*args, **kwargs)
                    if isinstance(algorithm, str) and algorithm.lower() in {
                        "shake_128",
                        "shake_256",
                    }:
                        return _wrap_shake_xof(result)
                    return result

                return guarded_new
        return value

    class _ModuleProxy:
        __slots__ = ()

        def __getattribute__(self, name: str) -> Any:
            if name in BLOCKED_ATTRIBUTES:
                raise SecurityViolation(
                    f"Access to attribute '{name}' is not allowed in sandbox"
                )
            if name.startswith("__") and name.endswith("__"):
                if name not in SAFE_DUNDERS:
                    raise SecurityViolation(
                        f"Access to attribute '{name}' is not allowed in sandbox"
                    )
            elif name.startswith("_"):
                raise SecurityViolation(
                    f"Access to private attribute '{name}' is not allowed in sandbox"
                )
            mod_name = getattr(mod, "__name__", "")
            blocked_fns = _MODULE_BLOCKED_FUNCTIONS.get(mod_name, frozenset())
            if name in blocked_fns:
                raise SecurityViolation(
                    f"Access to '{mod_name}.{name}' is not allowed in sandbox"
                )
            blocked_attrs = _MODULE_BLOCKED_ATTRIBUTES.get(mod_name, frozenset())
            if name in blocked_attrs:
                raise SecurityViolation(
                    f"Access to '{mod_name}.{name}' is not allowed in sandbox"
                )
            value = getattr(mod, name)
            # Block module-type attributes whose top-level package is not
            # in the import allowlist.  This prevents leaking raw modules
            # through attribute chains (e.g. typing.sys → raw sys).
            if isinstance(value, _types.ModuleType):
                top = getattr(value, "__name__", "").split(".")[0]
                if top not in _SAFE_IMPORT_ALLOWLIST:
                    raise SecurityViolation(
                        f"Access to module '{top}' via attribute "
                        f"is not allowed in sandbox"
                    )
                return _create_module_proxy(value, max_collection_size)
            return _wrap_module_callable(mod_name, name, value)

        def __repr__(self) -> str:
            mod_name = getattr(mod, "__name__", "?")
            return f"<sandboxed module '{mod_name}'>"

    return _ModuleProxy()


def _create_safe_import(max_collection_size: int = 10_000_000) -> Callable:
    """Create a restricted __import__ that only allows whitelisted modules.

    Python internals (e.g. ``@dataclass``, ``typing``) call ``__import__``
    implicitly.  A hard block breaks normal execution on some Python versions.
    This allows a curated set of safe stdlib modules while still preventing
    ``import os``, ``import subprocess``, etc.

    Returned modules are wrapped in a proxy that enforces attribute-access
    filtering even for C-level helpers like ``operator.attrgetter``.
    """
    real_import = (
        __builtins__["__import__"]
        if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )  # type: ignore[union-attr]

    def safe_import(
        name: str,
        globals: dict | None = None,
        locals: dict | None = None,
        fromlist: tuple = (),
        level: int = 0,
    ) -> object:
        top_level = name.split(".")[0]
        if top_level in _SAFE_IMPORT_ALLOWLIST:
            mod = real_import(name, globals, locals, fromlist, level)
            return _create_module_proxy(mod, max_collection_size)
        if top_level in BLOCKED_MODULES:
            raise SecurityViolation(
                f"Blocked operation: import of '{name}' is not allowed in sandbox"
            )
        raise SecurityViolation(
            "Blocked operation: __import__() is not allowed in sandbox"
        )

    return safe_import


def create_safe_globals(config: SandboxConfig, output_buffer: list) -> dict:
    """Create a restricted globals dictionary for sandboxed execution."""
    builtins_dict: dict[str, Any] = {}
    safe_globals: dict[str, Any] = {
        "__builtins__": builtins_dict,
        "__name__": "__sandbox__",
        "__doc__": None,
    }

    # Add safe builtins
    for name, value in SAFE_BUILTINS.items():
        builtins_dict[name] = value

    # Add blocked builtins that raise errors
    for name in BLOCKED_BUILTINS:
        if name == "__import__":
            continue  # handled separately below
        builtins_dict[name] = _create_blocked_function(name)

    # Provide a whitelisted __import__ instead of blocking it entirely.
    # Python internals (e.g. @dataclass, typing) call __import__ implicitly
    # on some versions, so a hard block breaks normal execution.
    builtins_dict["__import__"] = _create_safe_import(config.max_collection_size)

    # Add safe print
    builtins_dict["print"] = _create_safe_print(config, output_buffer)

    # Add safe getattr/hasattr that block dangerous attribute access
    # These are created lazily after the safe_getattr function is defined
    builtins_dict["getattr"] = _create_safe_getattr_wrapper()
    builtins_dict["hasattr"] = _create_safe_hasattr_wrapper()

    return safe_globals


def _create_safe_getattr_wrapper() -> Callable:
    """Create a wrapper for safe_getattr that can be used as a builtin."""

    def wrapper(obj: Any, name: str, *default: Any) -> Any:
        if name in BLOCKED_ATTRIBUTES:
            raise SecurityViolation(
                f"Access to attribute '{name}' is not allowed in sandbox"
            )
        # Block dunder attributes except safe ones
        if name.startswith("__") and name.endswith("__"):
            if name not in SAFE_DUNDERS:
                raise SecurityViolation(
                    f"Access to attribute '{name}' is not allowed in sandbox"
                )
        # Block single underscore private attributes
        elif name.startswith("_"):
            raise SecurityViolation(
                f"Access to private attribute '{name}' is not allowed in sandbox"
            )
        if default:
            return getattr(obj, name, default[0])
        return getattr(obj, name)

    wrapper.__name__ = "getattr"
    wrapper.__doc__ = "Safe getattr that blocks dangerous attribute access"
    return wrapper


def _create_safe_hasattr_wrapper() -> Callable:
    """Create a wrapper for safe_hasattr that can be used as a builtin.

    Must mirror the access policy of _create_safe_getattr_wrapper:
    allow SAFE_DUNDERS, block other dunders and private attributes.
    """

    def wrapper(obj: Any, name: str) -> bool:
        if name in BLOCKED_ATTRIBUTES:
            return False
        if name.startswith("__") and name.endswith("__"):
            if name not in SAFE_DUNDERS:
                return False
        elif name.startswith("_"):
            return False
        return hasattr(obj, name)

    wrapper.__name__ = "hasattr"
    wrapper.__doc__ = "Safe hasattr that blocks dangerous attribute access"
    return wrapper


# =============================================================================
# Execution Context
# =============================================================================


class SandboxContext:
    """
    Context for sandboxed execution (thread-based).

    .. deprecated::
        For untrusted code, use :class:`ProcessSandbox` instead.
        ``SandboxContext`` uses a daemon thread that **cannot be killed** if
        it enters a tight loop, so the timeout is only cooperative.
        ``ProcessSandbox`` enforces hard timeouts via ``subprocess.kill()``.

    Usage:
        with SandboxContext(config) as ctx:
            result = ctx.execute(code, globals_dict)
            print(ctx.get_output())
    """

    def __init__(self, config: SandboxConfig | None = None):
        import warnings

        warnings.warn(
            "SandboxContext is deprecated; use ProcessSandbox instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.config = config or SandboxConfig()
        self.output_buffer: list[str] = []
        self._old_recursion_limit: int | None = None
        self._timed_out = False

    def __enter__(self) -> "SandboxContext":
        # Set recursion limit
        self._old_recursion_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(
            self.config.max_recursion_depth * 6 + 100
        )  # Each Geno call uses ~4-5 Python frames
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # Restore recursion limit
        if self._old_recursion_limit is not None:
            sys.setrecursionlimit(self._old_recursion_limit)

        # Convert RecursionError to our custom error
        if exc_type is RecursionError:
            raise RecursionLimitError(
                f"Recursion limit exceeded (max {self.config.max_recursion_depth})"
            ) from exc_val

    def get_safe_globals(self) -> dict:
        """Get a safe globals dictionary for execution."""
        return create_safe_globals(self.config, self.output_buffer)

    def get_output(self) -> str:
        """Get captured output."""
        return "".join(self.output_buffer)

    def clear_output(self) -> None:
        """Clear the output buffer."""
        self.output_buffer.clear()

    def execute(self, code: str, extra_globals: dict | None = None) -> Any:
        """
        Execute code in the sandbox.

        Args:
            code: Python code to execute
            extra_globals: Additional globals to make available

        Returns:
            Result of execution (if any)

        Raises:
            SecurityViolation: If code attempts blocked operations or fails safety validation
            ResourceLimitExceeded: If resource limits are exceeded
            TimeoutError: If execution exceeds time limit
        """
        # Validate code safety BEFORE execution
        warnings = validate_code_safety(code)
        if warnings and self.config.strict:
            raise SecurityViolation(
                f"Code failed safety validation: {'; '.join(warnings)}"
            )

        # Unconditionally reject __getattribute__/__getattr__ in class defs.
        # These bypass safe_getattr at the C level — the same check the
        # process sandbox performs (lines 960-990).  This MUST run even in
        # non-strict mode because it defeats the primary sandbox boundary.
        _reject_dangerous_dunders(code)

        globals_dict = self.get_safe_globals()

        if extra_globals:
            # Only allow safe extras (no overriding builtins)
            for key, value in extra_globals.items():
                if key != "__builtins__":
                    globals_dict[key] = value

        # Execute with timeout if configured
        if self.config.timeout is not None:
            return self._execute_with_timeout(code, globals_dict)
        else:
            exec(code, globals_dict)  # nosec B102
            return globals_dict.get("__result__")

    def _execute_with_timeout(self, code: str, globals_dict: dict) -> Any:
        """Execute code with a timeout."""
        result: list = [None]
        exception: list = [None]

        def target() -> None:
            try:
                exec(code, globals_dict)  # nosec B102
                result[0] = globals_dict.get("__result__")
            except Exception as e:
                exception[0] = e

        thread = threading.Thread(target=target)
        thread.daemon = True
        thread.start()
        thread.join(timeout=self.config.timeout)

        if thread.is_alive():
            # Thread is still running - timeout occurred
            # Note: We can't actually kill the thread in Python,
            # but we can raise an error
            self._timed_out = True
            raise TimeoutError(
                f"Execution timed out after {self.config.timeout} seconds"
            )

        if exception[0] is not None:
            raise exception[0]

        return result[0]


# =============================================================================
# Process-based Sandbox (Hard Timeouts)
# =============================================================================


@dataclass
class ProcessSandboxConfig:
    """Configuration for process-based sandbox execution."""

    # Time limit in seconds (None = no limit)
    timeout: float | None = 5.0

    # Memory limit in bytes (Linux only, None = no limit)
    max_memory_bytes: int | None = 256 * 1024 * 1024  # 256 MB default

    # Maximum CPU time in seconds (Linux only, None = no limit)
    max_cpu_time: float | None = None

    # Maximum file size in bytes (Linux only)
    max_file_size_bytes: int = 0  # 0 = no file writing

    # Maximum number of processes/threads (Linux only)
    max_processes: int = 1  # Only the main process

    # Strict mode: validate code statically before execution
    strict: bool = True

    # Maximum output length (characters) - prevents memory exhaustion
    max_output_length: int = 100000

    # Allow print statements (captured to buffer)
    allow_print: bool = True

    # Maximum collection (string / list) size — forwarded to the runtime
    max_collection_size: int = 10_000_000

    # Maximum bit length for integer arithmetic results (~10,000 digits).
    # Forwarded to the worker so compiled code's _safe_add/_safe_mul honor
    # the administrator-configured bound.
    max_integer_bits: int = 33_219

    # Maximum Geno call depth — forwarded to the worker's sys.setrecursionlimit
    # so compiled code hits Geno's limit before Python's default (~1000).
    max_recursion_depth: int = 500

    # Compiler-generated Python can opt into runtime-prelude symbol
    # injection after its leading import block is stripped.
    #
    # SECURITY: enabling this relaxes the worker's AST validator
    # (blocked-attribute access, format/format_map calls, blocked-builtin
    # calls) on the assumption that the input is the Geno compiler's
    # output. Set to True *only* for compiler-emitted Python.
    compiled_runtime_prelude: bool = False
    # Number of leading source lines trusted as compiler-emitted runtime
    # prelude when compiled_runtime_prelude is true.  Worker AST validation
    # still applies to all following generated program-body code.
    trusted_prelude_line_count: int = 0

    def __post_init__(self) -> None:
        _validate_optional_positive_number(self.timeout, "ProcessSandboxConfig.timeout")
        _validate_optional_positive_int(
            self.max_memory_bytes, "ProcessSandboxConfig.max_memory_bytes"
        )
        _validate_optional_positive_number(
            self.max_cpu_time, "ProcessSandboxConfig.max_cpu_time"
        )
        _validate_non_negative_int(
            self.max_file_size_bytes,
            "ProcessSandboxConfig.max_file_size_bytes",
        )
        _validate_positive_int(self.max_processes, "ProcessSandboxConfig.max_processes")
        _validate_bool(self.strict, "ProcessSandboxConfig.strict")
        _validate_non_negative_int(
            self.max_output_length, "ProcessSandboxConfig.max_output_length"
        )
        _validate_bool(self.allow_print, "ProcessSandboxConfig.allow_print")
        _validate_non_negative_int(
            self.max_collection_size,
            "ProcessSandboxConfig.max_collection_size",
        )
        _validate_positive_int(
            self.max_integer_bits, "ProcessSandboxConfig.max_integer_bits"
        )
        _validate_positive_int(
            self.max_recursion_depth,
            "ProcessSandboxConfig.max_recursion_depth",
        )
        _validate_bool(
            self.compiled_runtime_prelude,
            "ProcessSandboxConfig.compiled_runtime_prelude",
        )
        _validate_non_negative_int(
            self.trusted_prelude_line_count,
            "ProcessSandboxConfig.trusted_prelude_line_count",
        )


# Stdin framing marker for a precompiled runtime-prelude blob. The worker
# only interprets it when the parent-set sandbox config carries a
# prelude_blob_sha256, so program text starting with this marker cannot
# trigger blob handling on the plain-text path.
_PRELUDE_BLOB_HEADER = "#GENO-PRELUDE-BLOB"


@lru_cache(maxsize=4)
def _compiled_prelude_blob(prelude_text: str) -> tuple[bytes, str]:
    """Marshal blob and SHA-256 for a canonical runtime-prelude text.

    Memoized so long-lived parents (geno test/serve, the API) compile the
    ~4k-line prelude once per process. Only ever called with the package's
    own prelude text — callers anchor the split to an exact prefix match
    against that text, never to caller-supplied lengths (the P2 lesson).
    """
    import marshal

    code_obj = compile(prelude_text, "<geno-runtime-prelude>", "exec")
    blob = marshal.dumps(code_obj)
    return blob, hashlib.sha256(blob).hexdigest()


class ProcessSandbox:
    """
    Execute code in a subprocess with hard timeout enforcement.

    This provides stronger isolation than thread-based execution:
    - True timeout enforcement via process.kill()
    - Memory limits (on Linux)
    - CPU time limits (on Linux)
    - No shared state with parent process

    The tradeoff is higher overhead and serialization costs.
    """

    _PIPE_READ_CHUNK_SIZE = 8192
    _STDERR_RESULT_OVERHEAD = 4096
    _STDERR_TRUNCATION_MARKER = "[stderr truncated by ProcessSandbox]"

    def __init__(self, config: ProcessSandboxConfig | None = None):
        self.config = config or ProcessSandboxConfig()
        self.system = platform.system()

    @classmethod
    def _read_stream_bounded(
        cls, stream: Any, limit: int, *, keep_tail: bool
    ) -> tuple[str, bool]:
        chunks: list[str] = []
        kept_length = 0
        truncated = False

        while True:
            chunk = stream.read(cls._PIPE_READ_CHUNK_SIZE)
            if not chunk:
                break

            if limit <= 0:
                truncated = True
                continue

            if keep_tail:
                chunks.append(chunk)
                kept_length += len(chunk)
                if kept_length > limit:
                    truncated = True
                while kept_length > limit and chunks:
                    overflow = kept_length - limit
                    first = chunks[0]
                    if len(first) <= overflow:
                        chunks.pop(0)
                        kept_length -= len(first)
                    else:
                        chunks[0] = first[overflow:]
                        kept_length -= overflow
            else:
                remaining = limit - kept_length
                if remaining > 0:
                    chunks.append(chunk[:remaining])
                    kept_length += min(len(chunk), remaining)
                if len(chunk) > remaining:
                    truncated = True

        return "".join(chunks), truncated

    def _stderr_capture_limit(self) -> int:
        return self.config.max_output_length + self._STDERR_RESULT_OVERHEAD

    def _run_worker(
        self,
        cmd: list[str],
        code: str,
        config_overrides: dict[str, Any] | None = None,
    ) -> tuple[int, str, str, bool]:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self._create_restricted_env(config_overrides),
        )

        stdout_result: dict[str, str | bool] = {"text": "", "truncated": False}
        stderr_result: dict[str, str | bool] = {"text": "", "truncated": False}

        def reader(
            pipe: Any,
            limit: int,
            keep_tail: bool,
            target: dict[str, str | bool],
        ) -> None:
            try:
                text, truncated = self._read_stream_bounded(
                    pipe, limit, keep_tail=keep_tail
                )
                target["text"] = text
                target["truncated"] = truncated
            finally:
                pipe.close()

        stdout_thread = threading.Thread(
            target=reader,
            args=(process.stdout, self.config.max_output_length, False, stdout_result),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=reader,
            args=(process.stderr, self._stderr_capture_limit(), True, stderr_result),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        # Popen.wait(timeout=...) polls with exponentially growing sleeps
        # (capped at 50 ms), adding up to ~50 ms of latency past the worker's
        # actual exit. A blocking wait wakes immediately; a watchdog timer
        # enforces the same hard timeout by killing the worker. A None
        # timeout means wait indefinitely, matching Popen.wait(timeout=None).
        timeout = self.config.timeout
        timed_out = threading.Event()

        def _kill_on_timeout() -> None:
            timed_out.set()
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                pass

        watchdog = (
            threading.Timer(timeout, _kill_on_timeout) if timeout is not None else None
        )
        if watchdog is not None:
            watchdog.daemon = True
            watchdog.start()
        try:
            if process.stdin is not None:
                try:
                    process.stdin.write(code)
                    process.stdin.close()
                except BrokenPipeError:
                    pass

            returncode = process.wait()
        finally:
            if watchdog is not None:
                watchdog.cancel()

        if timeout is not None and timed_out.is_set():
            stdout_thread.join(timeout=1.0)
            stderr_thread.join(timeout=1.0)
            raise subprocess.TimeoutExpired(cmd, timeout)

        stdout_thread.join()
        stderr_thread.join()

        return (
            returncode,
            str(stdout_result["text"]),
            str(stderr_result["text"]),
            bool(stderr_result["truncated"]),
        )

    def _format_truncated_stderr(self, stderr: str) -> str:
        limit = self._stderr_capture_limit()
        marker = f"{self._STDERR_TRUNCATION_MARKER}: kept last {limit} characters"
        return f"{marker}\n{stderr}" if stderr else marker

    def execute(self, code: str) -> tuple[Any, str, str | None]:
        """
        Execute code in a sandboxed subprocess.

        Args:
            code: Python code to execute

        Returns:
            Tuple of (result, output, error_message)
            - result: The value of __result__ if set, else None
            - output: Captured stdout
            - error_message: Error message if execution failed, else None

        Raises:
            TimeoutError: If execution exceeds time limit
            SecurityViolation: If code fails safety validation
            SandboxError: If the worker exits 0 without a parseable result
                (the result channel was lost — never silently reported as
                success)
        """
        # Validate code safety first (if strict mode)
        if self.config.strict:
            warnings = validate_code_safety(code)
            if warnings:
                raise SecurityViolation(
                    f"Code failed safety validation: {'; '.join(warnings)}"
                )

        # Swap a canonical runtime-prelude prefix for a precompiled blob so
        # the worker skips re-parsing/re-compiling ~4k lines per run. Falls
        # back to the fully validated text path on any mismatch.
        payload = code
        config_overrides: dict[str, Any] | None = None
        if self.config.compiled_runtime_prelude:
            payload, config_overrides = self._frame_prelude_blob(code)

        # Create the subprocess execution script
        worker_script = self._create_worker_script()

        # Stream code over stdin instead of writing it to disk. This avoids
        # leaving user code behind if the parent process dies before cleanup.
        cmd = [sys.executable, "-c", worker_script]

        # Execute with timeout
        try:
            returncode, stdout, stderr, stderr_truncated = self._run_worker(
                cmd, payload, config_overrides
            )

            # Parse the result
            # Result JSON is written to stderr to prevent
            # user output on stdout from spoofing results.
            stderr_lines = stderr.strip().split("\n") if stderr.strip() else []
            result_json = None
            for line in reversed(stderr_lines):
                if line.startswith("{"):
                    result_json = line
                    break

            parsed: dict[str, Any] | None = None
            if result_json is not None:
                try:
                    parsed = json.loads(result_json)
                except json.JSONDecodeError:
                    parsed = None

            if parsed is not None:
                success = parsed.get("success", parsed.get("error") is None)
                error = parsed.get("error")
                if not success and not error:
                    error = "Execution failed"
                # When the worker was run with GENO_SANDBOX_DEBUG it attaches
                # its traceback; surface it in the parent log so a compiler-bug
                # report from the process-isolated path is diagnosable.
                worker_traceback = parsed.get("traceback")
                if not success and worker_traceback:
                    logger.error(
                        "Sandbox worker error: %s\n%s", error, worker_traceback
                    )
                return (
                    parsed.get("result"),
                    stdout,
                    error if not success else None,
                )

            # No parseable result JSON. A non-zero exit is an ordinary worker
            # failure: report the captured stderr as the error.
            if returncode != 0:
                error = stderr or "Execution failed"
                if stderr_truncated:
                    error = self._format_truncated_stderr(error)
                return (None, stdout, error)

            # Exit code 0 with a missing/corrupt result channel. The worker
            # writes exactly one result JSON line before every clean exit, so
            # this state means the result was lost (e.g. truncated at the
            # stderr capture bound, or the worker was bypassed). Fail closed
            # instead of silently reporting success-with-None (H-09).
            detail = (
                "wrote no result JSON"
                if result_json is None
                else "wrote unparseable result JSON"
            )
            hint = (
                " (stderr exceeded the capture limit of "
                f"{self._stderr_capture_limit()} characters and was truncated;"
                " the serialized result may not have survived)"
                if stderr_truncated
                else ""
            )
            message = f"Sandbox worker exited 0 but {detail}{hint}"
            logger.error("%s; stderr tail: %r", message, stderr[-500:])
            raise SandboxError(message)

        except subprocess.TimeoutExpired as e:
            raise TimeoutError(
                f"Execution timed out after {self.config.timeout} seconds"
            ) from e

    def _frame_prelude_blob(self, code: str) -> tuple[str, dict[str, Any] | None]:
        """Replace a canonical runtime-prelude prefix with a marshal blob.

        The split is anchored to the package's own prelude text: only an
        exact textual prefix match is substituted, so caller-supplied
        lengths (trusted_prelude_line_count) never decide what skips text
        validation. On substitution the worker receives only the program
        tail as text and validates ALL of it (trusted prefix count 0); the
        blob's SHA-256 travels in the parent-set config and is re-verified
        by the worker before unmarshalling. Any mismatch falls back to the
        unchanged fully validated text path.
        """
        from .compiler import _stripped_runtime_prelude

        prelude_text = _stripped_runtime_prelude()
        if not prelude_text or not code.startswith(prelude_text):
            return code, None

        blob, blob_sha256 = _compiled_prelude_blob(prelude_text)
        encoded = base64.b64encode(blob).decode("ascii")
        tail = code[len(prelude_text) :]
        payload = f"{_PRELUDE_BLOB_HEADER} {len(encoded)}\n{encoded}\n{tail}"
        return payload, {
            "prelude_blob_sha256": blob_sha256,
            "trusted_prelude_line_count": 0,
        }

    # Static worker script -- all configuration is read from the
    # GENO_SANDBOX_CONFIG environment variable at startup.
    _WORKER_SCRIPT = r"""
import json
import os
import sys

def _truncate_worker_text(value, max_chars):
    text = str(value)
    if len(text) <= max_chars:
        return text
    if max_chars <= 0:
        return ""
    suffix = "... [truncated]"
    if max_chars <= len(suffix):
        return suffix[:max_chars]
    return text[: max_chars - len(suffix)] + suffix

def _emit_worker_error(error, error_type):
    sys.stderr.write(json.dumps({
        "result": None,
        "success": False,
        "error": error,
        "error_type": error_type,
    }) + '\n')

def main():
    # Load configuration from environment variable
    config = json.loads(os.environ["GENO_SANDBOX_CONFIG"])
    max_result_error = int(config.get("max_output_length", 100000))

    # Read the code from stdin before tightening address-space limits.
    # Python 3.13 can need additional allocator headroom while reading stdin
    # and importing the worker support modules; the memory cap is applied
    # immediately before user code executes.
    try:
        code = sys.stdin.read()
    except MemoryError:
        _emit_worker_error(
            "resource_limit: memory limit exceeded while reading sandbox input",
            "resource_limit",
        )
        sys.exit(1)

    # ---- Precompiled runtime-prelude blob (parent-framed) ----
    # Only interpreted when the parent-set config carries the blob's
    # SHA-256, so plain program text starting with the marker is inert.
    # The blob replaces the runtime-prelude TEXT prefix only; the program
    # tail stays text and goes through the full AST validation below with
    # no trusted prefix. Every framing or integrity failure is fatal —
    # nothing is executed on a mismatch.
    _prelude_blob_code = None
    _expected_blob_sha = config.get("prelude_blob_sha256")
    if _expected_blob_sha:
        import base64 as _b64
        import hashlib as _hashlib
        import marshal as _marshal

        def _blob_fail(detail):
            _emit_worker_error(
                "startup_error: prelude blob " + detail, "startup_error"
            )
            sys.exit(1)

        _hdr, _sep, _rest = code.partition("\n")
        _parts = _hdr.split()
        if not _sep or len(_parts) != 2 or _parts[0] != "#GENO-PRELUDE-BLOB":
            _blob_fail("framing is malformed")
        try:
            _blob_len = int(_parts[1])
        except ValueError:
            _blob_fail("length is malformed")
        if _blob_len <= 0 or len(_rest) < _blob_len + 1 or _rest[_blob_len] != "\n":
            _blob_fail("payload is truncated")
        try:
            _blob = _b64.b64decode(_rest[:_blob_len], validate=True)
        except (ValueError, TypeError):
            _blob_fail("payload is not valid base64")
        if _hashlib.sha256(_blob).hexdigest() != _expected_blob_sha:
            _blob_fail("failed integrity verification")
        try:
            _prelude_blob_code = _marshal.loads(_blob)
        except (ValueError, EOFError, TypeError):
            _blob_fail("could not be unmarshalled")
        code = _rest[_blob_len + 1:]

    # Set up non-memory resource limits (Linux/macOS)
    try:
        import resource

        def _try_setrlimit(which, soft, hard, name):
            # Apply a resource limit; return True on success.
            try:
                resource.setrlimit(which, (soft, hard))
                return True
            except (ValueError, OSError) as exc:
                sys.stderr.write(
                    f"Warning: failed to set {name}: {exc}\n"
                )
                return False

        # CPU time limit
        cpu_limit = config.get("max_cpu_time", 0)
        if cpu_limit > 0:
            _try_setrlimit(resource.RLIMIT_CPU, cpu_limit, cpu_limit, "RLIMIT_CPU")

        # File size limit (prevent file creation)
        file_limit = config.get("max_file_size_bytes", 0)
        _try_setrlimit(resource.RLIMIT_FSIZE, file_limit, file_limit, "RLIMIT_FSIZE")

        # Process limit — critical for preventing fork bombs.
        # NPROC failure is higher risk but os/subprocess imports are
        # still blocked at the Python level.
        proc_limit = config.get("max_processes", 1)
        _try_setrlimit(resource.RLIMIT_NPROC, proc_limit, proc_limit, "RLIMIT_NPROC")
    except ImportError:
        pass  # resource module not available (Windows)

    # ---- Worker-side AST validation (runs unconditionally) ----
    # This runs even when strict=False in the parent process, closing the
    # TOCTOU gap where the parent's static validator is skipped but the
    # worker exec()s arbitrary code.  It is a subset of validate_code_safety
    # plus the dangerous-dunder class body check.
    import ast as _ast

    _WORKER_DANGEROUS_DUNDERS = set(config.get("dangerous_dunders", []))
    _WORKER_BLOCKED_ATTRS = set(config.get("blocked_attrs", []))
    _WORKER_BLOCKED_BUILTINS = set(
        config.get("worker_ast_blocked_builtins", [])
    )
    _WORKER_FORMAT_METHODS = set(config.get("format_methods", []))
    _WORKER_SAFE_DUNDERS = set(config.get("safe_dunders", []))
    _TRUSTED_PRELUDE_LINE_COUNT = (
        int(config.get("trusted_prelude_line_count", 0))
        if config.get("compiled_runtime_prelude", False)
        else 0
    )

    def _worker_fail(msg):
        sys.stderr.write(json.dumps({
            "result": None, "success": False, "error": msg,
        }) + '\n')
        sys.exit(1)

    def _worker_in_trusted_prelude(node):
        end_lineno = getattr(node, "end_lineno", None)
        return (
            end_lineno is not None
            and getattr(node, "lineno", 0) <= _TRUSTED_PRELUDE_LINE_COUNT
            and end_lineno <= _TRUSTED_PRELUDE_LINE_COUNT
        )

    try:
        _tree = _ast.parse(code)
        for _node in _ast.walk(_tree):
            # 1. Dangerous dunders in class bodies. This runs on ALL nodes,
            # including the trusted prelude prefix: it is the guard against
            # C-level attribute-access bypass (custom __getattribute__), so
            # it must not be skipped on the strength of a caller-supplied
            # trusted_prelude_line_count.
            if isinstance(_node, _ast.ClassDef):
                for _child in _ast.walk(_node):
                    _dangerous_name = None
                    if isinstance(_child, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                        if _child.name in _WORKER_DANGEROUS_DUNDERS:
                            _dangerous_name = _child.name
                    elif isinstance(_child, _ast.Assign):
                        for _t in _child.targets:
                            if isinstance(_t, _ast.Name) and _t.id in _WORKER_DANGEROUS_DUNDERS:
                                _dangerous_name = _t.id
                    elif isinstance(_child, _ast.AnnAssign) and isinstance(_child.target, _ast.Name):
                        if _child.target.id in _WORKER_DANGEROUS_DUNDERS:
                            _dangerous_name = _child.target.id
                    if _dangerous_name:
                        _worker_fail(
                            f"Class '{_node.name}' defines {_dangerous_name} "
                            f"which is not allowed (bypasses sandbox attribute controls)"
                        )

            # Checks 2-4 only skip the trusted generated runtime support
            # prefix.  Compiled program-body code is validated even when
            # runtime-prelude globals are injected.
            if not _worker_in_trusted_prelude(_node):
                # 2. Direct attribute access to blocked names (. operator)
                if isinstance(_node, _ast.Attribute):
                    if _node.attr in _WORKER_BLOCKED_ATTRS:
                        _worker_fail(
                            f"Access to attribute '{_node.attr}' is not allowed in sandbox"
                        )
                    if _node.attr.startswith('__') and _node.attr.endswith('__'):
                        if _node.attr not in _WORKER_SAFE_DUNDERS:
                            _worker_fail(
                                f"Access to attribute '{_node.attr}' is not allowed in sandbox"
                            )
                    elif _node.attr.startswith('_'):
                        _worker_fail(
                            f"Access to private attribute '{_node.attr}' is not allowed in sandbox"
                        )

                # 3. str.format() / str.format_map() calls
                if isinstance(_node, _ast.Call) and isinstance(_node.func, _ast.Attribute):
                    if _node.func.attr in _WORKER_FORMAT_METHODS:
                        _worker_fail(
                            f"str.{_node.func.attr}() can bypass sandbox attribute "
                            f"restrictions via format spec traversal"
                        )

                # 4. Blocked builtin calls
                if isinstance(_node, _ast.Call) and isinstance(_node.func, _ast.Name):
                    if _node.func.id in _WORKER_BLOCKED_BUILTINS:
                        _worker_fail(f"Blocked operation: {_node.func.id}() is not allowed in sandbox")

            # Note: import checking is deliberately omitted here.
            # Compiled Geno code contains lazy imports inside function
            # bodies (e.g. from http.server import ...) that are
            # capability-gated and never called unless granted.  The
            # worker's safe_import blocks unauthorized imports at runtime.

    except SyntaxError as _exc:
        _worker_fail(f"Invalid Python syntax: {_exc}")

    # Create sandbox globals
    import builtins
    _REAL_IMPORT = builtins.__import__
    safe_builtins = {}
    safe_names = set(config.get("safe_builtins", [
        'bool', 'int', 'float', 'str', 'list', 'dict', 'tuple', 'set',
        'frozenset', 'isinstance', 'issubclass', 'callable',
        'range', 'enumerate', 'zip', 'map', 'filter', 'reversed', 'sorted',
        'len', 'sum', 'min', 'max', 'abs', 'round', 'all', 'any',
        'repr', 'ascii', 'chr', 'ord', 'divmod',
        'True', 'False', 'None',
        'Exception', 'ValueError', 'TypeError', 'IndexError', 'KeyError',
        'RuntimeError', 'StopIteration', 'AssertionError', 'AttributeError',
        'ZeroDivisionError', 'NameError', 'hash', 'iter', 'next', 'slice', 'print',
    ]))
    for name in safe_names:
        if hasattr(builtins, name):
            safe_builtins[name] = getattr(builtins, name)

    # Restricted type(): only single-arg form allowed (type query).
    # Three-arg form creates classes with custom __getattribute__
    # that bypass safe_getattr at the C level.
    def safe_type(*args):
        if len(args) == 1:
            return type(args[0])
        raise RuntimeError(
            "type() with multiple arguments is not allowed in sandbox"
        )
    safe_builtins['type'] = safe_type

    # Safe getattr/hasattr (defined early so module proxy can use them)
    blocked_attrs = set(config.get("blocked_attrs", []))
    safe_dunders = set(config.get("safe_dunders", []))

    # Module proxy: wraps imported modules so attribute access is filtered
    # even by C-level functions that bypass safe_getattr.
    _MODULE_BLOCKED_FUNCTIONS = {
        module: set(names)
        for module, names in config["module_blocked_functions"].items()
    }
    _MODULE_BLOCKED_ATTRIBUTES = {
        module: set(names)
        for module, names in config["module_blocked_attributes"].items()
    }
    _WORKER_SAFE_MODULES = set(config.get("safe_import_allowlist", [
        'csv', 'dataclasses', 'hashlib', 'hmac', 'io', 'typing', 'math',
        'secrets', 'copy', 'functools',
        'collections', 'abc', 'itertools', 'typing_extensions',
        'tomllib', 'tomli',
    ]))
    def _estimate_urlsafe_len(nbytes):
        return ((nbytes + 2) // 3) * 4

    def _wrap_shake_xof(obj):
        class _ShakeProxy:
            __slots__ = ()
            def __getattribute__(self, name):
                if name in blocked_attrs:
                    raise RuntimeError(f"Access to '{name}' not allowed")
                if name.startswith('__') and name.endswith('__'):
                    if name not in safe_dunders:
                        raise RuntimeError(f"Access to '{name}' not allowed")
                elif name.startswith('_'):
                    raise RuntimeError(f"Access to '{name}' not allowed")
                value = getattr(obj, name)
                if name == 'digest' and callable(value):
                    def guarded_digest(length, *args, **kwargs):
                        if isinstance(length, int) and length > max_coll:
                            raise RuntimeError(
                                'hashlib shake digest output exceeds sandbox '
                                'collection limit'
                            )
                        return value(length, *args, **kwargs)
                    return guarded_digest
                if name == 'hexdigest' and callable(value):
                    def guarded_hexdigest(length, *args, **kwargs):
                        if isinstance(length, int) and length * 2 > max_coll:
                            raise RuntimeError(
                                'hashlib shake hex output exceeds sandbox '
                                'collection limit'
                            )
                        return value(length, *args, **kwargs)
                    return guarded_hexdigest
                if name == 'copy' and callable(value):
                    def guarded_copy(*args, **kwargs):
                        return _wrap_shake_xof(value(*args, **kwargs))
                    return guarded_copy
                return value
            def __repr__(self):
                return '<sandboxed hashlib shake object>'
        return _ShakeProxy()

    def _wrap_module_callable(mod_name, attr_name, value):
        if mod_name == 'secrets' and callable(value):
            if attr_name == 'token_bytes':
                def guarded_token_bytes(*args, **kwargs):
                    nbytes = args[0] if args else kwargs.get('nbytes', 32)
                    if nbytes is None:
                        nbytes = 32
                    if isinstance(nbytes, int) and nbytes > max_coll:
                        raise RuntimeError(
                            'secrets.token_bytes output exceeds sandbox '
                            'collection limit'
                        )
                    return value(*args, **kwargs)
                return guarded_token_bytes
            if attr_name == 'token_hex':
                def guarded_token_hex(*args, **kwargs):
                    nbytes = args[0] if args else kwargs.get('nbytes', 32)
                    if nbytes is None:
                        nbytes = 32
                    if isinstance(nbytes, int) and nbytes * 2 > max_coll:
                        raise RuntimeError(
                            'secrets.token_hex output exceeds sandbox '
                            'collection limit'
                        )
                    return value(*args, **kwargs)
                return guarded_token_hex
            if attr_name == 'token_urlsafe':
                def guarded_token_urlsafe(*args, **kwargs):
                    nbytes = args[0] if args else kwargs.get('nbytes', 32)
                    if nbytes is None:
                        nbytes = 32
                    if (
                        isinstance(nbytes, int)
                        and _estimate_urlsafe_len(nbytes) > max_coll
                    ):
                        raise RuntimeError(
                            'secrets.token_urlsafe output exceeds sandbox '
                            'collection limit'
                        )
                    return value(*args, **kwargs)
                return guarded_token_urlsafe
        if mod_name == 'hashlib' and callable(value):
            if attr_name in {'shake_128', 'shake_256'}:
                def guarded_shake_factory(*args, **kwargs):
                    return _wrap_shake_xof(value(*args, **kwargs))
                return guarded_shake_factory
            if attr_name == 'new':
                def guarded_new(*args, **kwargs):
                    algorithm = args[0] if args else kwargs.get('name')
                    result = value(*args, **kwargs)
                    if (
                        isinstance(algorithm, str)
                        and algorithm.lower() in {'shake_128', 'shake_256'}
                    ):
                        return _wrap_shake_xof(result)
                    return result
                return guarded_new
        return value

    def _make_module_proxy(mod):
        class _Proxy:
            __slots__ = ()
            def __getattribute__(self, name):
                if name in blocked_attrs:
                    raise RuntimeError(f"Access to '{name}' not allowed")
                if name.startswith('__') and name.endswith('__'):
                    if name not in safe_dunders:
                        raise RuntimeError(f"Access to '{name}' not allowed")
                elif name.startswith('_'):
                    raise RuntimeError(f"Access to '{name}' not allowed")
                mod_name = getattr(mod, '__name__', '')
                blocked_fns = _MODULE_BLOCKED_FUNCTIONS.get(mod_name, set())
                if name in blocked_fns:
                    raise RuntimeError(f"Access to '{mod_name}.{name}' not allowed")
                blocked_mod_attrs = _MODULE_BLOCKED_ATTRIBUTES.get(mod_name, set())
                if name in blocked_mod_attrs:
                    raise RuntimeError(f"Access to '{mod_name}.{name}' not allowed")
                import types as _types
                value = getattr(mod, name)
                if isinstance(value, _types.ModuleType):
                    top = getattr(value, '__name__', '').split('.')[0]
                    if top not in _WORKER_SAFE_MODULES:
                        raise RuntimeError(f"Access to module '{top}' via attribute not allowed")
                    return _make_module_proxy(value)
                return _wrap_module_callable(mod_name, name, value)
            def __repr__(self):
                return f"<sandboxed module '{getattr(mod, '__name__', '?')}'>"
        return _Proxy()

    # Pre-import modules needed for compiled Geno runtime prelude injection.
    # Other allowlisted modules are imported on demand by safe_import below
    # and then wrapped with the same module proxy.
    # "re" deliberately excluded to match the thread sandbox policy:
    # runtime builtins receive _re via explicit compiled-runtime injection,
    # so user code never needs to import re directly. Keeping it out prevents
    # the builtin-level ReDoS mitigations.
    import dataclasses
    import typing
    import math
    import copy
    import functools

    _IMPORT_PROXY_CACHE = {}

    def _proxied_import_module(mod):
        mod_name = getattr(mod, '__name__', '')
        cached = _IMPORT_PROXY_CACHE.get(mod_name)
        if cached is not None:
            return cached
        proxy = _make_module_proxy(mod)
        _IMPORT_PROXY_CACHE[mod_name] = proxy
        return proxy

    # Safe import that only allows whitelisted modules
    def safe_import(
        name: str,
        globals: dict | None = None,
        locals: dict | None = None,
        fromlist: tuple = (),
        level: int = 0,
    ) -> object:
        top_level = name.split('.', 1)[0]
        if top_level in _WORKER_SAFE_MODULES:
            mod = _REAL_IMPORT(name, globals, locals, fromlist, level)
            return _proxied_import_module(mod)
        raise RuntimeError(f"Import of '{name}' is not allowed")

    # Block dangerous operations (but allow safe_import)
    blocked = {
        'eval', 'exec', 'compile', 'open', 'input',
        'globals', 'locals', 'vars', 'dir', 'setattr', 'delattr',
        'memoryview', 'bytearray', 'bytes', 'breakpoint',
    }
    def make_blocked(name):
        def blocked(*args, **kwargs):
            raise RuntimeError(f"Blocked: {name}()")
        return blocked
    for name in blocked:
        safe_builtins[name] = make_blocked(name)

    # Add safe import
    safe_builtins['__import__'] = safe_import
    def safe_getattr(obj, name, *default):
        if name in blocked_attrs:
            raise RuntimeError(f"Access to '{name}' not allowed")
        if name.startswith('__') and name.endswith('__'):
            if name not in safe_dunders:
                raise RuntimeError(f"Access to '{name}' not allowed")
        elif name.startswith('_'):
            raise RuntimeError(f"Access to '{name}' not allowed")
        if default:
            return getattr(obj, name, default[0])
        return getattr(obj, name)
    safe_builtins['getattr'] = safe_getattr
    def safe_hasattr(obj, name):
        if name in blocked_attrs:
            return False
        if name.startswith('__') and name.endswith('__'):
            if name not in safe_dunders:
                return False
        elif name.startswith('_'):
            return False
        return hasattr(obj, name)
    safe_builtins['hasattr'] = safe_hasattr

    # Capture output with limit enforcement
    max_output = config.get("max_output_length", 100000)
    allow_print = config.get("allow_print", True)
    output_buffer = []
    total_output_len = [0]  # Use list for mutable closure
    def safe_print(*args, sep=' ', end='\n', **kwargs):
        if not allow_print:
            raise RuntimeError("print() is disabled in this sandbox")
        output = sep.join(str(a) for a in args) + end
        total_output_len[0] += len(output)
        if total_output_len[0] > max_output:
            raise RuntimeError(f"Output limit exceeded ({max_output} characters)")
        output_buffer.append(output)
        sys.stdout.write(output)
    safe_builtins['print'] = safe_print

    # Inject configurable collection size limit so the runtime prelude
    # picks it up via globals().get('_GENO_MAX_COLLECTION_SIZE', ...).
    max_coll = int(os.environ.get('GENO_MAX_COLLECTION_SIZE', 10_000_000))
    # Same pattern for the integer bit-length ceiling.
    max_int_bits = int(os.environ.get('GENO_MAX_INTEGER_BITS', 33_219))

    # Set recursion limit to match the interpreter (100 + headroom)
    recursion_limit = int(os.environ.get('GENO_RECURSION_LIMIT', 150))
    sys.setrecursionlimit(recursion_limit)

    globals_dict = {
        '__builtins__': safe_builtins,
        '__name__': '__sandbox__',
        '_GENO_MAX_COLLECTION_SIZE': max_coll,
        '_GENO_MAX_INTEGER_BITS': max_int_bits,
    }

    if config.get("compiled_runtime_prelude", False):
        import re as _re_mod

        def _geno_run_async(awaitable):
            # Lazy: importing asyncio costs ~30 ms of worker startup and
            # almost no compiled programs are async. The import runs in
            # this trusted worker scope, not the sandboxed globals.
            import asyncio as _asyncio

            return _asyncio.run(awaitable)

        globals_dict.update(
            {
                'dataclass': dataclasses.dataclass,
                '_dataclasses_fields': dataclasses.fields,
                '_dataclasses_replace': dataclasses.replace,
                'Any': typing.Any,
                'Callable': typing.Callable,
                'TypeVar': typing.TypeVar,
                'Generic': typing.Generic,
                'Optional': typing.Optional,
                'Union': typing.Union,
                'deepcopy': copy.deepcopy,
                'math': math,
                'cmp_to_key': functools.cmp_to_key,
                '_re': _re_mod,
                '_geno_run_async': _geno_run_async,
            }
        )

    # Python 3.9's dataclasses._is_type does
    #   sys.modules.get(cls.__module__).__dict__
    # @dataclass-decorated classes get __module__ = '__sandbox__' from the
    # exec namespace, but '__sandbox__' is not a real entry in sys.modules.
    # Create a fake module so the lookup succeeds instead of raising
    # AttributeError on None.__dict__.
    import types as _sandbox_types
    _fake_mod = _sandbox_types.ModuleType('__sandbox__')
    _fake_mod.__dict__.update(globals_dict)
    sys.modules['__sandbox__'] = _fake_mod

    # Apply the address-space limit only after trusted worker setup is done.
    try:
        import resource as _resource

        mem_limit = config.get("max_memory_bytes", 0)
        if mem_limit > 0:
            try:
                _resource.setrlimit(_resource.RLIMIT_AS, (mem_limit, mem_limit))
            except (ValueError, OSError) as exc:
                sys.stderr.write(
                    f"Warning: failed to set RLIMIT_AS: {exc}\n"
                )
    except ImportError:
        pass

    try:
        if _prelude_blob_code is not None:
            # Hash-verified parent-built prelude (see framing block above).
            exec(_prelude_blob_code, globals_dict)  # nosec B102
        exec(code, globals_dict)  # nosec B102
        result = globals_dict.get('__result__')
        # Serialize result (handle non-JSON types)
        try:
            json_result = json.dumps({"result": result, "success": True, "error": None}, allow_nan=False)
        except (TypeError, ValueError):
            json_result = json.dumps({"result": str(result), "success": True, "error": None})
        # The parent keeps only the tail of stderr. A result line that cannot
        # fit inside that capture window would come back with its head cut
        # off and the run would be reported as a failure with a lost result;
        # emit a deterministic, parseable error instead.
        result_limit = int(config.get("stderr_result_limit", 0))
        if result_limit and len(json_result) + 1 > result_limit:
            _emit_worker_error(
                "result_too_large: serialized result is "
                + str(len(json_result))
                + " characters, exceeding the sandbox result limit of "
                + str(result_limit)
                + " (results must fit within max_output_length "
                + str(max_result_error)
                + " plus framing overhead)",
                "result_too_large",
            )
            sys.exit(1)
        sys.stderr.write(json_result + '\n')
    except MemoryError:
        sys.stderr.write(json.dumps({
            "result": None,
            "success": False,
            "error": "resource_limit: memory limit exceeded",
            "error_type": "resource_limit",
        }) + '\n')
        sys.exit(1)
    except Exception as e:
        detail = str(e) or type(e).__name__
        # Compiled Geno programs raise only sanctioned RuntimeError-style
        # errors. Any other type reaching here is an internal codegen/toolchain
        # defect, not a user-program error; tag it with the exception type so
        # the two are distinguishable instead of collapsing to a bare message.
        if not isinstance(e, RuntimeError):
            detail = type(e).__name__ + ": " + detail
        error_msg = _truncate_worker_text(detail, max_result_error)
        payload = {"result": None, "success": False, "error": error_msg}
        # Opt-in debug escape hatch: when the parent enables it, carry the
        # worker traceback so a compiler-bug report from the process-isolated
        # path is diagnosable. Off by default (never leaks internals).
        if config.get("debug"):
            import traceback as _traceback
            payload["traceback"] = _truncate_worker_text(
                _traceback.format_exc(), max_result_error
            )
        sys.stderr.write(json.dumps(payload) + '\n')
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except MemoryError:
        _emit_worker_error(
            "resource_limit: memory limit exceeded during sandbox startup",
            "resource_limit",
        )
        sys.exit(1)
    except BaseException as exc:
        detail = str(exc) or type(exc).__name__
        _emit_worker_error(
            f"startup_error: {type(exc).__name__}: {detail}",
            "startup_error",
        )
        sys.exit(1)
"""

    def _create_worker_script(self) -> str:
        """Create the worker script that runs in the subprocess."""
        return self._WORKER_SCRIPT

    def _create_restricted_env(
        self, config_overrides: dict[str, Any] | None = None
    ) -> dict:
        """Create a restricted environment for the subprocess."""
        # Start with a minimal environment
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),  # nosec B108
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }

        if self.system == "Windows":
            for name in ("SystemRoot", "WINDIR", "COMSPEC", "PATHEXT"):
                value = os.environ.get(name)
                if value:
                    env[name] = value

        # On macOS, we might need TMPDIR
        if self.system == "Darwin":
            env["TMPDIR"] = os.environ.get("TMPDIR", "/tmp")  # nosec B108

        # Pass sandbox configuration as JSON via environment variable
        worker_config: dict[str, Any] = {
            "max_memory_bytes": self.config.max_memory_bytes or 0,
            "max_cpu_time": (
                math.ceil(self.config.max_cpu_time)
                if self.config.max_cpu_time is not None
                else 0
            ),
            "max_file_size_bytes": self.config.max_file_size_bytes,
            "max_processes": self.config.max_processes,
            "max_output_length": self.config.max_output_length,
            # Parent-side stderr capture bound (max_output_length + framing
            # overhead). The worker refuses to emit a success-result line
            # that could not survive the parent's tail capture, so oversized
            # results fail with an explicit error instead of being lost.
            "stderr_result_limit": self._stderr_capture_limit(),
            "allow_print": self.config.allow_print,
            "blocked_attrs": sorted(BLOCKED_ATTRIBUTES),
            "dangerous_dunders": sorted(_DANGEROUS_DUNDERS),
            "format_methods": sorted(_FORMAT_METHODS),
            "safe_dunders": sorted(SAFE_DUNDERS),
            "worker_ast_blocked_builtins": sorted(_WORKER_AST_BLOCKED_BUILTINS),
            "trusted_prelude_line_count": self.config.trusted_prelude_line_count,
            # __build_class__ is needed by the worker for compiled Geno
            # code (runtime prelude classes, @dataclass definitions).
            # The worker's __getattribute__ check prevents abuse.
            "safe_builtins": sorted(SAFE_BUILTINS.keys()) + ["__build_class__"],
            "safe_import_allowlist": sorted(_SAFE_IMPORT_ALLOWLIST),
            "module_blocked_functions": {
                module: sorted(names)
                for module, names in sorted(_MODULE_BLOCKED_FUNCTIONS.items())
            },
            "module_blocked_attributes": {
                module: sorted(names)
                for module, names in sorted(_MODULE_BLOCKED_ATTRIBUTES.items())
            },
            "compiled_runtime_prelude": self.config.compiled_runtime_prelude,
            # Opt-in worker traceback for diagnosing compiler/codegen bugs on
            # the process-isolated path. Off unless GENO_SANDBOX_DEBUG is set.
            "debug": _sandbox_debug_enabled(),
        }
        if config_overrides:
            worker_config.update(config_overrides)
        env["GENO_SANDBOX_CONFIG"] = json.dumps(worker_config)

        # Forward collection size limit to the runtime support module
        env["GENO_MAX_COLLECTION_SIZE"] = str(self.config.max_collection_size)

        # Forward integer bit-length ceiling to the runtime support module
        env["GENO_MAX_INTEGER_BITS"] = str(self.config.max_integer_bits)

        # Forward recursion limit — the worker reads this and calls
        # sys.setrecursionlimit before executing user code.  Each Geno call
        # uses ~4-5 Python frames, so mirror the interpreter's headroom.
        env["GENO_RECURSION_LIMIT"] = str(self.config.max_recursion_depth * 6 + 100)

        # Forward program args for cli_args() builtin
        if "GENO_CLI_ARGS" in os.environ:
            env["GENO_CLI_ARGS"] = os.environ["GENO_CLI_ARGS"]

        return env


def run_in_process(
    code: str, config: ProcessSandboxConfig | None = None
) -> tuple[Any, str]:
    """
    Run code in a process-isolated sandbox with hard timeouts.

    This is more secure than run_sandboxed() as it provides:
    - True timeout enforcement (process can be killed)
    - Memory limits (on Linux)
    - Process isolation

    Args:
        code: Python code to execute
        config: Process sandbox configuration

    Returns:
        Tuple of (execution result, captured output)

    Raises:
        TimeoutError: If execution exceeds time limit
        SecurityViolation: If code fails safety validation
        SandboxError: If the worker's result channel is lost (never silently
            reported as success)
        RuntimeError: If execution fails
    """
    sandbox = ProcessSandbox(config)
    result, output, error = sandbox.execute(code)

    if error is not None:
        raise RuntimeError(error)

    return result, output


# =============================================================================
# Validation Helpers
# =============================================================================

# Dunder methods that bypass safe_getattr at the C level.
# __getattribute__/__getattr__: intercept all attribute access at C level.
# __del__: destructor runs at GC time outside sandbox control flow.
# __init_subclass__: called by class machinery when a subclass is created.
# __set_name__: called by class machinery for descriptor assignment.
# __class_getitem__: called by cls[args] subscript at C level.
# __get__/__set__/__delete__: descriptor hooks run during attribute access.
# __index__/__length_hint__: invoked by Python internals for indexing/sizing.
# __new__/__mro_entries__/__instancecheck__/__subclasscheck__: class machinery.
_DANGEROUS_DUNDERS = frozenset(
    {
        "__getattribute__",
        "__getattr__",
        "__setattr__",
        "__delattr__",
        "__new__",
        "__del__",
        "__get__",
        "__set__",
        "__delete__",
        "__index__",
        "__length_hint__",
        "__init_subclass__",
        "__set_name__",
        "__class_getitem__",
        "__mro_entries__",
        "__instancecheck__",
        "__subclasscheck__",
    }
)

_FORMAT_METHODS = frozenset({"format", "format_map"})

# The process sandbox worker always runs this AST filter, even when
# strict=False in the parent process, so it must stay limited to names
# that are genuinely dangerous regardless of local shadowing.
# Informational/REPL helpers like help() and quit() are blocked as
# builtins in the thread sandbox, but they should still be legal local
# function names in non-strict process execution.
_WORKER_AST_BLOCKED_BUILTINS = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "input",
        "globals",
        "locals",
        "vars",
        "dir",
        "setattr",
        "delattr",
        "memoryview",
        "bytearray",
        "bytes",
        "breakpoint",
    }
)


def _reject_dangerous_dunders(code: str) -> None:
    """Reject class definitions containing dangerous dunder overrides.

    Dunders in ``_DANGEROUS_DUNDERS`` are called by Python's class machinery
    at the C level, bypassing the sandbox's ``safe_getattr`` wrapper.  This
    mirrors the identical check in the process sandbox worker.

    Raises :class:`SecurityViolation` if a dangerous dunder is found.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise SecurityViolation(f"Invalid Python syntax: {exc}") from exc

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for child in ast.walk(node):
            dangerous_name: str | None = None
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if child.name in _DANGEROUS_DUNDERS:
                    dangerous_name = child.name
            elif isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Name) and target.id in _DANGEROUS_DUNDERS:
                        dangerous_name = target.id
            elif isinstance(child, ast.AnnAssign) and isinstance(
                child.target, ast.Name
            ):
                if child.target.id in _DANGEROUS_DUNDERS:
                    dangerous_name = child.target.id
            if dangerous_name:
                raise SecurityViolation(
                    f"Class '{node.name}' defines {dangerous_name} "
                    f"which is not allowed (bypasses sandbox attribute controls)"
                )


@lru_cache(maxsize=8)
def _cached_safety_scan(code: str) -> tuple[str, ...]:
    """Parse *code* and collect _SafetyVisitor warnings (memoized).

    The cache exists for the compiler's constant runtime prelude, which CLI
    runs would otherwise re-parse and re-walk (~4k lines) on every
    invocation. Memoization is sound because the scan is a pure function of
    the source text.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return (f"Invalid Python syntax: {exc}",)

    visitor = _SafetyVisitor()
    visitor.visit(tree)
    return tuple(visitor.warnings)


def validate_code_safety(code: str) -> list[str]:
    """
    Perform static analysis to detect potentially dangerous patterns.

    Uses AST analysis rather than regex for robust detection.  This is a
    defense-in-depth measure -- the runtime sandbox is the primary security
    boundary.  AST-based checking catches aliasing tricks like
    ``e = eval; e('x')`` that a simple regex would miss.

    Returns a list of warnings (empty if code appears safe).

    When *code* starts with the compiler's constant runtime prelude, the
    prelude prefix and the remainder are scanned separately so the prefix
    scan can be reused across invocations. This is equivalent to scanning
    the concatenation: _SafetyVisitor keeps no state across statements
    (every check is node-local), the prelude is a complete module so the
    split falls on a top-level statement boundary, and if a remainder ever
    failed to parse standalone the scan fails closed with a syntax warning
    rather than missing nodes.
    """
    from .compiler import _stripped_runtime_prelude

    prefix = _stripped_runtime_prelude()
    if prefix and len(code) > len(prefix) and code.startswith(prefix):
        return list(_cached_safety_scan(prefix)) + list(
            _cached_safety_scan(code[len(prefix) :])
        )
    return list(_cached_safety_scan(code))


class _SafetyVisitor(ast.NodeVisitor):
    """AST visitor that flags dangerous patterns."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    # -- imports -------------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top_level = alias.name.split(".")[0]
            if top_level in BLOCKED_MODULES:
                self.warnings.append(f"Potentially dangerous import: {top_level}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            top_level = node.module.split(".")[0]
            if top_level in BLOCKED_MODULES:
                self.warnings.append(f"Potentially dangerous import: {top_level}")
        self.generic_visit(node)

    # -- calls ---------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        # Direct call: eval('x')
        if isinstance(node.func, ast.Name) and node.func.id in BLOCKED_BUILTINS:
            self.warnings.append(f"Potentially dangerous call: {node.func.id}()")
        # Method call: "...".format(...) / str.format(...)
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in _FORMAT_METHODS:
                self.warnings.append(
                    f"str.{node.func.attr}() can bypass sandbox attribute "
                    f"restrictions via format spec traversal"
                )
        # getattr() with computed (non-literal) attribute name
        if isinstance(node.func, ast.Name) and node.func.id == "getattr":
            if len(node.args) >= 2:
                attr_arg = node.args[1]
                if not isinstance(attr_arg, ast.Constant):
                    self.warnings.append(
                        "getattr() with computed attribute name may bypass "
                        "static attribute checks"
                    )
        self.generic_visit(node)

    # -- attribute access ----------------------------------------------------

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in BLOCKED_ATTRIBUTES:
            self.warnings.append(f"Potentially dangerous attribute access: {node.attr}")
        elif node.attr.startswith("__") and node.attr.endswith("__"):
            if node.attr not in SAFE_DUNDERS:
                self.warnings.append(
                    f"Potentially dangerous attribute access: {node.attr}"
                )
        elif node.attr.startswith("_"):
            self.warnings.append(
                f"Access to private attribute '{node.attr}' may bypass "
                f"sandbox attribute controls"
            )
        self.generic_visit(node)

    # -- class definitions ---------------------------------------------------

    # The ``class`` statement is the standard way to create classes with custom
    # ``__getattribute__``, which bypasses safe_getattr at the C level --
    # the same escape vector that motivated blocking 3-arg type().
    # Compiled Geno code uses @dataclass (compiler-controlled), not user-
    # authored class bodies, so this only fires for injected/raw Python.

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.warnings.append(
            f"Class definition '{node.name}' could bypass sandbox attribute "
            f"access controls via custom __getattribute__"
        )
        self.generic_visit(node)

    # -- alias detection: e = eval -------------------------------------------

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Name) and node.value.id in BLOCKED_BUILTINS:
            self.warnings.append(
                f"Potentially dangerous alias of blocked builtin: {node.value.id}"
            )
        self.generic_visit(node)

    # -- string concatenation reconstructing blocked names -------------------

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.Add):
            if isinstance(node.left, ast.Constant) and isinstance(
                node.right, ast.Constant
            ):
                if isinstance(node.left.value, str) and isinstance(
                    node.right.value, str
                ):
                    combined = node.left.value + node.right.value
                    if combined in BLOCKED_ATTRIBUTES:
                        self.warnings.append(
                            f"String concatenation reconstructs blocked "
                            f"attribute name '{combined}'"
                        )
        self.generic_visit(node)

    # -- blocked attribute names as string literals --------------------------

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and node.value in BLOCKED_ATTRIBUTES:
            # Only flag dunder-style names to avoid false positives on common
            # strings like "format".  Non-dunder blocked attrs (format,
            # format_map, gi_frame) are caught at runtime by safe_getattr and
            # by the worker AST attribute-position check.
            val = node.value
            if val.startswith("__") and val.endswith("__"):
                self.warnings.append(
                    f"String literal '{val}' matches a blocked attribute name"
                )
        self.generic_visit(node)


def is_safe_value(value: Any) -> bool:
    """Check if a value is safe to include in sandbox globals.

    Uses an allowlist approach: only values whose origin is known-safe are
    accepted.  Anything not explicitly permitted is rejected.
    """
    # Safe primitive types
    if isinstance(value, (int, float, str, bool, type(None))):
        return True

    # Safe collections of safe values
    if isinstance(value, (list, tuple, set, frozenset)):
        return all(is_safe_value(item) for item in value)

    if isinstance(value, dict):
        return all(is_safe_value(k) and is_safe_value(v) for k, v in value.items())

    # Type objects: only allow from builtins or geno.*
    if isinstance(value, type):
        module = getattr(value, "__module__", None)
        if not isinstance(module, str):
            return False
        return module == "builtins" or module.startswith("geno.")

    # Callables: allowlist by origin
    if callable(value):
        module = getattr(value, "__module__", None)
        # Lambdas/closures have no module (or it is the sandbox module)
        if module is None:
            return True
        # Geno-defined callables
        if module.startswith("geno."):
            return True
        # Builtins that are in our safe set
        if module == "builtins":
            name = getattr(value, "__name__", None)
            return name in SAFE_BUILTINS
        return False

    return False


# =============================================================================
# Safe Attribute Access (exported functions)
# =============================================================================


def safe_getattr(obj: Any, name: str, *default) -> Any:
    """
    Safe version of getattr that blocks access to dangerous attributes.

    This prevents sandbox escape attempts via attribute access chains like:
    ().__class__.__bases__[0].__subclasses__()

    Args:
        obj: Object to get attribute from
        name: Attribute name
        default: Optional default value if attribute doesn't exist

    Returns:
        The attribute value

    Raises:
        SecurityViolation: If attempting to access a blocked attribute
    """
    if name in BLOCKED_ATTRIBUTES:
        raise SecurityViolation(
            f"Access to attribute '{name}' is not allowed in sandbox"
        )

    # Block dunder attributes except safe ones
    if name.startswith("__") and name.endswith("__"):
        if name not in SAFE_DUNDERS:
            raise SecurityViolation(
                f"Access to attribute '{name}' is not allowed in sandbox"
            )
    # Block single underscore private attributes
    elif name.startswith("_"):
        raise SecurityViolation(
            f"Access to private attribute '{name}' is not allowed in sandbox"
        )

    if default:
        return getattr(obj, name, default[0])
    return getattr(obj, name)


def safe_hasattr(obj: Any, name: str) -> bool:
    """
    Safe version of hasattr that blocks access to dangerous attributes.

    Must mirror the access policy of safe_getattr: allow SAFE_DUNDERS,
    block other dunders and private attributes.

    Args:
        obj: Object to check attribute on
        name: Attribute name

    Returns:
        True if the object has the attribute and it's safe to access
    """
    if name in BLOCKED_ATTRIBUTES:
        return False
    if name.startswith("__") and name.endswith("__"):
        if name not in SAFE_DUNDERS:
            return False
    elif name.startswith("_"):
        return False
    return hasattr(obj, name)


# =============================================================================
# Convenience Functions
# =============================================================================


def _sandbox_config_to_process_config(
    config: SandboxConfig,
) -> "ProcessSandboxConfig":
    return ProcessSandboxConfig(
        timeout=config.timeout,
        max_memory_bytes=config.max_memory_bytes,
        max_cpu_time=config.max_cpu_time,
        max_file_size_bytes=config.max_file_size_bytes,
        max_processes=config.max_processes,
        strict=config.strict,
        max_output_length=config.max_output_length,
        allow_print=config.allow_print,
        max_collection_size=config.max_collection_size,
        max_integer_bits=config.max_integer_bits,
        max_recursion_depth=config.max_recursion_depth,
        compiled_runtime_prelude=config.compiled_runtime_prelude,
        trusted_prelude_line_count=config.trusted_prelude_line_count,
    )


def run_sandboxed(
    code: str, config: SandboxConfig | None = None, use_process: bool = True
) -> tuple[Any, str]:
    """
    Run code in a sandbox and return (result, output).

    Uses ProcessSandbox for maximum security with hard timeouts.
    The ``use_process`` parameter is deprecated and ignored — process
    isolation is always used.

    Args:
        code: Python code to execute
        config: Sandbox configuration
        use_process: Deprecated, ignored.  Kept for API compatibility.

    Returns:
        Tuple of (execution result, captured output)

    Raises:
        SecurityViolation: If code fails safety validation (in strict mode) or attempts blocked operations
        TimeoutError: If execution exceeds time limit
    """
    effective_config = config or SandboxConfig()

    # Validate code safety BEFORE execution (strict mode only — matching
    # ProcessSandbox.execute; the warnings are unused otherwise and the
    # worker-side AST validator still runs unconditionally either way).
    if effective_config.strict:
        warnings = validate_code_safety(code)
        if warnings:
            raise SecurityViolation(
                f"Code failed safety validation: {'; '.join(warnings)}"
            )

    process_config = _sandbox_config_to_process_config(effective_config)
    return run_in_process(code, process_config)


def check_sandbox_escape(code: str) -> bool:
    """
    Check if code might attempt to escape the sandbox.

    Returns True if potential escape attempt detected.
    """
    warnings = validate_code_safety(code)
    return len(warnings) > 0
