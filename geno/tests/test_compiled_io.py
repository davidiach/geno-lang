"""
Tests for compiled-output I/O builtins (#133)
=============================================

Verifies that fs and http builtins work in compiled Python and JS output,
and that capability gating is enforced.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.compiler import compile_to_python
from geno.js_compiler import compile_to_js
from geno.tests._script_runner import run_python_code


def _run_compiled_python(
    py_code: str, *args: str, cwd: str | Path | None = None
) -> subprocess.CompletedProcess[str]:
    return run_python_code(
        py_code,
        python_executable=sys.executable,
        args=args,
        timeout=10,
        cwd=cwd,
    )


# =========================================================================
# Python compiled output — fs builtins
# =========================================================================


class TestPythonFsBuiltins:
    def test_fs_read_text_in_compiled(self, tmp_path):
        """Compiled Python output can read a file with --cap fs."""
        (tmp_path / "hello.txt").write_text("hello world")

        source = """
func main() -> String
  return fs_read_text("hello.txt")
end func
"""

        py_code = compile_to_python(source)
        py_code += "\nprint(main())\n"

        result = _run_compiled_python(py_code, "--cap", "fs", cwd=tmp_path)
        assert result.returncode == 0
        assert "hello world" in result.stdout

    def test_fs_write_text_in_compiled(self, tmp_path):
        """Compiled Python output can write a file with --cap fs."""
        source = """
func main() -> Int
  fs_write_text("output.txt", "written by geno")
  return 0
end func
"""

        py_code = compile_to_python(source)
        py_code += "\nmain()\n"

        result = _run_compiled_python(py_code, "--cap", "fs", cwd=tmp_path)
        out_file = tmp_path / "output.txt"
        assert result.returncode == 0
        assert out_file.read_text() == "written by geno"

    def test_fs_exists_in_compiled(self, tmp_path):
        """Compiled Python output can check file existence."""
        (tmp_path / "exists.txt").write_text("hi")

        source = """
func main() -> Bool
  return fs_exists("exists.txt")
end func
"""

        py_code = compile_to_python(source)
        py_code += "\nprint(main())\n"

        result = _run_compiled_python(py_code, "--cap", "fs", cwd=tmp_path)
        assert result.returncode == 0
        assert "True" in result.stdout

    def test_fs_list_dir_in_compiled(self, tmp_path):
        """Compiled Python output can list directory entries."""
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")

        source = """
func main() -> String
  let result: Result[List[String], String] = fs_list_dir(".")
  match result with
  | Ok(entries) -> return join(entries, ",")
  | Err(msg) -> return msg
  end match
end func
"""

        py_code = compile_to_python(source)
        py_code += "\nprint(main())\n"

        result = _run_compiled_python(py_code, "--cap", "fs", cwd=tmp_path)
        assert result.returncode == 0
        assert "a.txt" in result.stdout
        assert "b.txt" in result.stdout


# =========================================================================
# Capability gating
# =========================================================================


class TestCapabilityGating:
    def test_fs_denied_without_cap(self, tmp_path):
        """fs builtins raise capability error when --cap fs not granted."""
        source = """
func main() -> String
  return fs_read_text("/tmp/anything.txt")
end func
"""
        py_code = compile_to_python(source)

        # Pass --cap print (not fs) to trigger the capability check
        result = _run_compiled_python(py_code, "--cap", "print")
        assert result.returncode != 0
        assert "Capability denied" in result.stderr
        assert "fs" in result.stderr

    def test_http_denied_without_cap(self):
        """http builtins raise capability error when --cap http not granted."""
        source = """
func main() -> String
  return http_fetch("http://example.com")
end func
"""
        py_code = compile_to_python(source)

        result = _run_compiled_python(py_code, "--cap", "print")
        assert result.returncode != 0
        assert "Capability denied" in result.stderr
        assert "http" in result.stderr

    def test_no_cap_flags_denies_all(self, tmp_path):
        """Without any --cap flags, capability-gated builtins are denied."""
        (tmp_path / "test.txt").write_text("trusted")

        source = """
func main() -> String
  return fs_read_text("test.txt")
end func
"""

        py_code = compile_to_python(source)
        py_code += "\nprint(main())\n"

        # No --cap flags — should be denied
        result = _run_compiled_python(py_code, cwd=tmp_path)
        assert result.returncode != 0
        assert "Capability denied" in result.stderr


# =========================================================================
# JS compiled output — fs builtins (Node.js)
# =========================================================================


class TestJsFsBuiltins:
    @pytest.fixture(autouse=True)
    def _check_node(self):
        """Skip if Node.js is not available."""
        result = subprocess.run(["node", "--version"], capture_output=True, text=True)
        if result.returncode != 0:
            pytest.skip("Node.js not available")

    def test_js_fs_read_text(self, tmp_path):
        """JS compiled output can read a file in Node.js with --cap fs."""
        test_file = tmp_path / "hello.txt"
        test_file.write_text("hello from node")

        source = """
func main() -> String
  return fs_read_text("{path}")
end func
""".replace("{path}", str(test_file).replace("\\", "\\\\"))

        js_code = compile_to_js(source)
        js_file = tmp_path / "app.js"
        js_file.write_text(js_code)
        result = subprocess.run(
            ["node", str(js_file), "--cap", "fs"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "hello from node" in result.stdout

    def test_js_fs_read_text_normalizes_newlines(self, tmp_path):
        """JS fs_read_text matches Python text-mode newline normalization."""
        test_file = tmp_path / "lines.txt"
        test_file.write_bytes(b"one\r\ntwo\rthree\n")

        source = """
func main() -> String
  return fs_read_text("{path}")
end func
""".replace("{path}", str(test_file).replace("\\", "\\\\"))

        js_code = compile_to_js(source)
        js_file = tmp_path / "app.js"
        js_file.write_text(js_code)
        result = subprocess.run(
            ["node", str(js_file), "--cap", "fs"],
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert b"one\ntwo\nthree\n" in result.stdout
        assert b"\r" not in result.stdout

    def test_js_fs_write_text(self, tmp_path):
        """JS compiled output can write a file in Node.js with --cap fs."""
        out_file = tmp_path / "out.txt"

        source = """
func main() -> Int
  fs_write_text("{path}", "written by geno js")
  return 0
end func
""".replace("{path}", str(out_file).replace("\\", "\\\\"))

        js_code = compile_to_js(source)
        js_file = tmp_path / "app.js"
        js_file.write_text(js_code)
        result = subprocess.run(
            ["node", str(js_file), "--cap", "fs"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert out_file.read_text() == "written by geno js"

    def test_js_fs_exists(self, tmp_path):
        """JS compiled output can check file existence with --cap fs."""
        (tmp_path / "exists.txt").write_text("hi")

        source = """
func main() -> Bool
  return fs_exists("{path}")
end func
""".replace("{path}", str(tmp_path / "exists.txt").replace("\\", "\\\\"))

        js_code = compile_to_js(source)
        js_file = tmp_path / "app.js"
        js_file.write_text(js_code)
        result = subprocess.run(
            ["node", str(js_file), "--cap", "fs"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "true" in result.stdout.lower()

    def test_js_fs_list_dir_orders_unicode_names_like_python(self, tmp_path):
        """JS fs_list_dir uses Python-compatible Unicode ordering."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        (entries_dir / chr(128512)).write_text("smile")
        (entries_dir / chr(57344)).write_text("private")

        source = """
func main() -> String
  let result: Result[List[String], String] = fs_list_dir("entries")
  match result with
  | Ok(entries) -> return to_string(char_code(entries[0])) + "," + to_string(char_code(entries[1]))
  | Err(msg) -> return msg
  end match
end func
"""

        py_code = compile_to_python(source)
        py_result = _run_compiled_python(py_code, "--cap", "fs", cwd=tmp_path)
        assert py_result.returncode == 0
        assert py_result.stdout == "57344,128512\n"

        js_code = compile_to_js(source)
        js_file = tmp_path / "app.js"
        js_file.write_text(js_code)
        js_result = subprocess.run(
            ["node", str(js_file), "--cap", "fs"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=tmp_path,
        )
        assert js_result.returncode == 0
        assert js_result.stdout == py_result.stdout

    def test_js_fs_denied_with_cap(self, tmp_path):
        """JS fs builtins raise capability error when --cap fs not granted."""
        source = """
func main() -> String
  return fs_read_text("/tmp/anything.txt")
end func
"""
        js_code = compile_to_js(source)

        # Write to file to avoid node interpreting --cap as its own flag
        js_file = tmp_path / "test.js"
        js_file.write_text(js_code)

        result = subprocess.run(
            ["node", str(js_file), "--cap", "print"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0
        assert "Capability denied" in result.stderr


# =========================================================================
# JS compiled output — http builtins (Node.js)
# =========================================================================


class TestJsHttpBuiltins:
    @pytest.fixture(autouse=True)
    def _check_node(self):
        """Skip if Node.js is not available."""
        result = subprocess.run(["node", "--version"], capture_output=True, text=True)
        if result.returncode != 0:
            pytest.skip("Node.js not available")

    def test_js_http_fetch_rejects_data_scheme(self, tmp_path):
        """JS compiled output rejects non-http schemes for http_fetch."""
        source = """
func main() -> String
  return http_fetch("data:text/plain,hello")
end func
"""
        js_code = compile_to_js(source)
        js_file = tmp_path / "app.js"
        js_file.write_text(js_code)

        result = subprocess.run(
            ["node", str(js_file), "--cap", "http"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert "scheme 'data' is not allowed" in result.stderr

    def test_js_http_fetch_requires_process_cap_for_node_sync_bridge(self, tmp_path):
        """Node JS output does not hide child-process use behind http alone."""
        source = """
func main() -> String
  return http_fetch("http://example.test")
end func
"""
        js_code = compile_to_js(source)
        js_file = tmp_path / "app.js"
        js_file.write_text(js_code)

        result = subprocess.run(
            ["node", str(js_file), "--cap", "http"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert "requires '--cap process'" in result.stderr

    def test_js_http_request_rejects_data_scheme_as_err(self, tmp_path):
        """JS compiled output preserves http_request's Result contract."""
        source = """
func main() -> String
  let result: Result[HttpResponse, String] = http_request(
    method: "GET",
    url: "data:text/plain,hello",
    headers: [],
    body: ""
  )
  match result with
  | Ok(_) -> return "unexpected"
  | Err(msg) -> return msg
  end match
end func
"""
        js_code = compile_to_js(source)
        js_file = tmp_path / "app.js"
        js_file.write_text(js_code)

        result = subprocess.run(
            ["node", str(js_file), "--cap", "http"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0
        assert "scheme 'data' is not allowed" in result.stdout


# =========================================================================
# Python compiled output — http builtins are present
# =========================================================================


class TestPythonHttpBuiltins:
    def test_http_functions_in_compiled_output(self):
        """Compiled Python output contains http builtin functions."""
        source = """
func main() -> String
  return http_fetch("http://example.com")
end func
"""
        py_code = compile_to_python(source)
        assert "def http_fetch" in py_code
        assert "def http_post" in py_code
        assert "def http_request" in py_code

    def test_http_request_invalid_scheme_returns_err(self):
        """Compiled Python preserves http_request's Result contract."""
        source = """
func main() -> String
  let result: Result[HttpResponse, String] = http_request(
    method: "GET",
    url: "file:///etc/passwd",
    headers: [],
    body: ""
  )
  match result with
  | Ok(_) -> return "unexpected"
  | Err(msg) -> return msg
  end match
end func
"""
        py_code = compile_to_python(source)
        py_code += "\nprint(main())\n"

        result = _run_compiled_python(py_code, "--cap", "http")

        assert result.returncode == 0
        assert "scheme 'file' is not allowed" in result.stdout
