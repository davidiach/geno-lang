"""
Tests for builtins added during the pre-launch improvements pass
================================================================

Covers:
- sleep_ms (clock capability)
- stdin_read_all (stdin capability)
- json_stringify_pretty (always available)
- spawn / spawn_with_input (process capability)
"""

import io
import json
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import geno
from geno.values import ConstructorValue


def _geno_string(value: str) -> str:
    return json.dumps(value)


def _run(source, capabilities=None, host_callbacks=None, timeout=10.0):
    config = geno.RunConfig(
        timeout=timeout, capabilities=capabilities, host_callbacks=host_callbacks
    )
    result = geno.run(source, config=config)
    if not result.ok:
        msgs = "; ".join(d.message for d in result.diagnostics)
        raise AssertionError(f"Program failed: {msgs}")
    return result.value_raw


def _check(source):
    return geno.check(source)


# ---------------------------------------------------------------------------
# sleep_ms
# ---------------------------------------------------------------------------


SLEEP_SOURCE = """
func main() -> Unit
  sleep_ms(10)
end func
"""


class TestSleepMsTypeCheck:
    def test_signature(self):
        assert _check(SLEEP_SOURCE).ok


class TestSleepMs:
    def test_sleeps_roughly_correct_duration(self):
        def _sleep_cb(ms):
            time.sleep(ms / 1000.0)
            return None

        start = time.monotonic()
        _run(
            "func main() -> Unit\n  sleep_ms(50)\nend func\n",
            capabilities={"clock"},
            host_callbacks={"sleep_ms": _sleep_cb},
        )
        elapsed_ms = (time.monotonic() - start) * 1000
        # Allow generous jitter; we only care that it actually blocked.
        assert elapsed_ms >= 40

    def test_zero_is_valid(self):
        def _sleep_cb(ms):
            assert ms == 0
            return None

        _run(
            "func main() -> Unit\n  sleep_ms(0)\nend func\n",
            capabilities={"clock"},
            host_callbacks={"sleep_ms": _sleep_cb},
        )

    def test_negative_raises(self):
        # The _serve.install_clock_callbacks impl rejects negative ms at the
        # host level. We mirror that contract here by having the callback raise.
        def _sleep_cb(ms):
            if ms < 0:
                raise RuntimeError(f"sleep_ms: negative duration not allowed ({ms})")
            return None

        result = geno.run(
            "func main() -> Unit\n  sleep_ms(-5)\nend func\n",
            config=geno.RunConfig(
                timeout=5.0,
                capabilities={"clock"},
                host_callbacks={"sleep_ms": _sleep_cb},
            ),
        )
        assert not result.ok
        assert any(
            "negative" in diagnostic.message for diagnostic in result.diagnostics
        )

    def test_denied_without_clock_capability(self):
        result = geno.run(SLEEP_SOURCE, config=geno.RunConfig(timeout=5.0))
        assert not result.ok


# ---------------------------------------------------------------------------
# stdin_read_all
# ---------------------------------------------------------------------------


STDIN_SOURCE = """
func main() -> Result[String, String]
  return stdin_read_all()
end func
"""


class TestStdinReadAll:
    def test_type_signature(self):
        assert _check(STDIN_SOURCE).ok

    def test_ok_path(self):
        def _stdin_cb():
            return ConstructorValue("Ok", {"value": "hello\n"})

        result = _run(
            STDIN_SOURCE,
            capabilities={"stdin"},
            host_callbacks={"stdin_read_all": _stdin_cb},
        )
        assert result.constructor == "Ok"
        assert result.fields["value"] == "hello\n"

    def test_err_path(self):
        def _stdin_cb():
            return ConstructorValue("Err", {"error": "stdin is not valid UTF-8"})

        result = _run(
            STDIN_SOURCE,
            capabilities={"stdin"},
            host_callbacks={"stdin_read_all": _stdin_cb},
        )
        assert result.constructor == "Err"

    def test_denied_without_stdin_capability(self):
        result = geno.run(STDIN_SOURCE, config=geno.RunConfig(timeout=5.0))
        assert not result.ok

    def test_compiled_helper_accepts_text_only_stdin(self, monkeypatch):
        from geno import _runtime_support as rs

        saved = rs._GENO_CAPS
        rs._GENO_CAPS = {"stdin"}
        monkeypatch.setattr(sys, "stdin", io.StringIO("hello\n"))
        try:
            result = rs.stdin_read_all()
        finally:
            rs._GENO_CAPS = saved
        assert isinstance(result, rs.Ok)
        assert result.value == "hello\n"

    def test_cli_callback_accepts_text_only_stdin(self, monkeypatch):
        from geno._serve import install_stdin_callbacks
        from geno.interpreter import Interpreter

        interpreter = Interpreter()
        install_stdin_callbacks(interpreter)
        monkeypatch.setattr(sys, "stdin", io.StringIO("hello\n"))

        result = interpreter.global_env.bindings["stdin_read_all"].func()

        assert result.constructor == "Ok"
        assert result.fields["value"] == "hello\n"


# ---------------------------------------------------------------------------
# json_stringify_pretty
# ---------------------------------------------------------------------------


class TestBoolRejectedAsInt:
    """Python's ``isinstance(True, int)`` is True. Builtins declaring Int
    parameters must reject bool explicitly, or interpreter vs compiled-Python
    backends diverge on what's a valid Int."""

    def test_json_stringify_pretty_rejects_bool_indent(self):
        from geno.builtins import builtin_json_stringify_pretty
        from geno.values import ConstructorValue as CV
        from geno.values import GenoRuntimeError

        empty_obj = CV("JsonObject", {"entries": []})
        with pytest.raises(GenoRuntimeError, match="indent must be Int"):
            builtin_json_stringify_pretty(empty_obj, True)

    def test_sleep_ms_rejects_bool_via_compiled_helper(self):
        # Direct call to the compiled-Python helper — verify it rejects bool.
        # Grant the clock capability by patching the module-level _GENO_CAPS.
        from geno import _runtime_support as rs

        saved = rs._GENO_CAPS
        rs._GENO_CAPS = {"clock"}
        try:
            with pytest.raises(RuntimeError, match="expected Int"):
                rs.sleep_ms(True)
        finally:
            rs._GENO_CAPS = saved

    def test_json_stringify_pretty_compiled_helper_rejects_bool(self):
        # Compiled-Python backend must match interpreter on bool rejection.
        from geno import _runtime_support as rs

        empty_obj = rs.JsonObject([])
        with pytest.raises(RuntimeError, match="indent must be Int"):
            rs.json_stringify_pretty(empty_obj, False)


class TestJsonStringifyPretty:
    def test_type_signature(self):
        source = """
func main() -> String
  match json_parse("{\\"a\\":1}") with
  | Ok(v) -> return json_stringify_pretty(v, 2)
  | Err(_) -> return ""
  end match
end func
"""
        assert _check(source).ok

    def test_pretty_has_newlines(self):
        source = """
func main() -> String
  match json_parse("{\\"a\\":1,\\"b\\":2}") with
  | Ok(v) -> return json_stringify_pretty(v, 2)
  | Err(_) -> return ""
  end match
end func
"""
        out = _run(source)
        assert "\n" in out
        # 2-space indent
        assert '  "a"' in out

    def test_zero_indent_is_compact(self):
        source = """
func main() -> String
  match json_parse("{\\"a\\":1,\\"b\\":2}") with
  | Ok(v) -> return json_stringify_pretty(v, 0)
  | Err(_) -> return ""
  end match
end func
"""
        out = _run(source)
        assert "\n" not in out

    def test_negative_indent_is_compact(self):
        source = """
func main() -> String
  match json_parse("{\\"a\\":1}") with
  | Ok(v) -> return json_stringify_pretty(v, -3)
  | Err(_) -> return ""
  end match
end func
"""
        out = _run(source)
        assert "\n" not in out

    def test_preserves_insertion_order(self):
        source = """
func main() -> String
  match json_parse("{\\"z\\":1,\\"a\\":2,\\"m\\":3}") with
  | Ok(v) -> return json_stringify_pretty(v, 2)
  | Err(_) -> return ""
  end match
end func
"""
        out = _run(source)
        z_idx = out.index('"z"')
        a_idx = out.index('"a"')
        m_idx = out.index('"m"')
        assert z_idx < a_idx < m_idx


# ---------------------------------------------------------------------------
# spawn / spawn_with_input
# ---------------------------------------------------------------------------


def _process_ok(*, exit_code, stdout="", stderr=""):
    return ConstructorValue(
        "Ok",
        {
            "value": ConstructorValue(
                "ProcessResult",
                {
                    "exit_code": exit_code,
                    "stdout": stdout,
                    "stderr": stderr,
                },
            )
        },
    )


def _real_spawn(program, args):
    try:
        result = subprocess.run(
            [program, *args], capture_output=True, text=True, timeout=10
        )
        return _process_ok(
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    except Exception as e:
        return ConstructorValue("Err", {"error": str(e)})


def _real_spawn_with_input(program, args, stdin_text):
    try:
        result = subprocess.run(
            [program, *args],
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return _process_ok(
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    except Exception as e:
        return ConstructorValue("Err", {"error": str(e)})


SPAWN_CALLBACKS = {
    "spawn": _real_spawn,
    "spawn_with_input": _real_spawn_with_input,
}


class TestSpawn:
    def test_type_signature(self):
        source = """
func main() -> Result[ProcessResult, String]
  return spawn("python", ["-c", "print('ok')"])
end func
"""
        assert _check(source).ok

    def test_spawn_python_no_quoting(self):
        source = f"""
func main() -> Result[ProcessResult, String]
  return spawn({_geno_string(sys.executable)}, ["-c", "print('ok')"])
end func
"""
        result = _run(source, capabilities={"process"}, host_callbacks=SPAWN_CALLBACKS)
        assert result.constructor == "Ok"
        pr = result.fields["value"]
        assert pr.fields["exit_code"] == 0
        assert "ok" in pr.fields["stdout"]

    def test_spawn_argv_preserves_spaces(self):
        # This is the headline case — a single argv entry containing spaces
        # must NOT be shell-split.
        source = f"""
func main() -> Result[ProcessResult, String]
  return spawn({_geno_string(sys.executable)}, ["-c", "import sys; print(len(sys.argv), sys.argv[1])", "hello world"])
end func
"""
        result = _run(source, capabilities={"process"}, host_callbacks=SPAWN_CALLBACKS)
        assert result.constructor == "Ok"
        pr = result.fields["value"]
        # argv: [<script>, "hello world"] -> len == 2
        assert "2 hello world" in pr.fields["stdout"]

    def test_spawn_with_input(self):
        source = f"""
func main() -> Result[ProcessResult, String]
  return spawn_with_input(program: {_geno_string(sys.executable)}, args: ["-c", "import sys; sys.stdout.write(sys.stdin.read())"], stdin: "piped\\n")
end func
"""
        result = _run(source, capabilities={"process"}, host_callbacks=SPAWN_CALLBACKS)
        assert result.constructor == "Ok"
        pr = result.fields["value"]
        assert pr.fields["stdout"] == "piped\n"

    def test_spawn_denied_without_capability(self):
        source = """
func main() -> Result[ProcessResult, String]
  return spawn("true", [])
end func
"""
        result = geno.run(source, config=geno.RunConfig(timeout=5.0))
        assert not result.ok
