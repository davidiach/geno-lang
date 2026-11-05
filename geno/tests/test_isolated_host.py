"""Trusted host side of the isolated ``geno run`` lane (proposal 0013).

These units exercise ``geno.isolated_host`` directly. That module runs with
real builtins inside the worker as well as here, so the units are faithful;
only the runtime prelude's own worker branches need a real ``geno run``.
"""

from __future__ import annotations

import io
import os
import sys
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
    ENV_NAME_TAG_LIMIT,
    STDIN_CHUNK_BYTES,
    IsolatedEnv,
    IsolatedHostBinding,
    IsolatedRunClock,
    IsolatedRunPolicy,
    IsolatedRunPolicyError,
    IsolatedStdin,
    IsolatedStdinDecodeError,
    build_example_interpreter,
    build_host_table,
    checked_prepared_host_state,
    duplicate_program_stdin,
    env_policy_denied_message,
    frontend_error_tags,
    isolated_env_snapshot,
    isolated_run_policy_for,
    with_program_stdin,
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
def test_grants_without_host_capabilities_bind_nothing(grant: frozenset[str]) -> None:
    # Trusted setup always gets the program arguments; without `env` no
    # binding exposes them.
    policy = IsolatedRunPolicy(capabilities=grant)

    assert build_host_table(policy, program_args=["a"]) == {}


def test_env_grant_binds_the_allowlist_snapshot_and_arguments() -> None:
    policy = IsolatedRunPolicy(
        capabilities=frozenset({"env", "print"}),
        env_allowed_names=frozenset({"HOME", "LANG"}),
    )

    table = build_host_table(
        policy, program_args=["a b", "-v"], env_snapshot={"HOME": "/home/caller"}
    )

    assert set(table) == {"env"}
    env = table["env"]
    assert type(env) is IsolatedEnv
    assert env.program_args == ("a b", "-v")
    assert dict(env.environ) == {"HOME": "/home/caller"}
    assert env.lookup("env_get", "HOME") == "/home/caller"
    assert env.lookup("env_get", "LANG") is None


def test_host_table_rejects_a_snapshot_it_cannot_bind() -> None:
    env_policy = IsolatedRunPolicy(
        capabilities=frozenset({"env"}), env_allowed_names=frozenset({"HOME"})
    )

    with pytest.raises(ValueError, match="requires the 'env' capability"):
        build_host_table(IsolatedRunPolicy.default(), env_snapshot={})
    with pytest.raises(ValueError, match="unlisted name"):
        build_host_table(env_policy, env_snapshot={"SECRET": "x"})
    with pytest.raises(TypeError):
        build_host_table(env_policy, env_snapshot={"HOME": 3})  # type: ignore[dict-item]
    with pytest.raises(TypeError):
        build_host_table(env_policy, program_args=["ok", 3])  # type: ignore[list-item]


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


def test_checked_host_state_accepts_only_each_capabilitys_own_binding() -> None:
    policy = IsolatedRunPolicy(capabilities=frozenset({"env", "print"}))
    env = IsolatedEnv((), {}, ())
    runtime = sorted(policy.capabilities)

    table, _used = checked_prepared_host_state(
        _prepared(host_table={"env": env}, runtime_capabilities=runtime), policy
    )

    assert table == {"env": env}
    with pytest.raises(ValueError, match="wrong binding"):
        checked_prepared_host_state(
            _prepared(host_table={"env": _Binding()}, runtime_capabilities=runtime),
            policy,
        )
    with pytest.raises(ValueError, match="wrong binding"):
        checked_prepared_host_state(
            _prepared(host_table={"print": env}, runtime_capabilities=runtime),
            policy,
        )


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


def test_example_interpreter_never_reads_the_workers_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The worker's own environment (temporary HOME, sandbox configuration)
    # must not leak into examples: they read the run's snapshot only.
    monkeypatch.setenv("HOME", "/worker/home")
    monkeypatch.setenv("GENO_SANDBOX_CONFIG", "{}")
    policy = IsolatedRunPolicy(
        capabilities=frozenset({"env"}),
        env_allowed_names=frozenset({"HOME", "GENO_SANDBOX_CONFIG"}),
    )
    table = build_host_table(
        policy, program_args=["x", "y"], env_snapshot={"HOME": "/home/caller"}
    )
    source = """func home() -> String
    example () -> "/home/caller"
    return env_get_or("HOME", "none")
end func

func config() -> Option[String]
    example () -> None
    return env_get("GENO_SANDBOX_CONFIG")
end func

func args() -> List[String]
    example () -> ["x", "y"]
    return cli_args()
end func
"""
    program = Parser(Lexer(source, "ex.geno").tokenize()).parse_program()

    interpreter = build_example_interpreter(policy, SandboxConfig(timeout=5.0), table)
    interpreter.run(program, execute_main=False)

    with pytest.raises(ValueError, match="env grant has no host binding"):
        build_example_interpreter(policy, SandboxConfig(timeout=5.0))


def test_example_env_denial_is_a_located_tagged_e412() -> None:
    policy = IsolatedRunPolicy(
        capabilities=frozenset({"env"}), env_allowed_names=frozenset({"HOME"})
    )
    table = build_host_table(policy, env_snapshot={})
    program = Parser(
        Lexer(
            """func secret() -> String
    example () -> "x"
    return env_get_or("SECRET", "x")
end func
""",
            "ex.geno",
        ).tokenize()
    ).parse_program()
    interpreter = build_example_interpreter(policy, SandboxConfig(timeout=5.0), table)

    with pytest.raises(GenoRuntimeError) as excinfo:
        interpreter.run(program, execute_main=False)

    error = excinfo.value
    assert error.message == env_policy_denied_message("env_get_or", "SECRET")
    assert error.error_code is ErrorCode.RUNTIME_CAPABILITY_DENIED
    assert error.location is not None
    assert (error.location.line, error.location.column) == (3, 12)
    assert error._geno_env_policy_denied == "SECRET"  # type: ignore[attr-defined]
    assert frontend_error_tags(error) == {"env_name": "SECRET"}


def test_example_env_callbacks_keep_the_builtins_limits() -> None:
    policy = IsolatedRunPolicy(
        capabilities=frozenset({"env"}), env_allowed_names=frozenset({"BIG"})
    )
    table = build_host_table(
        policy, program_args=["a" * 50], env_snapshot={"BIG": "b" * 50}
    )
    config = SandboxConfig(timeout=5.0, max_collection_size=10)
    for call in ('env_get("BIG")', 'Some(env_get_or("BIG", ""))'):
        program = Parser(
            Lexer(
                f"func big() -> Option[String]\n    return {call}\nend func\n",
                "ex.geno",
            ).tokenize()
        ).parse_program()
        interpreter = build_example_interpreter(policy, config, table)
        interpreter.run(program, execute_main=False)
        with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
            interpreter.call_function(interpreter.global_env.lookup("big"), [])
    program = Parser(
        Lexer(
            "func args() -> List[String]\n    return cli_args()\nend func\n",
            "ex.geno",
        ).tokenize()
    ).parse_program()
    interpreter = build_example_interpreter(policy, config, table)
    interpreter.run(program, execute_main=False)
    with pytest.raises(GenoRuntimeError, match="String size exceeds limit"):
        interpreter.call_function(interpreter.global_env.lookup("args"), [])


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
    tagged_env_python_error: Any = RuntimeError("x")
    tagged_env_python_error._geno_env_policy_denied = "SECRET"
    assert frontend_error_tags(tagged_env_python_error) == {}


def test_env_name_tag_is_truncated() -> None:
    error: Any = GenoRuntimeError("x")
    error._geno_env_policy_denied = "N" * (ENV_NAME_TAG_LIMIT + 10)

    assert frontend_error_tags(error) == {"env_name": "N" * ENV_NAME_TAG_LIMIT}


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


def test_missing_env_binding_is_a_second_gate_for_every_env_builtin() -> None:
    namespace = _prelude_namespace(
        _GENO_CAPS=frozenset({"env"}), _GENO_HOST=MappingProxyType({})
    )
    unavailable = namespace["_GenoHostUnavailable"]

    for call in (
        lambda: namespace["env_get"]("HOME"),
        lambda: namespace["env_get_or"]("HOME", "x"),
        lambda: namespace["cli_args"](),
    ):
        with pytest.raises(unavailable, match="host access for 'env'"):
            call()


def test_prelude_env_builtins_use_the_binding_and_return_geno_values() -> None:
    env = IsolatedEnv({"HOME", "UNSET"}, {"HOME": "/home/caller"}, ["a b", "-v"])
    namespace = _prelude_namespace(
        _GENO_CAPS=frozenset({"env"}), _GENO_HOST=MappingProxyType({"env": env})
    )

    home = namespace["env_get"]("HOME")
    assert type(home) is namespace["Some"]
    assert home.value == "/home/caller"
    assert namespace["env_get"]("UNSET") is namespace["None_"]
    assert namespace["env_get_or"]("UNSET", "fallback") == "fallback"
    assert namespace["env_get_or"]("HOME", "fallback") == "/home/caller"
    args = namespace["cli_args"]()
    assert type(args) is list
    assert args == ["a b", "-v"]
    for call in (
        lambda: namespace["env_get"]("SECRET"),
        lambda: namespace["env_get_or"]("SECRET", "fallback"),
    ):
        with pytest.raises(RuntimeError) as excinfo:
            call()
        assert type(excinfo.value) is RuntimeError
        assert excinfo.value._geno_env_policy_denied == "SECRET"  # type: ignore[attr-defined]


def test_prelude_env_builtins_still_check_the_grant_first() -> None:
    env = IsolatedEnv({"HOME"}, {"HOME": "/home/caller"}, ["a"])
    namespace = _prelude_namespace(
        _GENO_CAPS=frozenset({"print"}), _GENO_HOST=MappingProxyType({"env": env})
    )

    with pytest.raises(RuntimeError) as excinfo:
        namespace["cli_args"]()

    assert excinfo.value._geno_capability_denied == "env"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# env: allowlist, snapshot and the Windows case rule


def test_env_policy_names_are_exact_on_posix() -> None:
    policy = isolated_run_policy_for(
        {"env", "print"}, timeout=5.0, allow_env=["HOME", "Path"], os_name="posix"
    )

    assert policy.env_allowed_names == frozenset({"HOME", "Path"})
    assert policy.env_case_insensitive is False
    assert IsolatedRunPolicy.from_config(policy.to_config(), "posix") == policy


def test_env_policy_names_are_folded_on_windows() -> None:
    policy = isolated_run_policy_for(
        {"env", "print"}, timeout=5.0, allow_env=["Path", "home"], os_name="nt"
    )

    assert policy.env_allowed_names == frozenset({"PATH", "HOME"})
    assert policy.env_case_insensitive is True
    assert IsolatedRunPolicy.from_config(policy.to_config(), "nt") == policy
    # Folding is Windows-only, and the parent sends the names folded.
    with pytest.raises(IsolatedRunPolicyError, match="only on Windows"):
        IsolatedRunPolicy.from_config(policy.to_config(), "posix")
    with pytest.raises(IsolatedRunPolicyError, match="upper-case"):
        IsolatedRunPolicy.from_config(
            {**policy.to_config(), "env_allowed_names": ["Path"]}, "nt"
        )


def test_env_policy_without_env_folds_nothing() -> None:
    policy = isolated_run_policy_for({"print"}, timeout=5.0, os_name="nt")

    assert policy.env_case_insensitive is False
    with pytest.raises(IsolatedRunPolicyError, match="'env' capability"):
        isolated_run_policy_for({"print"}, timeout=5.0, allow_env=["HOME"])


@pytest.mark.parametrize("name", ["", "A=B", "A\x00B"])
def test_env_policy_rejects_invalid_names(name: str) -> None:
    with pytest.raises(IsolatedRunPolicyError, match="env_allowed_names"):
        isolated_run_policy_for({"env"}, timeout=5.0, allow_env=[name])


def test_snapshot_holds_only_allowed_present_variables_on_posix() -> None:
    environ = {"HOME": "/home/caller", "SECRET": "s", "Path": "/odd"}

    snapshot = isolated_env_snapshot(
        ["HOME", "MISSING", "PATH"], environ=environ, os_name="posix"
    )

    assert snapshot == {"HOME": "/home/caller"}


def test_windows_case_rule_end_to_end() -> None:
    # `--allow-env Path` under a simulated Windows parent and worker permits
    # PATH, Path and path, as Windows' own os.environ would.
    environ = {"Path": "C:\\bin", "SECRET": "s"}
    policy = isolated_run_policy_for(
        {"env"}, timeout=5.0, allow_env=["Path"], os_name="nt"
    )

    snapshot = isolated_env_snapshot(["Path"], environ=environ, os_name="nt")
    env = IsolatedEnv(
        policy.env_allowed_names,
        snapshot,
        (),
        case_insensitive=policy.env_case_insensitive,
    )

    assert snapshot == {"PATH": "C:\\bin"}
    for name in ("PATH", "Path", "path"):
        assert env.lookup("env_get", name) == "C:\\bin"
    with pytest.raises(RuntimeError) as excinfo:
        env.lookup("env_get", "secret")
    # The denial names the variable exactly as the program wrote it.
    assert excinfo.value._geno_env_policy_denied == "secret"  # type: ignore[attr-defined]


def test_posix_case_rule_is_exact() -> None:
    policy = isolated_run_policy_for(
        {"env"}, timeout=5.0, allow_env=["Path"], os_name="posix"
    )
    environ = {"PATH": "/usr/bin", "Path": "/odd"}
    env = IsolatedEnv(
        policy.env_allowed_names,
        isolated_env_snapshot(["Path"], environ=environ, os_name="posix"),
        (),
        case_insensitive=policy.env_case_insensitive,
    )

    assert env.lookup("env_get", "Path") == "/odd"
    with pytest.raises(RuntimeError, match="'PATH' is not allowed"):
        env.lookup("env_get", "PATH")


def test_env_lookup_denies_unlisted_names_even_when_unset() -> None:
    env = IsolatedEnv({"HOME"}, {}, ())

    assert env.lookup("env_get", "HOME") is None
    with pytest.raises(RuntimeError) as excinfo:
        env.lookup("env_get_or", "NEVER_SET_ANYWHERE")
    error = excinfo.value
    assert type(error) is RuntimeError
    assert str(error) == env_policy_denied_message("env_get_or", "NEVER_SET_ANYWHERE")
    assert str(error) == (
        "env_get_or: environment variable 'NEVER_SET_ANYWHERE' is not allowed "
        "by the host env policy"
    )
    with pytest.raises(TypeError, match="name must be a string"):
        env.lookup("env_get", 3)  # type: ignore[arg-type]


def test_env_binding_exposes_only_read_only_views() -> None:
    env = IsolatedEnv({"HOME"}, {"HOME": "/home/caller"}, ["a"])

    with pytest.raises(TypeError):
        env.environ["HOME"] = "changed"  # type: ignore[index]
    assert isinstance(env.program_args, tuple)
    with pytest.raises(AttributeError):
        env.extra = 1  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# stdin: the lazy, bounded reader of the program's input


@pytest.fixture
def stdin_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the lane rules grant ``stdin``, in this process only.

    The real lane keeps ``stdin`` refused until proposal 0013's stdin gate
    passes (``test_platform_matrix``); these units exercise the binding and
    its wiring, and no worker ever sees the patch.
    """
    from geno import capabilities as capabilities_module

    monkeypatch.setattr(
        capabilities_module,
        "ISOLATED_RUN_CAPABILITIES",
        capabilities_module.ISOLATED_RUN_CAPABILITIES | {"stdin"},
    )


class _Input:
    """Program input in a regular file: reads fill whole chunks until the end."""

    def __init__(self, path: Any, data: bytes) -> None:
        path.write_bytes(data)
        self.path = path
        self.fd = os.open(path, os.O_RDONLY)
        assert self.fd >= 3

    def position(self) -> int:
        return os.lseek(self.fd, 0, os.SEEK_CUR)

    def close(self) -> None:
        os.close(self.fd)


@pytest.fixture
def program_input(tmp_path: Any) -> Any:
    opened: list[_Input] = []

    def make(data: bytes) -> _Input:
        source = _Input(tmp_path / f"input{len(opened)}", data)
        opened.append(source)
        return source

    yield make
    for source in opened:
        source.close()


def _no_limit(size: int) -> None:
    pass


def _limit(maximum: int) -> Any:
    def check(size: int) -> None:
        if size > maximum:
            raise RuntimeError(f"String size exceeds limit ({size} > {maximum})")

    return check


def _whole_buffer_error(data: bytes) -> str:
    with pytest.raises(UnicodeDecodeError) as excinfo:
        data.decode("utf-8")
    return str(excinfo.value)


def test_stdin_chunks_are_bounded() -> None:
    assert STDIN_CHUNK_BYTES == 8192


@pytest.mark.parametrize(
    "character", ["é", "€", "\U0001f600"], ids=["2-byte", "3-byte", "4-byte"]
)
def test_stdin_decodes_a_character_split_across_chunks(
    program_input: Any, character: str
) -> None:
    # The character starts at byte 8191, the last byte of the first chunk.
    text = "a" * (STDIN_CHUNK_BYTES - 1) + character + "tail"
    source = program_input(text.encode("utf-8"))

    assert IsolatedStdin(source.fd).read_text(_no_limit) == text


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"a" * 9000 + b"\xff" + b"b" * 10, id="invalid-byte-at-9000"),
        pytest.param(
            b"a" * (STDIN_CHUNK_BYTES - 1) + b"\xe2" + b"A" * 10,
            id="lead-byte-ends-chunk-1",
        ),
        pytest.param(
            b"a" * (STDIN_CHUNK_BYTES - 2) + b"\xf0\x90" + b"A",
            id="partial-sequence-ends-chunk-1",
        ),
        pytest.param(b"a" * 20000 + b"\xe2\x82", id="truncated-at-eof"),
        pytest.param(b"abc\xffdef", id="invalid-byte-at-3"),
        pytest.param(b"\xed\xa0\x80", id="encoded-surrogate"),
    ],
)
def test_stdin_invalid_utf8_matches_the_whole_buffer_text(
    program_input: Any, data: bytes
) -> None:
    source = program_input(data)

    with pytest.raises(IsolatedStdinDecodeError) as excinfo:
        IsolatedStdin(source.fd).read_text(_no_limit)

    assert str(excinfo.value) == _whole_buffer_error(data)
    assert isinstance(excinfo.value, UnicodeError)


@pytest.mark.parametrize("character", ["a", "é"])
def test_stdin_over_limit_stops_within_one_chunk(
    program_input: Any, character: str
) -> None:
    limit = 100
    data = (character * (limit + 3 * STDIN_CHUNK_BYTES)).encode("utf-8")
    source = program_input(data)

    with pytest.raises(RuntimeError) as excinfo:
        IsolatedStdin(source.fd).read_text(_limit(limit))

    message = str(excinfo.value)
    prefix, _, rest = message.partition("String size exceeds limit (")
    assert prefix == ""
    size, _, suffix = rest.partition(f" > {limit})")
    assert suffix == ""
    # The size at detection, not the whole input's.
    assert limit < int(size) <= limit + STDIN_CHUNK_BYTES
    # And the reader stopped there: at most one chunk past the limit in bytes
    # for this input, never the whole stream.
    assert source.position() <= len(character.encode()) * limit + STDIN_CHUNK_BYTES
    assert source.position() < len(data)


def test_stdin_reads_after_the_end_return_nothing(program_input: Any) -> None:
    source = program_input(b"once")
    stdin = IsolatedStdin(source.fd)

    assert stdin.read_text(_no_limit) == "once"
    # No replay, even if more arrives after the end was seen.
    with open(source.path, "ab") as more:
        more.write(b" more")
    assert stdin.read_text(_no_limit) == ""
    assert stdin.read_chunk() == b""


def test_stdin_reads_nothing_until_asked(stdin_lane: None) -> None:
    read_end, write_end = os.pipe()
    try:
        os.write(write_end, b"untouched")
        os.close(write_end)
        policy = IsolatedRunPolicy(
            capabilities=frozenset({"print", "stdin"}), stdin_fd=read_end
        )

        table = build_host_table(policy)
        build_example_interpreter(policy, SandboxConfig(timeout=5.0), table)

        assert type(table["stdin"]) is IsolatedStdin
        assert os.read(read_end, 100) == b"untouched"
    finally:
        os.close(read_end)


def test_stdin_without_a_handle_is_an_os_error() -> None:
    with pytest.raises(OSError, match="stdin is not available"):
        IsolatedStdin(None).read_text(_no_limit)


@pytest.mark.parametrize("fd", [0, 1, 2, -1, True, "3", 3.0])
def test_stdin_handle_cannot_be_a_control_pipe(fd: Any) -> None:
    with pytest.raises(ValueError, match="descriptor >= 3"):
        IsolatedStdin(fd)


def test_stdin_binding_exposes_no_attributes() -> None:
    stdin = IsolatedStdin(None)

    with pytest.raises(AttributeError):
        stdin.extra = 1  # type: ignore[attr-defined]


def test_stdin_policy_round_trips_only_with_the_grant(stdin_lane: None) -> None:
    policy = IsolatedRunPolicy(
        capabilities=frozenset({"print", "stdin"}), stdin_fd=7, deadline_seconds=3.0
    )

    assert IsolatedRunPolicy.from_config(policy.to_config()) == policy
    with pytest.raises(IsolatedRunPolicyError, match="at least 3"):
        IsolatedRunPolicy.from_config(policy.to_config() | {"stdin_fd": 2})
    with pytest.raises(IsolatedRunPolicyError, match="integer or null"):
        IsolatedRunPolicy.from_config(policy.to_config() | {"stdin_fd": "7"})
    # Granted without a usable handle: reads then fail with Err.
    unavailable = IsolatedRunPolicy(capabilities=frozenset({"stdin"}))
    stdin = build_host_table(unavailable)["stdin"]
    assert isinstance(stdin, IsolatedStdin)
    with pytest.raises(OSError, match="stdin is not available"):
        stdin.read_text(_no_limit)


def test_the_real_lane_refuses_a_stdin_policy() -> None:
    policy = IsolatedRunPolicy(capabilities=frozenset({"stdin"}), stdin_fd=7)

    with pytest.raises(IsolatedRunPolicyError, match="'stdin' is not available"):
        IsolatedRunPolicy.from_config(policy.to_config())
    with pytest.raises(IsolatedRunPolicyError):
        build_host_table(policy)


def test_checked_host_state_accepts_the_stdin_binding_only_for_stdin(
    stdin_lane: None,
) -> None:
    policy = IsolatedRunPolicy(capabilities=frozenset({"env", "stdin"}))
    runtime = sorted(policy.capabilities)
    stdin = IsolatedStdin(None)
    env = IsolatedEnv((), {}, ())

    table, _used = checked_prepared_host_state(
        _prepared(
            host_table={"stdin": stdin, "env": env}, runtime_capabilities=runtime
        ),
        policy,
    )

    assert table == {"stdin": stdin, "env": env}
    for wrong in ({"stdin": env}, {"env": stdin}):
        with pytest.raises(ValueError, match="wrong binding"):
            checked_prepared_host_state(
                _prepared(host_table=wrong, runtime_capabilities=runtime), policy
            )


class _Stream:
    def __init__(self, fd: Any) -> None:
        self._fd = fd

    def fileno(self) -> Any:
        if isinstance(self._fd, BaseException):
            raise self._fd
        return self._fd


def test_program_stdin_is_duplicated_only_with_the_grant(
    program_input: Any,
) -> None:
    source = program_input(b"input")
    policy = IsolatedRunPolicy(capabilities=frozenset({"print", "regex"}))

    assert with_program_stdin(policy, _Stream(source.fd)) is policy

    granted = IsolatedRunPolicy(capabilities=frozenset({"print", "stdin"}))
    with_handle = with_program_stdin(granted, _Stream(source.fd))
    try:
        assert with_handle.capabilities == granted.capabilities
        fd = with_handle.stdin_fd
        assert fd is not None and fd >= 3 and fd != source.fd
        # Only pass_fds hands it to the worker.
        assert os.get_inheritable(fd) is False
        assert os.read(fd, 100) == b"input"
    finally:
        if with_handle.stdin_fd is not None:
            os.close(with_handle.stdin_fd)


@pytest.mark.parametrize(
    "stream",
    [
        None,
        _Stream(io.UnsupportedOperation("no fileno")),
        _Stream(OSError("closed")),
        _Stream(ValueError("I/O operation on closed file")),
        _Stream(-1),
        _Stream(True),
        object(),
    ],
)
def test_unusable_program_stdin_gives_no_handle(
    monkeypatch: pytest.MonkeyPatch, stream: Any
) -> None:
    monkeypatch.setattr(sys, "stdin", stream)
    granted = IsolatedRunPolicy(capabilities=frozenset({"stdin"}))

    assert duplicate_program_stdin() is None
    assert with_program_stdin(granted).stdin_fd is None


def test_program_stdin_never_lands_on_a_control_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A caller that closed its stdout and stderr gets 1 and 2 back from dup.
    import geno.isolated_host as isolated_host

    class _FakeOs:
        name = "posix"

        def __init__(self) -> None:
            self.numbers = [1, 2, 9]
            self.closed: list[int] = []

        def dup(self, fd: int) -> int:
            assert fd == 5
            return self.numbers.pop(0)

        def close(self, fd: int) -> None:
            self.closed.append(fd)

    fake = _FakeOs()
    monkeypatch.setattr(isolated_host, "os", fake)

    assert duplicate_program_stdin(_Stream(5)) == 9
    assert fake.closed == [1, 2]


# ---------------------------------------------------------------------------
# stdin through the runtime prelude and the example interpreter


def _standalone_stdin_read_all(
    monkeypatch: pytest.MonkeyPatch, data: bytes, **injected: Any
) -> Any:
    """Standalone compiled ``stdin_read_all`` (no host table) on *data*."""
    namespace = _prelude_namespace(_GENO_CAPS=frozenset({"stdin"}), **injected)
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data)))
    return namespace["stdin_read_all"]()


def test_missing_stdin_binding_is_a_second_gate() -> None:
    namespace = _prelude_namespace(
        _GENO_CAPS=frozenset({"stdin"}),
        _GENO_HOST=MappingProxyType({"env": IsolatedEnv((), {}, ())}),
    )

    with pytest.raises(
        namespace["_GenoHostUnavailable"], match="host access for 'stdin'"
    ):
        namespace["stdin_read_all"]()


def test_prelude_stdin_checks_the_grant_before_reading(program_input: Any) -> None:
    source = program_input(b"untouched")
    namespace = _prelude_namespace(
        _GENO_CAPS=frozenset({"print"}),
        _GENO_HOST=MappingProxyType({"stdin": IsolatedStdin(source.fd)}),
    )

    with pytest.raises(RuntimeError) as excinfo:
        namespace["stdin_read_all"]()

    assert excinfo.value._geno_capability_denied == "stdin"  # type: ignore[attr-defined]
    assert source.position() == 0


@pytest.mark.parametrize(
    "data",
    [
        "héllo\nwörld\n".encode() * 1500,
        b"",
        b"a" * 9000 + b"\xff",
        b"a" * (STDIN_CHUNK_BYTES - 1) + b"\xe2A",
        b"abc\xe2\x82",
    ],
    ids=["text", "empty", "invalid-at-9000", "lead-ends-chunk-1", "truncated"],
)
def test_prelude_stdin_matches_standalone(
    monkeypatch: pytest.MonkeyPatch, program_input: Any, data: bytes
) -> None:
    source = program_input(data)
    namespace = _prelude_namespace(
        _GENO_CAPS=frozenset({"stdin"}),
        _GENO_HOST=MappingProxyType({"stdin": IsolatedStdin(source.fd)}),
    )
    expected = _standalone_stdin_read_all(monkeypatch, data)

    result = namespace["stdin_read_all"]()

    name = type(expected).__name__
    assert name in {"Ok", "Err"}
    assert type(result) is namespace[name]
    field = "value" if name == "Ok" else "error"
    assert getattr(result, field) == getattr(expected, field)
    # A Geno value, never a host object.
    assert type(getattr(result, field)) is str
    # Then the end of input: Ok("").
    again = namespace["stdin_read_all"]()
    assert type(again) is namespace["Ok"]
    assert again.value == ""


def test_prelude_stdin_without_a_handle_is_an_err() -> None:
    namespace = _prelude_namespace(
        _GENO_CAPS=frozenset({"stdin"}),
        _GENO_HOST=MappingProxyType({"stdin": IsolatedStdin(None)}),
    )

    result = namespace["stdin_read_all"]()

    assert type(result) is namespace["Err"]
    assert result.error == "stdin is not available"


def test_prelude_stdin_keeps_the_collection_limit(program_input: Any) -> None:
    limit = 100
    source = program_input(b"a" * (limit + 3 * STDIN_CHUNK_BYTES))
    namespace = _prelude_namespace(
        _GENO_CAPS=frozenset({"stdin"}),
        _GENO_HOST=MappingProxyType({"stdin": IsolatedStdin(source.fd)}),
        _GENO_MAX_COLLECTION_SIZE=limit,
    )

    with pytest.raises(RuntimeError) as excinfo:
        namespace["stdin_read_all"]()

    assert type(excinfo.value) is RuntimeError
    assert str(excinfo.value) == (
        f"String size exceeds limit ({STDIN_CHUNK_BYTES} > {limit})"
    )


EXAMPLE_STDIN_READER = """func read_input() -> String
    example () -> "first"
    match stdin_read_all() with
        | Ok(text) -> return text
        | Err(error) -> return "Err " + error
    end match
end func
"""


def _example_stdin(fd: int | None) -> tuple[Any, IsolatedStdin]:
    policy = IsolatedRunPolicy(capabilities=frozenset({"print", "stdin"}), stdin_fd=fd)
    table = build_host_table(policy)
    interpreter = build_example_interpreter(policy, SandboxConfig(timeout=5.0), table)
    stdin = table["stdin"]
    assert isinstance(stdin, IsolatedStdin)
    return interpreter, stdin


def test_examples_and_main_share_one_stdin_stream(
    stdin_lane: None, program_input: Any
) -> None:
    source = program_input(b"first")
    interpreter, stdin = _example_stdin(source.fd)
    program = Parser(Lexer(EXAMPLE_STDIN_READER, "ex.geno").tokenize()).parse_program()

    interpreter.run(program, execute_main=False)

    # The example consumed the input; `main` reads the same binding.
    namespace = _prelude_namespace(
        _GENO_CAPS=frozenset({"stdin"}), _GENO_HOST=MappingProxyType({"stdin": stdin})
    )
    rest = namespace["stdin_read_all"]()
    assert type(rest) is namespace["Ok"]
    assert rest.value == ""


@pytest.mark.parametrize(
    "data", [b"a" * 9000 + b"\xff", b"abc\xe2\x82"], ids=["invalid", "truncated"]
)
def test_example_and_compiled_stdin_errors_are_identical(
    stdin_lane: None, program_input: Any, data: bytes
) -> None:
    interpreter, _stdin = _example_stdin(program_input(data).fd)
    compiled = _prelude_namespace(
        _GENO_CAPS=frozenset({"stdin"}),
        _GENO_HOST=MappingProxyType({"stdin": IsolatedStdin(program_input(data).fd)}),
    )

    example_result = interpreter.global_env.bindings["stdin_read_all"].func()
    compiled_result = compiled["stdin_read_all"]()

    assert example_result.constructor == "Err"
    assert type(compiled_result) is compiled["Err"]
    assert example_result.fields["error"] == compiled_result.error
    assert compiled_result.error == (
        f"stdin is not valid UTF-8: {_whole_buffer_error(data)}"
    )


def test_example_stdin_keeps_the_interpreters_limits(
    stdin_lane: None, program_input: Any
) -> None:
    from geno.values import RuntimeLimitError

    limit = 100
    source = program_input(b"a" * (limit + 3 * STDIN_CHUNK_BYTES))
    policy = IsolatedRunPolicy(capabilities=frozenset({"stdin"}), stdin_fd=source.fd)
    table = build_host_table(policy)
    interpreter = build_example_interpreter(
        policy, SandboxConfig(timeout=5.0, max_collection_size=limit), table
    )

    with pytest.raises(RuntimeLimitError) as excinfo:
        interpreter.global_env.bindings["stdin_read_all"].func()

    assert excinfo.value.message == (
        f"String size exceeds limit ({STDIN_CHUNK_BYTES} > {limit})"
    )
    unavailable, _stdin = _example_stdin(None)
    result = unavailable.global_env.bindings["stdin_read_all"].func()
    assert result.constructor == "Err"
    assert result.fields["error"] == "stdin is not available"


def test_example_interpreter_needs_the_stdin_binding(stdin_lane: None) -> None:
    policy = IsolatedRunPolicy(capabilities=frozenset({"stdin"}))

    with pytest.raises(ValueError, match="stdin grant has no host binding"):
        build_example_interpreter(policy, SandboxConfig(timeout=5.0))
    with pytest.raises(ValueError, match="stdin grant has no host binding"):
        build_example_interpreter(
            policy,
            SandboxConfig(timeout=5.0),
            {"stdin": IsolatedEnv((), {}, ())},  # type: ignore[dict-item]
        )
