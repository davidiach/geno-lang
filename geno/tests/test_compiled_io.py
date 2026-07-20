"""
Tests for compiled-output I/O builtins (#133)
=============================================

Verifies that fs and http builtins work in compiled Python and JS output,
and that capability gating is enforced.
"""

import contextlib
import http.server
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.compiler import compile_to_python
from geno.js_compiler import compile_to_js
from geno.tests._script_runner import run_node_code, run_python_code


def _run_compiled_python(
    py_code: str, *args: str, cwd: str | Path | None = None
) -> subprocess.CompletedProcess[str]:
    result = run_python_code(
        py_code,
        python_executable=sys.executable,
        args=args,
        timeout=10,
        cwd=cwd,
    )
    return cast(subprocess.CompletedProcess[str], result)


@contextlib.contextmanager
def _compiled_http_server() -> Iterator[str]:
    class TargetHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = self.headers.get("X-Api-Key", "").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            pass

    target_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
    target_thread = threading.Thread(target=target_server.serve_forever, daemon=True)
    target_thread.start()
    redirect_target = (
        f"http://127.0.0.1:{target_server.server_address[1]}/redirect-target"
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        def _respond(
            self,
            status: int,
            body: str,
            extra_headers: list[tuple[str, str]] | None = None,
        ) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("X-Reply", "present")
            for key, value in extra_headers or []:
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:
            if self.path == "/drop":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if self.path == "/large":
                self._respond(200, "x" * (1024 * 1024 + 1))
                return
            if self.path == "/status":
                self._respond(404, "not-found")
                return
            if self.path == "/headers":
                self._respond(
                    200,
                    "headers",
                    [("X-Dupe", "one"), ("X-Dupe", "two")],
                )
                return
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", redirect_target)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/loop":
                self.send_response(302)
                self.send_header("Location", "/loop")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/framing-get":
                self._respond(204 if "Content-Length" not in self.headers else 409, "")
                return
            if self.path == "/head-target":
                self._respond(409, "redirected HEAD became GET")
                return

            self._respond(200, "fetch-ok")

        def do_HEAD(self) -> None:
            if self.path == "/head-redirect":
                self.send_response(302)
                self.send_header("Location", "/head-target")
            elif self.path == "/head-target":
                self.send_response(204)
            elif self.path == "/framing-head":
                self.send_response(204 if "Content-Length" not in self.headers else 409)
            else:
                self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:
            size = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(size).decode("utf-8")
            if self.path == "/request":
                self._respond(201, f"{self.headers.get('X-Geno')}:{body}")
            elif self.path == "/request-size":
                self._respond(200, str(len(body)))
            elif self.path in {"/redirect-307", "/redirect-308"}:
                self.send_response(307 if self.path.endswith("307") else 308)
                self.send_header("Location", "/redirect-post-target")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif self.path == "/redirect-post-target":
                self._respond(200, f"POST:{body}")
            elif self.path == "/status":
                self._respond(404, "not-found-post")
            else:
                self._respond(200, f"{self.headers.get('Content-Type')}:{body}")

        def log_message(self, fmt: str, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        target_server.shutdown()
        target_server.server_close()
        target_thread.join(timeout=2)


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

    @pytest.mark.parametrize("esm", [False, True], ids=["commonjs", "esm"])
    def test_js_http_builtins_use_live_node_bridge(
        self, tmp_path: Path, esm: bool
    ) -> None:
        """Compiled Node HTTP preserves the portable request/response contract."""
        with _compiled_http_server() as base_url:
            source = f"""
@untested("response header inspection")
func header_values(headers: List[(String, String)], name: String) -> String
  var values: List[String] = []
  for header: (String, String) in headers do
    let (header_name, header_value): (String, String) = header
    if header_name == name then
      values = concat(values, [header_value])
    end if
  end for
  return join(values, "|")
end func

func main() -> String
  let fetched: String = http_fetch("{base_url}/fetch")
  let large: String = http_fetch("{base_url}/large")
  if length(large) != 1048577 then
    return "wrong-large-body"
  end if
  let posted: String = http_post("{base_url}/post", "post-body")
  let get_request: Result[HttpResponse, String] = http_request(
    method: "GET",
    url: "{base_url}/fetch",
    headers: [],
    body: ""
  )
  match get_request with
  | Err(message) -> return "get-error:" + message
  | Ok(response) ->
    if response.body != "fetch-ok" then
      return "wrong-get-body"
    end if
  end match

  let requested: Result[HttpResponse, String] = http_request(
    method: "POST",
    url: "{base_url}/request",
    headers: [("X-Geno", "request-header")],
    body: "request-body"
  )
  match requested with
  | Err(message) -> return "error:" + message
  | Ok(response) ->
    match response with
    | HttpResponse(status, response_body, response_headers) ->
      if status != 201 then
        return "wrong-status"
      end if
      if length(response_headers) == 0 then
        return "missing-headers"
      end if

      let large_body: String = string_repeat(text: "s", count: 2097152)
      let large_request: Result[HttpResponse, String] = http_request(
        method: "POST",
        url: "{base_url}/request-size",
        headers: [],
        body: large_body
      )
      match large_request with
      | Err(message) -> return "large-request-error:" + message
      | Ok(large_response) ->
        if large_response.body != "2097152" then
          return "wrong-large-request-size"
        end if
      end match

      let redirected: Result[HttpResponse, String] = http_request(
        method: "GET",
        url: "{base_url}/redirect",
        headers: [("X-Api-Key", "secret")],
        body: ""
      )
      match redirected with
      | Err(message) -> return "redirect-error:" + message
      | Ok(redirect_response) ->
        if redirect_response.body != "" then
          return "redirect-leaked-header"
        end if
      end match

      let status_result: Result[HttpResponse, String] = http_request(
        method: "GET",
        url: "{base_url}/status",
        headers: [],
        body: ""
      )
      match status_result with
      | Err(message) -> return "status-error:" + message
      | Ok(status_response) ->
        if status_response.status != 404 or status_response.body != "not-found" then
          return "wrong-non-2xx"
        end if
      end match

      let header_result: Result[HttpResponse, String] = http_request(
        method: "GET",
        url: "{base_url}/headers",
        headers: [],
        body: ""
      )
      match header_result with
      | Err(message) -> return "header-error:" + message
      | Ok(header_response) ->
        if header_values(header_response.headers, "X-Dupe") != "one|two" then
          return "wrong-duplicate-headers"
        end if
      end match

      let failed: Result[HttpResponse, String] = http_request(
        method: "GET",
        url: "{base_url}/drop",
        headers: [],
        body: ""
      )
      match failed with
      | Ok(_) -> return "unexpected-transport-success"
      | Err(_) -> return fetched + "|" + posted + "|" + response_body
      end match
    end match
  end match
end func
"""
            js_file = tmp_path / ("app.mjs" if esm else "app.js")
            js_code = compile_to_js(source, esm=esm)
            assert isinstance(js_code, str)
            js_file.write_text(js_code)

            env = {**os.environ, "GENO_HTTP_ALLOW_PRIVATE": "1"}
            result = subprocess.run(
                ["node", str(js_file), "--cap", "http"],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )

        assert result.returncode == 0, result.stderr
        assert result.stdout == (
            "fetch-ok|application/json:post-body|request-header:request-body\n"
        )
        assert "_GENO_JSON is not defined" not in result.stderr
        assert "request-body" not in result.stderr
        assert result.stderr == ""

    def test_js_http_rejects_private_targets_by_default(self, tmp_path: Path) -> None:
        with _compiled_http_server() as base_url:
            source = f"""
func main() -> String
  return http_fetch("{base_url}/fetch")
end func
"""
            js_file = tmp_path / "private.js"
            js_code = compile_to_js(source)
            assert isinstance(js_code, str)
            js_file.write_text(js_code)
            env = dict(os.environ)
            env.pop("GENO_HTTP_ALLOW_PRIVATE", None)
            result = subprocess.run(
                ["node", str(js_file), "--cap", "http"],
                capture_output=True,
                text=True,
                timeout=15,
                env=env,
            )

        assert result.returncode != 0
        assert "non-public network targets are not allowed" in result.stderr

    def test_js_http_stream_enforces_collection_limit(self, tmp_path: Path) -> None:
        with _compiled_http_server() as base_url:
            source = f"""
func main() -> String
  return http_fetch("{base_url}/large")
end func
"""
            js_file = tmp_path / "limited.js"
            js_code = compile_to_js(source)
            assert isinstance(js_code, str)
            js_file.write_text(
                "globalThis.__GENO_MAX_COLLECTION_SIZE = 1024;\n" + js_code
            )
            env = {**os.environ, "GENO_HTTP_ALLOW_PRIVATE": "1"}
            result = subprocess.run(
                ["node", str(js_file), "--cap", "http"],
                capture_output=True,
                text=True,
                timeout=15,
                env=env,
            )

        assert result.returncode != 0
        assert "String size exceeds limit" in result.stderr

    def test_http_request_limit_is_fatal_across_compiled_backends(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _compiled_http_server() as base_url:
            monkeypatch.setenv("GENO_HTTP_ALLOW_PRIVATE", "1")
            source = f"""
func main() -> String
  let result: Result[HttpResponse, String] = http_request(
    method: "GET",
    url: "{base_url}/large",
    headers: [],
    body: ""
  )
  match result with
  | Err(message) -> return "transport:" + message
  | Ok(response) -> return response.body
  end match
end func
"""
            python_code = "_GENO_MAX_COLLECTION_SIZE = 1024\n" + compile_to_python(
                source
            )
            node_code = "globalThis.__GENO_MAX_COLLECTION_SIZE = 1024;\n" + cast(
                str, compile_to_js(source)
            )
            python_result = run_python_code(
                python_code,
                python_executable=sys.executable,
                args=("--cap", "http"),
                timeout=15,
            )
            node_result = run_node_code(
                node_code,
                args=("--cap", "http"),
                timeout=15,
            )

        for result in (python_result, node_result):
            assert result.returncode != 0
            assert "String size exceeds limit" in result.stderr
            assert "transport:" not in result.stdout

    def test_python_and_node_preserve_non_2xx_and_duplicate_headers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _compiled_http_server() as base_url:
            monkeypatch.setenv("GENO_HTTP_ALLOW_PRIVATE", "1")
            source = f"""
@untested("response header inspection")
func header_values(headers: List[(String, String)], name: String) -> String
  var values: List[String] = []
  for header: (String, String) in headers do
    let (header_name, header_value): (String, String) = header
    if header_name == name then
      values = concat(values, [header_value])
    end if
  end for
  return join(values, "|")
end func

func main() -> String
  let fetched_status: String = http_fetch("{base_url}/status")
  let posted_status: String = http_post("{base_url}/status", "request")
  let status_result: Result[HttpResponse, String] = http_request(
    method: "GET",
    url: "{base_url}/status",
    headers: [],
    body: ""
  )
  match status_result with
  | Err(message) -> return "status-error:" + message
  | Ok(status_response) ->
    let header_result: Result[HttpResponse, String] = http_request(
      method: "GET",
      url: "{base_url}/headers",
      headers: [],
      body: ""
    )
    match header_result with
    | Err(message) -> return "header-error:" + message
    | Ok(header_response) ->
      let loop_result: Result[HttpResponse, String] = http_request(
        method: "GET",
        url: "{base_url}/loop",
        headers: [],
        body: ""
      )
      match loop_result with
      | Ok(_) -> return "unexpected-redirect-loop-success"
      | Err(_) ->
        return fetched_status + ":" + posted_status
          + ":" + to_string(status_response.status) + ":" + status_response.body
          + ":" + header_values(header_response.headers, "X-Dupe")
      end match
    end match
  end match
end func
"""
            python_result = run_python_code(
                compile_to_python(source),
                python_executable=sys.executable,
                args=("--cap", "http"),
                timeout=15,
            )
            node_result = run_node_code(
                cast(str, compile_to_js(source)),
                args=("--cap", "http"),
                timeout=15,
            )

            from geno._serve import install_http_callbacks
            from geno.interpreter import Interpreter
            from geno.values import ConstructorValue

            interpreter = Interpreter()
            install_http_callbacks(interpreter, allow_private_networks=True)
            callbacks = interpreter.global_env.bindings
            assert callbacks["http_fetch"].func(f"{base_url}/status") == "not-found"
            assert (
                callbacks["http_post"].func(f"{base_url}/status", "request")
                == "not-found-post"
            )
            hosted_result = callbacks["http_request"].func(
                "GET", f"{base_url}/status", [], ""
            )
            assert isinstance(hosted_result, ConstructorValue)
            assert hosted_result.constructor == "Ok"
            hosted_response = hosted_result.fields["value"]
            assert isinstance(hosted_response, ConstructorValue)
            assert hosted_response.constructor == "HttpResponse"
            assert hosted_response.fields["status"] == 404
            assert hosted_response.fields["body"] == "not-found"
            hosted_headers = callbacks["http_request"].func(
                "GET", f"{base_url}/headers", [], ""
            )
            assert hosted_headers.constructor == "Ok"
            hosted_header_pairs = hosted_headers.fields["value"].fields["headers"]
            assert [pair for pair in hosted_header_pairs if pair[0] == "X-Dupe"] == [
                ("X-Dupe", "one"),
                ("X-Dupe", "two"),
            ]
            hosted_loop = callbacks["http_request"].func(
                "GET", f"{base_url}/loop", [], ""
            )
            assert hosted_loop.constructor == "Err"
            assert "too many redirects" in hosted_loop.fields["error"]

        expected = "not-found:not-found-post:404:not-found:one|two\n"
        assert python_result.returncode == 0, python_result.stderr
        assert node_result.returncode == 0, node_result.stderr
        assert python_result.stdout == expected
        assert node_result.stdout == expected

    def test_python_node_and_hosted_redirect_method_and_framing_parity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _compiled_http_server() as base_url:
            monkeypatch.setenv("GENO_HTTP_ALLOW_PRIVATE", "1")
            source = f"""
@untested("HTTP parity probe")
func response_summary(method: String, url: String, body: String) -> String
  let result: Result[HttpResponse, String] = http_request(
    method: method,
    url: url,
    headers: [],
    body: body
  )
  match result with
  | Err(message) -> return "error:" + message
  | Ok(response) -> return to_string(response.status) + ":" + response.body
  end match
end func

func main() -> String
  return response_summary(method: "GET", url: "{base_url}/framing-get", body: "")
    + "|" + response_summary(method: "HEAD", url: "{base_url}/framing-head", body: "")
    + "|" + response_summary(method: "HEAD", url: "{base_url}/head-redirect", body: "")
    + "|" + response_summary(method: "POST", url: "{base_url}/redirect-307", body: "body-307")
    + "|" + response_summary(method: "POST", url: "{base_url}/redirect-308", body: "body-308")
end func
"""
            python_result = run_python_code(
                compile_to_python(source),
                python_executable=sys.executable,
                args=("--cap", "http"),
                timeout=15,
            )
            node_result = run_node_code(
                cast(str, compile_to_js(source)),
                args=("--cap", "http"),
                timeout=15,
            )

            from geno._serve import install_http_callbacks
            from geno.interpreter import Interpreter

            interpreter = Interpreter()
            install_http_callbacks(interpreter, allow_private_networks=True)
            request = interpreter.global_env.bindings["http_request"].func

            def hosted_summary(method: str, path: str, body: str) -> str:
                result = request(method, f"{base_url}{path}", [], body)
                assert result.constructor == "Ok"
                response = result.fields["value"]
                return f"{response.fields['status']}:{response.fields['body']}"

            hosted_result = "|".join(
                [
                    hosted_summary("GET", "/framing-get", ""),
                    hosted_summary("HEAD", "/framing-head", ""),
                    hosted_summary("HEAD", "/head-redirect", ""),
                    hosted_summary("POST", "/redirect-307", "body-307"),
                    hosted_summary("POST", "/redirect-308", "body-308"),
                ]
            )

        expected = "204:|204:|204:|200:POST:body-307|200:POST:body-308"
        assert python_result.returncode == 0, python_result.stderr
        assert node_result.returncode == 0, node_result.stderr
        assert python_result.stdout == expected + "\n"
        assert node_result.stdout == expected + "\n"
        assert hosted_result == expected


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
