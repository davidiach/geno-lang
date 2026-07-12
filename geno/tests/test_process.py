"""
Tests for process execution builtins
=====================================
"""

import json
import os
import shlex
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import geno
from geno.compiler import compile_to_python
from geno.js_compiler import compile_to_js
from geno.values import ConstructorValue


def _process_ok(
    *, exit_code: int, stdout: str = "", stderr: str = ""
) -> ConstructorValue:
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


def _fake_exec(command):
    """Host callback for exec — runs a real subprocess."""
    canned_results = {
        "echo hello": _process_ok(exit_code=0, stdout="hello\n"),
        "echo done": _process_ok(exit_code=0, stdout="done\n"),
        "false": _process_ok(exit_code=1),
        "sh -c 'echo errmsg >&2'": _process_ok(exit_code=0, stderr="errmsg\n"),
    }
    if command in canned_results:
        return canned_results[command]

    try:
        result = subprocess.run(
            command,
            shell=True,
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


def _fake_exec_with_input(command, stdin_text):
    """Host callback for exec_with_input — runs a real subprocess with stdin."""
    if command == "cat":
        return _process_ok(exit_code=0, stdout=stdin_text)

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
            input=stdin_text,
        )
        return _process_ok(
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    except Exception as e:
        return ConstructorValue("Err", {"error": str(e)})


def run(source: str, capabilities=None, host_callbacks=None):
    config = geno.RunConfig(
        timeout=10.0, capabilities=capabilities, host_callbacks=host_callbacks
    )
    result = geno.run(source, config=config)
    if not result.ok:
        msgs = "; ".join(d.message for d in result.diagnostics)
        raise AssertionError(f"Program failed: {msgs}")
    return result.value_raw


def check(source: str):
    return geno.check(source)


PROCESS_CALLBACKS = {
    "exec": _fake_exec,
    "exec_with_input": _fake_exec_with_input,
}


def _skip_if_node_child_process_unavailable() -> None:
    probe = (
        "const { spawnSync } = require('child_process');"
        f"const r = spawnSync({json.dumps(sys.executable)}, "
        "['-c', 'print(\"ok\")'], { encoding: 'utf8' });"
        "if (r.error) { process.stdout.write(r.error.message); process.exit(2); }"
        "if (r.status !== 0) {"
        "  process.stdout.write(r.stderr || r.stdout || String(r.status));"
        "  process.exit(3);"
        "}"
        "process.stdout.write(r.stdout);"
    )
    try:
        result = subprocess.run(
            ["node", "-e", probe],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        pytest.skip("Node.js not available")
    except subprocess.TimeoutExpired:
        pytest.skip("Node.js child_process.spawnSync probe timed out")

    if result.returncode != 0 or result.stdout.strip() != "ok":
        detail = (result.stdout or result.stderr).strip() or f"exit {result.returncode}"
        pytest.skip(f"Node.js child_process.spawnSync unavailable: {detail}")


EXEC_SOURCE = """
func main() -> Result[ProcessResult, String]
  return exec("echo hello")
end func
"""

EXEC_WITH_INPUT_SOURCE = """
func main() -> Result[ProcessResult, String]
  return exec_with_input("cat", "piped input")
end func
"""


class TestExecTypeCheck:
    def test_exec_type_signature(self):
        result = check(EXEC_SOURCE)
        assert result.ok

    def test_exec_with_input_type_signature(self):
        result = check(EXEC_WITH_INPUT_SOURCE)
        assert result.ok

    def test_process_result_fields(self):
        source = """
func main() -> String
  let result: Result[ProcessResult, String] = exec("echo hi")
  match result with
  | Ok(pr) ->
    match pr with
    | ProcessResult(code, out, err) -> return out
    end match
  | Err(msg) -> return msg
  end match
end func
"""
        result = check(source)
        assert result.ok


class TestExecCapabilityDenied:
    def test_exec_denied_without_capability(self):
        """exec without 'process' capability should fail at runtime."""
        result = geno.run(EXEC_SOURCE, config=geno.RunConfig(timeout=10.0))
        assert not result.ok
        msgs = " ".join(d.message for d in result.diagnostics)
        assert "callback" in msgs.lower() or "denied" in msgs.lower()

    def test_exec_with_input_denied_without_capability(self):
        result = geno.run(EXEC_WITH_INPUT_SOURCE, config=geno.RunConfig(timeout=10.0))
        assert not result.ok

    def test_exec_cap_granted_but_no_callback(self):
        """Capability granted but no host callback provided — should still fail."""
        result = geno.run(
            EXEC_SOURCE,
            config=geno.RunConfig(timeout=10.0, capabilities={"process"}),
        )
        assert not result.ok
        msgs = " ".join(d.message for d in result.diagnostics)
        assert "callback" in msgs.lower()


class TestExecWithCapability:
    def test_exec_echo(self):
        result = run(
            EXEC_SOURCE,
            capabilities={"process"},
            host_callbacks=PROCESS_CALLBACKS,
        )
        assert result.constructor == "Ok"
        pr = result.fields["value"]
        assert pr.constructor == "ProcessResult"
        assert pr.fields["exit_code"] == 0
        assert "hello" in pr.fields["stdout"]

    def test_exec_with_input_cat(self):
        result = run(
            EXEC_WITH_INPUT_SOURCE,
            capabilities={"process"},
            host_callbacks=PROCESS_CALLBACKS,
        )
        assert result.constructor == "Ok"
        pr = result.fields["value"]
        assert pr.constructor == "ProcessResult"
        assert pr.fields["exit_code"] == 0
        assert pr.fields["stdout"] == "piped input"

    def test_exec_nonzero_exit(self):
        source = """
func main() -> Result[ProcessResult, String]
  return exec("false")
end func
"""
        result = run(
            source,
            capabilities={"process"},
            host_callbacks=PROCESS_CALLBACKS,
        )
        assert result.constructor == "Ok"
        pr = result.fields["value"]
        assert pr.fields["exit_code"] != 0

    def test_exec_stderr_capture(self):
        source = """
func main() -> Result[ProcessResult, String]
  return exec("sh -c 'echo errmsg >&2'")
end func
"""
        result = run(
            source,
            capabilities={"process"},
            host_callbacks=PROCESS_CALLBACKS,
        )
        assert result.constructor == "Ok"
        pr = result.fields["value"]
        assert "errmsg" in pr.fields["stderr"]

    def test_process_result_match(self):
        source = """
func main() -> Int
  let result: Result[ProcessResult, String] = exec("echo done")
  match result with
  | Ok(pr) ->
    match pr with
    | ProcessResult(code, out, err) -> return code
    end match
  | Err(_) -> return -1
  end match
end func
"""
        result = run(
            source,
            capabilities={"process"},
            host_callbacks=PROCESS_CALLBACKS,
        )
        assert result == 0


class TestExecCompiled:
    def test_compiled_python_contains_exec(self):
        py_code = compile_to_python(EXEC_SOURCE)
        assert "exec_" in py_code  # Python mangles exec -> exec_

    def test_compiled_js_rejects_exec_on_node_target(self):
        with pytest.raises(Exception, match="not available on the 'node-cli' target"):
            compile_to_js(EXEC_SOURCE)

    def test_js_exec_codegen_requires_explicit_permissive_compile(self, tmp_path):
        _skip_if_node_child_process_unavailable()
        script = 'print("hi")'
        command = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"
        source = f"""
func main() -> String
  let result: Result[ProcessResult, String] = exec({json.dumps(command)})
  match result with
  | Ok(_) -> return "ok"
  | Err(msg) -> return msg
  end match
end func
"""
        js_code = compile_to_js(source, typecheck=False)
        js_file = tmp_path / "exec_test.js"
        js_file.write_text(js_code)
        result = subprocess.run(
            ["node", str(js_file), "--cap", "process"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "ok" in result.stdout.lower()

    def test_js_exec_uses_argv_semantics_for_shell_metacharacters(self, tmp_path):
        _skip_if_node_child_process_unavailable()
        script = "import sys;sys.stdout.write(chr(124).join(sys.argv[1:]))"
        command = (
            f"{shlex.quote(sys.executable)} -c {shlex.quote(script)} "
            "-- && echo SHELL_EXPANDED"
        )
        source = f"""
func main() -> String
  let result: Result[ProcessResult, String] = exec({json.dumps(command)})
  match result with
  | Ok(pr) ->
    match pr with
    | ProcessResult(_, out, _) -> return out
    end match
  | Err(msg) -> return msg
  end match
end func
"""
        js_code = compile_to_js(source, typecheck=False)
        js_file = tmp_path / "exec_argv_test.js"
        js_file.write_text(js_code)

        result = subprocess.run(
            ["node", str(js_file), "--cap", "process"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0
        assert "--|&&|echo|SHELL_EXPANDED" in result.stdout

    def test_js_exec_with_input_uses_argv_semantics_for_metacharacters(self, tmp_path):
        _skip_if_node_child_process_unavailable()
        script = (
            "import sys;"
            "sys.stdout.write(sys.stdin.read()+chr(124)+chr(124).join(sys.argv[1:]))"
        )
        command = (
            f"{shlex.quote(sys.executable)} -c {shlex.quote(script)} "
            "-- && echo SHELL_EXPANDED"
        )
        source = f"""
func main() -> String
  let result: Result[ProcessResult, String] = exec_with_input({json.dumps(command)}, "payload")
  match result with
  | Ok(pr) ->
    match pr with
    | ProcessResult(_, out, _) -> return out
    end match
  | Err(msg) -> return msg
  end match
end func
"""
        js_code = compile_to_js(source, typecheck=False)
        js_file = tmp_path / "exec_with_input_argv_test.js"
        js_file.write_text(js_code)

        result = subprocess.run(
            ["node", str(js_file), "--cap", "process"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0
        assert "payload|--|&&|echo|SHELL_EXPANDED" in result.stdout


class TestProcessEnvIsolation:
    def test_runtime_spawn_scrubs_parent_env_without_env_cap(self, monkeypatch):
        import geno._runtime_support as runtime_support

        monkeypatch.setenv("GENO_PROCESS_ENV_PROBE", "secret-from-parent")
        monkeypatch.setattr(runtime_support, "_GENO_CAPS", {"process"})

        result = runtime_support.spawn(
            sys.executable,
            [
                "-c",
                (
                    "import os; "
                    "print(os.environ.get('GENO_PROCESS_ENV_PROBE', 'missing'))"
                ),
            ],
        )

        assert isinstance(result, runtime_support.Ok)
        assert result.value.stdout.strip() == "missing"

    def test_runtime_spawn_inherits_parent_env_with_env_cap(self, monkeypatch):
        import geno._runtime_support as runtime_support

        monkeypatch.setenv("GENO_PROCESS_ENV_PROBE", "secret-from-parent")
        monkeypatch.setattr(runtime_support, "_GENO_CAPS", {"env", "process"})

        result = runtime_support.spawn(
            sys.executable,
            [
                "-c",
                (
                    "import os; "
                    "print(os.environ.get('GENO_PROCESS_ENV_PROBE', 'missing'))"
                ),
            ],
        )

        assert isinstance(result, runtime_support.Ok)
        assert result.value.stdout.strip() == "secret-from-parent"

    def test_unsafe_spawn_scrubs_parent_env_without_env_cap(self, monkeypatch):
        from geno._serve import install_process_callbacks
        from geno.interpreter import Interpreter

        monkeypatch.setenv("GENO_PROCESS_ENV_PROBE", "secret-from-parent")
        interpreter = Interpreter()
        install_process_callbacks(interpreter)
        spawn = interpreter.global_env.bindings["spawn"].func

        result = spawn(
            sys.executable,
            [
                "-c",
                (
                    "import os; "
                    "print(os.environ.get('GENO_PROCESS_ENV_PROBE', 'missing'))"
                ),
            ],
        )

        assert result.constructor == "Ok"
        assert result.fields["value"].fields["stdout"].strip() == "missing"

    def test_unsafe_spawn_inherits_parent_env_with_env_cap(self, monkeypatch):
        from geno._serve import install_process_callbacks
        from geno.interpreter import Interpreter

        monkeypatch.setenv("GENO_PROCESS_ENV_PROBE", "secret-from-parent")
        interpreter = Interpreter()
        install_process_callbacks(interpreter, inherit_env=True)
        spawn = interpreter.global_env.bindings["spawn"].func

        result = spawn(
            sys.executable,
            [
                "-c",
                (
                    "import os; "
                    "print(os.environ.get('GENO_PROCESS_ENV_PROBE', 'missing'))"
                ),
            ],
        )

        assert result.constructor == "Ok"
        assert result.fields["value"].fields["stdout"].strip() == "secret-from-parent"

    def test_js_process_exec_uses_capability_aware_env(self):
        js_code = compile_to_js(EXEC_SOURCE, typecheck=False)

        assert "function _processEnv()" in js_code
        assert "env: _processEnv()" in js_code
        assert "spawnSync" in js_code
        assert "execSync(command" not in js_code


class TestUnsafeProcessCallbacks:
    def test_unsafe_exec_invalid_command_returns_err(self):
        from geno._serve import install_process_callbacks
        from geno.interpreter import Interpreter

        interpreter = Interpreter()
        install_process_callbacks(interpreter)
        exec_fn = interpreter.global_env.bindings["exec"].func

        result = exec_fn(123)

        assert isinstance(result, ConstructorValue)
        assert result.constructor == "Err"
        assert "command must be String" in result.fields["error"]

    def test_unsafe_exec_with_input_invalid_command_returns_err(self):
        from geno._serve import install_process_callbacks
        from geno.interpreter import Interpreter

        interpreter = Interpreter()
        install_process_callbacks(interpreter)
        exec_with_input = interpreter.global_env.bindings["exec_with_input"].func

        result = exec_with_input(123, "stdin")

        assert isinstance(result, ConstructorValue)
        assert result.constructor == "Err"
        assert "command must be String" in result.fields["error"]
