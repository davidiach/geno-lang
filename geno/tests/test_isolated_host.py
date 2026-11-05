"""Trusted host side of the isolated ``geno run`` lane (proposal 0013).

These units exercise ``geno.isolated_host`` directly. That module runs with
real builtins inside the worker as well as here, so the units are faithful;
only the runtime prelude's own worker branches need a real ``geno run``.
"""

from __future__ import annotations

import time
from types import MappingProxyType
from typing import Any

import pytest

from geno.capabilities import (
    DEFAULT_ALLOWED_CAPABILITIES,
    ISOLATED_HOST_PRIVATE_NAMES,
    ISOLATED_RUN_REFUSED_CAPABILITIES,
    KNOWN_CAPABILITIES,
    isolated_run_capabilities,
)
from geno.diagnostics import ErrorCode
from geno.isolated_host import (
    IsolatedHostBinding,
    IsolatedRunClock,
    IsolatedRunPolicy,
    IsolatedRunPolicyError,
    build_example_interpreter,
    build_host_table,
    checked_prepared_host_state,
    frontend_error_tags,
    isolated_run_policy_for,
)
from geno.lexer import Lexer
from geno.parser import Parser
from geno.sandbox import SandboxConfig
from geno.values import BuiltinFunction, GenoRuntimeError

NOT_YET_ISOLATED = sorted(
    KNOWN_CAPABILITIES - isolated_run_capabilities() - ISOLATED_RUN_REFUSED_CAPABILITIES
)


def _config(**overrides: Any) -> dict[str, Any]:
    return {**IsolatedRunPolicy.default().to_config(), **overrides}


# ---------------------------------------------------------------------------
# IsolatedRunPolicy


def test_default_policy_round_trips() -> None:
    policy = IsolatedRunPolicy.default()

    assert policy.capabilities == DEFAULT_ALLOWED_CAPABILITIES
    assert IsolatedRunPolicy.from_config(policy.to_config()) == policy
    assert policy.to_config() == {
        "capabilities": ["clock", "print", "random"],
        "env_allowed_names": [],
        "env_case_insensitive": False,
        "fs_read_only": False,
        "fs_configured_roots": None,
        "fs_allow_absolute": False,
        "max_total_write_bytes": 0,
        "http_allow_private": False,
        "stdin_fd": None,
        "deadline_seconds": None,
    }


def test_policy_for_a_grant_round_trips_with_its_deadline() -> None:
    policy = isolated_run_policy_for({"print", "regex"}, timeout=12.5)

    assert policy.capabilities == frozenset({"print", "regex"})
    assert policy.deadline_seconds == 12.5
    assert IsolatedRunPolicy.from_config(policy.to_config()) == policy


@pytest.mark.parametrize("timeout", [None, 0.0, -1.0, float("inf"), float("nan")])
def test_policy_for_a_run_without_a_usable_deadline_sends_none(
    timeout: float | None,
) -> None:
    assert isolated_run_policy_for(None, timeout=timeout).deadline_seconds is None


def test_empty_grant_is_allowed_and_exact() -> None:
    policy = IsolatedRunPolicy.from_config(_config(capabilities=[]))

    assert policy.capabilities == frozenset()


@pytest.mark.parametrize(
    "capability",
    sorted(ISOLATED_RUN_REFUSED_CAPABILITIES) + NOT_YET_ISOLATED,
)
def test_from_config_rejects_capabilities_the_lane_does_not_grant(
    capability: str,
) -> None:
    with pytest.raises(IsolatedRunPolicyError, match="not available"):
        IsolatedRunPolicy.from_config(_config(capabilities=["print", capability]))


def test_from_config_rejects_unknown_capabilities() -> None:
    with pytest.raises(IsolatedRunPolicyError, match="unknown capability 'regx'"):
        IsolatedRunPolicy.from_config(_config(capabilities=["regx"]))


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        "capabilities",
        {},
    ],
)
def test_from_config_rejects_a_missing_or_malformed_policy(raw: object) -> None:
    with pytest.raises(IsolatedRunPolicyError):
        IsolatedRunPolicy.from_config(raw)


def test_from_config_rejects_extra_and_missing_keys() -> None:
    extra = _config(grant_everything=True)
    missing = _config()
    del missing["stdin_fd"]

    with pytest.raises(IsolatedRunPolicyError, match="unexpected grant_everything"):
        IsolatedRunPolicy.from_config(extra)
    with pytest.raises(IsolatedRunPolicyError, match="missing stdin_fd"):
        IsolatedRunPolicy.from_config(missing)


@pytest.mark.parametrize(
    "overrides",
    [
        {"capabilities": "print"},
        {"capabilities": ["print", "print"]},
        {"capabilities": ["print", 3]},
        {"capabilities": [""]},
        {"env_allowed_names": "HOME"},
        {"env_case_insensitive": 0},
        {"fs_read_only": "yes"},
        {"fs_configured_roots": "/tmp"},
        {"fs_configured_roots": [""]},
        {"fs_allow_absolute": None},
        {"max_total_write_bytes": "0"},
        {"max_total_write_bytes": False},
        {"max_total_write_bytes": 1.0},
        {"http_allow_private": 1},
        {"stdin_fd": "3"},
        {"stdin_fd": True},
        {"deadline_seconds": "30"},
        {"deadline_seconds": True},
        {"deadline_seconds": 0},
        {"deadline_seconds": -1.0},
    ],
)
def test_from_config_rejects_wrong_types_and_values(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(IsolatedRunPolicyError):
        IsolatedRunPolicy.from_config(_config(**overrides))


@pytest.mark.parametrize(
    ("overrides", "capability"),
    [
        ({"env_allowed_names": ["HOME"]}, "env"),
        ({"env_case_insensitive": True}, "env"),
        ({"fs_read_only": True}, "fs"),
        ({"fs_configured_roots": ["/tmp"]}, "fs"),
        ({"fs_configured_roots": []}, "fs"),
        ({"fs_allow_absolute": True}, "fs"),
        ({"max_total_write_bytes": 1}, "fs"),
        ({"http_allow_private": True}, "http"),
        ({"stdin_fd": 3}, "stdin"),
    ],
)
def test_from_config_rejects_settings_for_an_ungranted_capability(
    overrides: dict[str, Any], capability: str
) -> None:
    with pytest.raises(IsolatedRunPolicyError, match=f"'{capability}' capability"):
        IsolatedRunPolicy.from_config(_config(**overrides))


def test_validate_checks_the_platform() -> None:
    # The lane rules, not the caller, decide what a platform grants.
    policy = IsolatedRunPolicy(capabilities=frozenset({"print", "regex"}))

    policy.validate("posix")
    policy.validate("nt")
    with pytest.raises(IsolatedRunPolicyError):
        IsolatedRunPolicy(capabilities=frozenset({"process"})).validate("posix")


# ---------------------------------------------------------------------------
# Clock


def test_clock_counts_down_from_its_start() -> None:
    clock = IsolatedRunClock(10.0, started_at=time.monotonic() - 4.0)

    remaining = clock.remaining()
    assert remaining is not None
    assert 5.0 < remaining <= 6.0
    assert IsolatedRunClock(None).remaining() is None


# ---------------------------------------------------------------------------
# Host table


@pytest.mark.parametrize(
    "grant",
    [
        DEFAULT_ALLOWED_CAPABILITIES,
        frozenset({"regex"}),
        frozenset({"clock", "print", "random", "regex"}),
        frozenset(),
    ],
)
def test_no_supported_grant_binds_host_access_yet(grant: frozenset[str]) -> None:
    assert build_host_table(IsolatedRunPolicy(capabilities=grant)) == {}


def test_build_host_table_rejects_an_invalid_policy() -> None:
    with pytest.raises(IsolatedRunPolicyError):
        build_host_table(IsolatedRunPolicy(capabilities=frozenset({"process"})))


class _Binding(IsolatedHostBinding):
    __slots__ = ()


def _prepared(**overrides: Any) -> dict[str, Any]:
    return {
        "host_table": {},
        "example_output_length": 0,
        "runtime_capabilities": sorted(DEFAULT_ALLOWED_CAPABILITIES),
        **overrides,
    }


def test_checked_host_state_accepts_the_prepared_state() -> None:
    policy = IsolatedRunPolicy.default()

    table, used = checked_prepared_host_state(
        _prepared(example_output_length=7), policy
    )

    assert table == {}
    assert used == 7


@pytest.mark.parametrize(
    "overrides",
    [
        {"host_table": None},
        {"host_table": MappingProxyType({})},
        # Only granted capabilities may have a binding.
        {"host_table": {"fs": _Binding()}},
        # Only trusted binding objects, never modules or plain values.
        {"host_table": {"print": object()}},
        {"host_table": {"print": time}},
        {"example_output_length": -1},
        {"example_output_length": True},
        {"example_output_length": "0"},
        {"runtime_capabilities": ["clock", "print", "random", "regex"]},
        {"runtime_capabilities": ("clock", "print", "random")},
    ],
)
def test_checked_host_state_rejects_anything_else(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        checked_prepared_host_state(_prepared(**overrides), IsolatedRunPolicy.default())


# ---------------------------------------------------------------------------
# Example interpreter


def test_example_interpreter_keeps_a_non_default_grant() -> None:
    policy = IsolatedRunPolicy(capabilities=frozenset({"regex", "print"}))

    interpreter = build_example_interpreter(policy, SandboxConfig(timeout=5.0))

    regex_match = interpreter.global_env.bindings["regex_match"]
    env_get = interpreter.global_env.bindings["env_get"]
    clock_now = interpreter.global_env.bindings["clock_now"]
    assert isinstance(regex_match, BuiltinFunction)
    assert isinstance(env_get, BuiltinFunction)
    assert regex_match.func.__name__ != "denied"
    assert env_get.func.__name__ == "denied"
    # Omitted defaults are not restored either.
    assert isinstance(clock_now, BuiltinFunction)
    assert clock_now.func.__name__ == "denied"


def _run_example_program(source: str, grant: set[str]) -> Any:
    program = Parser(Lexer(source, "ex.geno").tokenize()).parse_program()
    interpreter = build_example_interpreter(
        IsolatedRunPolicy(capabilities=frozenset(grant)), SandboxConfig(timeout=5.0)
    )
    interpreter.run(program, execute_main=False)
    return interpreter


def test_example_interpreter_runs_regex_examples() -> None:
    source = """func first(text: String) -> Option[String]
    example "ab cd" -> Some("ab")
    return regex_match(pattern: "[a-z]+", text: text)
end func
"""
    _run_example_program(source, {"regex"})

    with pytest.raises(GenoRuntimeError, match="requires the 'regex' capability"):
        _run_example_program(source, {"print"})


def test_example_interpreter_installs_sleep_only_with_clock() -> None:
    source = """func pause(ms: Int) -> Int
    example 1 -> 1
    sleep_ms(ms)
    return ms
end func
"""
    _run_example_program(source, {"clock"})

    with pytest.raises(GenoRuntimeError, match="requires the 'clock' capability"):
        _run_example_program(source, {"print"})


def test_example_interpreter_records_example_output() -> None:
    source = """func noisy(n: Int) -> Int
    example 2 -> 2
    print("seven..")
    return n
end func
"""
    interpreter = _run_example_program(source, {"print"})

    assert interpreter.get_output() == ""
    assert interpreter.example_output_length == len("seven..\n")


# ---------------------------------------------------------------------------
# Denial tags


def _denial_from(source: str, grant: set[str]) -> GenoRuntimeError:
    with pytest.raises(GenoRuntimeError) as excinfo:
        _run_example_program(source, grant)
    return excinfo.value


def test_denial_stub_tags_survive_the_call_site_rewrap() -> None:
    error = _denial_from(
        """func first(text: String) -> Option[String]
    example "ab" -> Some("ab")
    return regex_match(pattern: "[a-z]+", text: text)
end func
""",
        {"print"},
    )

    assert error.error_code is ErrorCode.RUNTIME_CAPABILITY_DENIED
    assert error.location is not None
    assert (error.location.line, error.location.column) == (3, 12)
    assert error._geno_capability_denied == "regex"  # type: ignore[attr-defined]
    assert frontend_error_tags(error) == {"capability": "regex"}


def test_call_site_rewrap_copies_an_env_policy_tag() -> None:
    from geno.interpreter import Interpreter

    def refused(name: str) -> None:
        error: Any = GenoRuntimeError(
            f"env_get: environment variable {name!r} is not allowed by the host "
            "env policy",
            error_code=ErrorCode.RUNTIME_CAPABILITY_DENIED,
        )
        error._geno_env_policy_denied = name
        raise error

    program = Parser(
        Lexer(
            'func main() -> Option[String]\n    return env_get("SECRET")\nend func\n',
            "env.geno",
        ).tokenize()
    ).parse_program()
    interpreter = Interpreter(capabilities={"env"})
    interpreter.global_env.bindings["env_get"] = BuiltinFunction(
        "env_get", refused, 1, ["name"]
    )

    with pytest.raises(GenoRuntimeError) as excinfo:
        interpreter.run(program)

    error = excinfo.value
    assert error.location is not None
    assert error._geno_env_policy_denied == "SECRET"  # type: ignore[attr-defined]
    assert not hasattr(error, "_geno_capability_denied")


def test_forged_denials_carry_no_tag() -> None:
    forged = _denial_from(
        """func forged(n: Int) -> Int
    example 1 -> 1
    panic("Capability denied: 'regex_match' requires the 'regex' capability")
end func
""",
        {"print"},
    )

    assert frontend_error_tags(forged) == {}
    assert frontend_error_tags(RuntimeError("Capability denied")) == {}
    tagged_python_error: Any = RuntimeError("x")
    tagged_python_error._geno_capability_denied = "regex"
    # Frontend tags come only from the interpreter's own error type.
    assert frontend_error_tags(tagged_python_error) == {}
    unknown: Any = GenoRuntimeError("x")
    unknown._geno_capability_denied = "everything"
    assert frontend_error_tags(unknown) == {}


# ---------------------------------------------------------------------------
# The runtime prelude's host channel


def _prelude_namespace(**injected: Any) -> dict[str, Any]:
    from geno.runtime_prelude import RUNTIME_PRELUDE

    namespace: dict[str, Any] = {**injected}
    exec(compile(RUNTIME_PRELUDE, "<prelude>", "exec"), namespace)
    return namespace


def test_prelude_binds_exactly_the_reserved_host_names() -> None:
    namespace = _prelude_namespace()

    assert set(namespace) >= ISOLATED_HOST_PRIVATE_NAMES


def test_standalone_prelude_has_no_host_table() -> None:
    namespace = _prelude_namespace()

    assert namespace["_GENO_HOST"] is None
    assert namespace["_geno_host_module"]("env") is None
    assert namespace["_geno_environ"]() is None


def test_missing_binding_is_an_independent_second_gate() -> None:
    namespace = _prelude_namespace(
        _GENO_CAPS=frozenset({"env"}), _GENO_HOST=MappingProxyType({})
    )
    unavailable = namespace["_GenoHostUnavailable"]

    with pytest.raises(unavailable, match="host access for 'env' is not bound"):
        namespace["_geno_host_module"]("env")
    with pytest.raises(unavailable):
        namespace["_geno_environ"]()
    # It is a RuntimeError but never the capability-denial type the worker
    # tags, so it cannot be mistaken for (or turned into) a missing grant.
    assert issubclass(unavailable, RuntimeError)
    assert unavailable is not RuntimeError


def test_bound_host_object_is_returned_to_trusted_prelude_code() -> None:
    class _Env(IsolatedHostBinding):
        __slots__ = ("environ",)

        def __init__(self) -> None:
            self.environ = MappingProxyType({"HOME": "/home/caller"})

    binding = _Env()
    namespace = _prelude_namespace(
        _GENO_CAPS=frozenset({"env"}), _GENO_HOST=MappingProxyType({"env": binding})
    )

    assert namespace["_geno_host_module"]("env") is binding
    assert namespace["_geno_environ"]() == {"HOME": "/home/caller"}
