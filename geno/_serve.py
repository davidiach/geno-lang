"""
Runtime callback installers for interpreter capabilities.

These functions install real I/O, HTTP, process, and server builtins
onto an interpreter instance when the corresponding ``--cap`` flags
are enabled at the CLI.

Extracted from ``__main__.py`` to keep the CLI module focused on
argument parsing and dispatch.
"""

import logging
import re
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)


def _checked_callback_result(interpreter, value: Any) -> Any:
    interpreter._check_collection_limits([value], None)
    return value


def _env_truthy(name: str) -> bool:
    import os

    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _roots_from_env(name: str):
    import os

    raw = os.environ.get(name)
    if not raw:
        return None
    return [item for item in raw.split(os.pathsep) if item]


def _resolve_fs_roots(roots: Iterable[str] | None) -> list[str]:
    import os

    raw_roots = list(roots) if roots is not None else _roots_from_env("GENO_FS_ROOTS")
    if not raw_roots:
        raw_roots = [os.getcwd()]
    return [os.path.realpath(os.fspath(root)) for root in raw_roots]


def _is_under_root(path: str, root: str) -> bool:
    import os

    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:
        return False


def _resolve_scoped_path(
    path,
    fn_name: str,
    roots: list[str],
    *,
    allow_absolute_paths: bool,
) -> str:
    import os

    if not isinstance(path, str):
        raise RuntimeError(f"{fn_name}: path must be String")
    if os.path.isabs(path):
        if not allow_absolute_paths:
            raise RuntimeError(f"{fn_name}: absolute paths are not allowed")
        resolved = os.path.realpath(path)
        if any(_is_under_root(resolved, root) for root in roots):
            return resolved
        raise RuntimeError(f"{fn_name}: path escapes configured filesystem roots")

    for root in roots:
        resolved = os.path.realpath(os.path.join(root, path))
        if _is_under_root(resolved, root):
            return resolved
    raise RuntimeError(f"{fn_name}: path escapes configured filesystem roots")


def _read_limited_utf8_stream(interpreter, reader, fn_name: str) -> str:
    import codecs

    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    parts: list[str] = []
    total = 0
    while True:
        chunk = reader.read(8192)
        if not chunk:
            break
        text = decoder.decode(chunk)
        if not text:
            continue
        total += len(text)
        interpreter._check_collection_size("String", total, None)
        parts.append(text)
    tail = decoder.decode(b"", final=True)
    if tail:
        total += len(tail)
        interpreter._check_collection_size("String", total, None)
        parts.append(tail)
    return "".join(parts)


def _read_limited_text_stream(interpreter, reader, fn_name: str) -> str:
    parts: list[str] = []
    total = 0
    while True:
        text = reader.read(8192)
        if not text:
            break
        total += len(text)
        interpreter._check_collection_size("String", total, None)
        parts.append(text)
    return "".join(parts)


def _validate_http_target(
    url: str,
    fn_name: str,
    *,
    allow_private_networks: bool,
) -> None:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise RuntimeError(
            f"{fn_name}: scheme '{scheme}' is not allowed, only http and https"
        )
    if not parsed.hostname:
        raise RuntimeError(f"{fn_name}: URL must include a hostname")
    if allow_private_networks:
        return

    _resolve_validated_http_addresses(
        parsed.hostname,
        parsed.port or (443 if scheme == "https" else 80),
        fn_name,
        allow_private_networks=allow_private_networks,
    )


def _validate_http_address(
    host: str,
    fn_name: str,
    *,
    allow_private_networks: bool,
) -> None:
    if allow_private_networks:
        return
    import ipaddress

    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise RuntimeError(
            f"{fn_name}: cannot validate resolved host {host!r}"
        ) from exc
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise RuntimeError(
            f"{fn_name}: private, local, or reserved network targets are not allowed"
        )


def _resolve_validated_http_addresses(
    hostname: str,
    port: int,
    fn_name: str,
    *,
    allow_private_networks: bool,
) -> list[Any]:
    import socket

    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise RuntimeError(f"{fn_name}: cannot resolve host {hostname!r}") from exc
    for info in infos:
        resolved_host = info[4][0]
        if not isinstance(resolved_host, str):
            raise RuntimeError(f"{fn_name}: cannot validate resolved host")
        _validate_http_address(
            resolved_host,
            fn_name,
            allow_private_networks=allow_private_networks,
        )
    return infos


def _create_validated_http_connection(
    hostname: str,
    port: int,
    timeout: Any,
    source_address: Any,
    fn_name: str,
    *,
    allow_private_networks: bool,
) -> Any:
    import socket

    infos = _resolve_validated_http_addresses(
        hostname,
        port,
        fn_name,
        allow_private_networks=allow_private_networks,
    )
    last_error = None
    global_default_timeout = getattr(socket, "_GLOBAL_DEFAULT_TIMEOUT", object())
    for family, socktype, proto, _canonname, sockaddr in infos:
        sock = socket.socket(family, socktype, proto)
        try:
            if timeout is not global_default_timeout:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
    if last_error is not None:
        raise last_error
    raise OSError(f"{fn_name}: cannot resolve host {hostname!r}")


def _read_limited_temp_text(interpreter, handle, fn_name: str) -> str:
    handle.seek(0)
    return _read_limited_utf8_stream(interpreter, handle, fn_name)


def _resolve_executable_allowlist(allowed_executables: Iterable[str] | None):
    import os

    raw = (
        list(allowed_executables)
        if allowed_executables is not None
        else _roots_from_env("GENO_PROCESS_EXECUTABLES")
    )
    if raw is None:
        return None
    return {os.path.realpath(os.fspath(path)) for path in raw}


def _validate_process_argv(
    argv,
    fn_name: str,
    *,
    process_env,
    allow_path_search: bool,
    allowed_executables: set[str] | None,
):
    import os
    import shutil

    if not argv:
        return None, f"{fn_name}: command must not be empty"
    program = argv[0]
    if not isinstance(program, str):
        return None, f"{fn_name}: program must be String"
    if not allow_path_search and not os.path.isabs(program):
        return None, f"{fn_name}: executable must be an absolute path"

    resolved = program
    if os.path.isabs(program):
        resolved = os.path.realpath(program)
    elif allow_path_search:
        path_env = None if process_env is None else process_env.get("PATH")
        found = shutil.which(program, path=path_env)
        if found is None:
            return None, f"{fn_name}: executable not found: {program}"
        resolved = os.path.realpath(found)

    if allowed_executables is not None and resolved not in allowed_executables:
        return None, f"{fn_name}: executable is not in the configured allowlist"
    return [resolved, *argv[1:]], None


def install_fs_callbacks(
    interpreter,
    *,
    roots: Iterable[str] | None = None,
    allow_absolute_paths: bool | None = None,
    allow_write: bool | None = None,
):
    """Install real file I/O callbacks on an interpreter instance."""
    import os

    from .values import BuiltinFunction, ConstructorValue

    fs_roots = _resolve_fs_roots(roots)
    absolute_ok = (
        _env_truthy("GENO_FS_ALLOW_ABSOLUTE")
        if allow_absolute_paths is None
        else allow_absolute_paths
    )
    writes_ok = (
        not _env_truthy("GENO_FS_READ_ONLY") if allow_write is None else allow_write
    )

    def _fs_read_text(path):
        path = _resolve_scoped_path(
            path, "fs_read_text", fs_roots, allow_absolute_paths=absolute_ok
        )
        with open(path, encoding="utf-8") as f:
            result = _read_limited_text_stream(interpreter, f, "fs_read_text")
        return _checked_callback_result(interpreter, result)

    def _fs_write_text(path, content):
        if not writes_ok:
            raise RuntimeError("fs_write_text: filesystem writes are not allowed")
        path = _resolve_scoped_path(
            path, "fs_write_text", fs_roots, allow_absolute_paths=absolute_ok
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return None

    def _fs_list_dir(path):
        try:
            path = _resolve_scoped_path(
                path, "fs_list_dir", fs_roots, allow_absolute_paths=absolute_ok
            )
            entries = sorted(os.listdir(path))
            result = ConstructorValue("Ok", {"value": entries})
            return _checked_callback_result(interpreter, result)
        except (OSError, RuntimeError) as e:
            return ConstructorValue("Err", {"error": str(e)})

    def _fs_exists(path):
        path = _resolve_scoped_path(
            path, "fs_exists", fs_roots, allow_absolute_paths=absolute_ok
        )
        return os.path.exists(path)

    interpreter.global_env.bind(
        "fs_read_text", BuiltinFunction("fs_read_text", _fs_read_text, 1, ["path"])
    )
    interpreter.global_env.bind(
        "fs_write_text",
        BuiltinFunction("fs_write_text", _fs_write_text, 2, ["path", "content"]),
    )
    interpreter.global_env.bind(
        "fs_list_dir", BuiltinFunction("fs_list_dir", _fs_list_dir, 1, ["path"])
    )
    interpreter.global_env.bind(
        "fs_exists", BuiltinFunction("fs_exists", _fs_exists, 1, ["path"])
    )


def install_http_callbacks(
    interpreter, *, allow_private_networks: bool | None = None
) -> None:
    """Install real HTTP implementations for --cap http."""
    import http.client
    import ssl
    from urllib.parse import urljoin
    from urllib.request import (
        HTTPHandler,
        HTTPRedirectHandler,
        HTTPSHandler,
        ProxyHandler,
        Request,
        build_opener,
    )

    from .values import BuiltinFunction, ConstructorValue

    private_networks_ok = (
        _env_truthy("GENO_HTTP_ALLOW_PRIVATE")
        if allow_private_networks is None
        else allow_private_networks
    )

    def _check_scheme(url, fn_name):
        _validate_http_target(url, fn_name, allow_private_networks=private_networks_ok)

    def _open_request(req, fn_name):
        class _ValidatedHTTPConnection(http.client.HTTPConnection):
            def connect(self) -> None:
                self.sock = _create_validated_http_connection(
                    self.host,
                    self.port,
                    self.timeout,
                    getattr(self, "source_address", None),
                    fn_name,
                    allow_private_networks=private_networks_ok,
                )

        class _ValidatedHTTPSConnection(http.client.HTTPSConnection):
            def connect(self) -> None:
                self.sock = _create_validated_http_connection(
                    self.host,
                    self.port,
                    self.timeout,
                    getattr(self, "source_address", None),
                    fn_name,
                    allow_private_networks=private_networks_ok,
                )
                server_hostname = self.host
                context = (
                    getattr(self, "_context", None) or ssl.create_default_context()
                )
                self.sock = context.wrap_socket(
                    self.sock,
                    server_hostname=server_hostname,
                )

        class _ValidatedHTTPHandler(HTTPHandler):
            def http_open(self, request: Any) -> Any:
                return self.do_open(_ValidatedHTTPConnection, request)

        class _ValidatedHTTPSHandler(HTTPSHandler):
            def https_open(self, request: Any) -> Any:
                return self.do_open(_ValidatedHTTPSConnection, request)

        class _HttpOnlyRedirectHandler(HTTPRedirectHandler):
            def redirect_request(
                self,
                request: Any,
                fp: Any,
                code: int,
                msg: str,
                headers: Any,
                newurl: str,
            ) -> Any:
                _check_scheme(urljoin(request.full_url, newurl), fn_name)
                return super().redirect_request(request, fp, code, msg, headers, newurl)

        return build_opener(
            ProxyHandler({}),
            _ValidatedHTTPHandler,
            _ValidatedHTTPSHandler,
            _HttpOnlyRedirectHandler,
        ).open(req, timeout=30)

    def _http_fetch(url):
        _check_scheme(url, "http_fetch")
        try:
            with _open_request(Request(url), "http_fetch") as resp:
                result = _read_limited_utf8_stream(interpreter, resp, "http_fetch")
        except (OSError, ValueError) as e:
            raise RuntimeError(f"http_fetch: {e}")
        return _checked_callback_result(interpreter, result)

    def _http_post(url, body):
        _check_scheme(url, "http_post")
        try:
            req = Request(url, data=body.encode("utf-8"), method="POST")
            req.add_header("Content-Type", "application/json")
            with _open_request(req, "http_post") as resp:
                result = _read_limited_utf8_stream(interpreter, resp, "http_post")
        except (OSError, ValueError) as e:
            raise RuntimeError(f"http_post: {e}")
        return _checked_callback_result(interpreter, result)

    def _http_request(method, url, headers, body):
        try:
            _check_scheme(url, "http_request")
            data = body.encode("utf-8") if body is not None else None
            req = Request(url, data=data, method=method)
            for key, value in headers:
                req.add_header(key, value)
            with _open_request(req, "http_request") as resp:
                resp_headers = [(k, v) for k, v in resp.getheaders()]
                body_text = _read_limited_utf8_stream(interpreter, resp, "http_request")
                result = ConstructorValue(
                    "Ok",
                    {
                        "value": ConstructorValue(
                            "HttpResponse",
                            {
                                "status": resp.status,
                                "body": body_text,
                                "headers": resp_headers,
                            },
                        )
                    },
                )
                return _checked_callback_result(interpreter, result)
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            return ConstructorValue("Err", {"error": str(e)})

    interpreter.global_env.bind(
        "http_fetch", BuiltinFunction("http_fetch", _http_fetch, 1, ["url"])
    )
    interpreter.global_env.bind(
        "http_post", BuiltinFunction("http_post", _http_post, 2, ["url", "body"])
    )
    interpreter.global_env.bind(
        "http_request",
        BuiltinFunction(
            "http_request", _http_request, 4, ["method", "url", "headers", "body"]
        ),
    )


def _minimal_process_env():
    import os

    env = {}
    for key in (
        "PATH",
        "Path",
        "SystemRoot",
        "SYSTEMROOT",
        "WINDIR",
        "windir",
        "COMSPEC",
        "ComSpec",
        "PATHEXT",
    ):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def install_process_callbacks(
    interpreter,
    *,
    inherit_env: bool = False,
    allow_path_search: bool | None = None,
    allowed_executables: Iterable[str] | None = None,
) -> None:
    """Install real process execution callbacks for --cap process."""
    import shlex
    import subprocess
    import tempfile
    import time

    from .sandbox import TimeoutError as SandboxTimeout
    from .values import BuiltinFunction, ConstructorValue

    TIMEOUT = 30
    process_env = None if inherit_env else _minimal_process_env()
    path_search_ok = (
        _env_truthy("GENO_PROCESS_ALLOW_PATH_SEARCH")
        if allow_path_search is None
        else allow_path_search
    )
    executable_allowlist = _resolve_executable_allowlist(allowed_executables)

    def _timeout_error():
        return SandboxTimeout(
            f"Execution timed out after {interpreter.sandbox_config.timeout} seconds"
        )

    def _bounded_process_timeout():
        deadline = interpreter._deadline
        if deadline is None:
            return TIMEOUT, False
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise _timeout_error()
        return min(TIMEOUT, remaining), remaining < TIMEOUT

    def _run_process(argv, *, input_text=None):
        argv, err = _validate_process_argv(
            argv,
            "process",
            process_env=process_env,
            allow_path_search=path_search_ok,
            allowed_executables=executable_allowlist,
        )
        if err is not None:
            raise ValueError(err)
        timeout, bounded_by_deadline = _bounded_process_timeout()
        stdin_payload = input_text.encode("utf-8") if input_text is not None else None
        try:
            with tempfile.TemporaryFile() as stdout_file:
                with tempfile.TemporaryFile() as stderr_file:
                    result = subprocess.run(
                        argv,
                        input=stdin_payload,
                        stdout=stdout_file,
                        stderr=stderr_file,
                        env=process_env,
                        timeout=timeout,
                    )
                    return subprocess.CompletedProcess(
                        argv,
                        result.returncode,
                        _read_limited_temp_text(interpreter, stdout_file, "process"),
                        _read_limited_temp_text(interpreter, stderr_file, "process"),
                    )
        except subprocess.TimeoutExpired:
            if bounded_by_deadline:
                raise _timeout_error()
            raise

    def _process_result(result):
        value = ConstructorValue(
            "Ok",
            {
                "value": ConstructorValue(
                    "ProcessResult",
                    {
                        "exit_code": result.returncode,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                    },
                )
            },
        )
        return _checked_callback_result(interpreter, value)

    def _exec(command):
        try:
            if not isinstance(command, str):
                return ConstructorValue(
                    "Err", {"error": "exec: command must be String"}
                )
            result = _run_process(shlex.split(command))
            return _process_result(result)
        except subprocess.TimeoutExpired:
            return ConstructorValue("Err", {"error": "Process timed out"})
        except (OSError, ValueError, AttributeError, IndexError) as e:
            return ConstructorValue("Err", {"error": str(e)})

    def _exec_with_input(command, stdin_text):
        try:
            if not isinstance(command, str):
                return ConstructorValue(
                    "Err", {"error": "exec_with_input: command must be String"}
                )
            if not isinstance(stdin_text, str):
                return ConstructorValue(
                    "Err", {"error": "exec_with_input: stdin must be String"}
                )
            result = _run_process(shlex.split(command), input_text=stdin_text)
            return _process_result(result)
        except subprocess.TimeoutExpired:
            return ConstructorValue("Err", {"error": "Process timed out"})
        except (OSError, ValueError, AttributeError, IndexError) as e:
            return ConstructorValue("Err", {"error": str(e)})

    def _validate_argv(program, args, fn_name):
        if not isinstance(program, str):
            return f"{fn_name}: program must be String"
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            return f"{fn_name}: args must be List[String]"
        return None

    def _spawn(program, args):
        err = _validate_argv(program, args, "spawn")
        if err is not None:
            return ConstructorValue("Err", {"error": err})
        try:
            result = _run_process([program, *args])
            return _process_result(result)
        except subprocess.TimeoutExpired:
            return ConstructorValue("Err", {"error": "Process timed out"})
        except (OSError, ValueError) as e:
            return ConstructorValue("Err", {"error": str(e)})

    def _spawn_with_input(program, args, stdin_text):
        err = _validate_argv(program, args, "spawn_with_input")
        if err is not None:
            return ConstructorValue("Err", {"error": err})
        if not isinstance(stdin_text, str):
            return ConstructorValue(
                "Err", {"error": "spawn_with_input: stdin must be String"}
            )
        try:
            result = _run_process([program, *args], input_text=stdin_text)
            return _process_result(result)
        except subprocess.TimeoutExpired:
            return ConstructorValue("Err", {"error": "Process timed out"})
        except (OSError, ValueError) as e:
            return ConstructorValue("Err", {"error": str(e)})

    interpreter.global_env.bind("exec", BuiltinFunction("exec", _exec, 1, ["command"]))
    interpreter.global_env.bind(
        "exec_with_input",
        BuiltinFunction("exec_with_input", _exec_with_input, 2, ["command", "stdin"]),
    )
    interpreter.global_env.bind(
        "spawn", BuiltinFunction("spawn", _spawn, 2, ["program", "args"])
    )
    interpreter.global_env.bind(
        "spawn_with_input",
        BuiltinFunction(
            "spawn_with_input", _spawn_with_input, 3, ["program", "args", "stdin"]
        ),
    )


def install_clock_callbacks(interpreter) -> None:
    """Install real clock builtins that need side-effectful operations (sleep)."""
    import time

    from .sandbox import TimeoutError as SandboxTimeout
    from .values import BuiltinFunction

    def _sleep_ms(ms):
        if not isinstance(ms, int) or isinstance(ms, bool):
            raise RuntimeError("sleep_ms: ms must be Int")
        if ms < 0:
            raise RuntimeError(f"sleep_ms: negative duration not allowed ({ms})")
        if ms == 0:
            return None
        sleep_seconds = ms / 1000.0
        deadline = interpreter._deadline
        if deadline is not None and sleep_seconds > deadline - time.perf_counter():
            raise SandboxTimeout(
                f"Execution timed out after {interpreter.sandbox_config.timeout} seconds"
            )
        time.sleep(sleep_seconds)
        return None

    interpreter.global_env.bind(
        "sleep_ms", BuiltinFunction("sleep_ms", _sleep_ms, 1, ["ms"])
    )


def install_stdin_callbacks(interpreter) -> None:
    """Install stdin_read_all for --cap stdin."""
    import sys

    from .values import BuiltinFunction, ConstructorValue

    def _stdin_read_all():
        try:
            buffered = getattr(sys.stdin, "buffer", None)
            data = buffered.read() if buffered is not None else sys.stdin.read()
        except OSError as e:
            return ConstructorValue("Err", {"error": str(e)})
        except UnicodeDecodeError as e:
            return ConstructorValue("Err", {"error": f"stdin is not valid UTF-8: {e}"})
        if isinstance(data, bytes):
            try:
                data = data.decode("utf-8")
            except UnicodeDecodeError as e:
                return ConstructorValue(
                    "Err", {"error": f"stdin is not valid UTF-8: {e}"}
                )
        if not isinstance(data, str):
            return ConstructorValue(
                "Err",
                {"error": f"stdin returned unsupported type: {type(data).__name__}"},
            )
        return _checked_callback_result(
            interpreter, ConstructorValue("Ok", {"value": data})
        )

    interpreter.global_env.bind(
        "stdin_read_all", BuiltinFunction("stdin_read_all", _stdin_read_all, 0, [])
    )


_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


def _decode_request_body(raw_body: bytes) -> str:
    try:
        return raw_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Request body must be valid UTF-8") from exc


def _build_checked_http_request(interpreter, method, raw_path, headers, body):
    from .values import ConstructorValue

    path_parts = raw_path.split("?", 1)
    path = path_parts[0]
    query = path_parts[1] if len(path_parts) > 1 else ""
    request = ConstructorValue(
        "HttpRequest",
        {
            "method": method,
            "path": path,
            "query": query,
            "headers": headers,
            "body": body,
        },
    )
    return _checked_callback_result(interpreter, request)


def _plain_response(handler, status: int, body: str) -> None:
    encoded = body.encode("utf-8", errors="replace")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


def _validate_response_headers(headers):
    """Validate response headers before passing them to BaseHTTPRequestHandler."""
    if headers is None:
        return []
    validated = []
    for header in headers:
        try:
            name, value = header
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Invalid response header entry") from exc
        if not isinstance(name, str) or not _HEADER_NAME_RE.fullmatch(name):
            raise RuntimeError(f"Invalid response header name: {name!r}")
        if not isinstance(value, str) or "\r" in value or "\n" in value:
            raise RuntimeError(f"Invalid response header value for {name!r}")
        validated.append((name, value))
    return validated


def _run_serve_handler(interp: Any, handler: Any, request: Any) -> Any:
    """Invoke a serve route handler with a fresh per-request execution budget.

    ``http_listen`` runs inside the program's own execution, which carries a
    program-wide wall-clock deadline (``sandbox_config.timeout``, default 30s)
    and a cumulative step counter. If each request reused those, the server would
    reject every request with a timeout / step-limit error once the program
    deadline passed or the step budget was exhausted — a few seconds after
    startup the server stays alive but is permanently broken (every request
    fails with no useful response).

    Give each request its own budget instead: clear the inherited deadline and
    reset the step counter and the cumulative output length, then run the handler
    under a fresh ``sandbox_config.timeout`` deadline so it is still individually
    bounded by the same limits (no unbounded execution). The output counter is
    reset too — otherwise a handler that prints would eventually exceed
    ``max_output_length`` and 500 every subsequent request, the same zombie-server
    failure through the output channel. The interpreter's prior state is restored
    afterwards so nothing leaks between requests.

    This save/reset/restore of shared interpreter state is only correct because
    the serve HTTP server is single-threaded (``HTTPServer``, requests handled
    serially). A threaded server would need per-request interpreter state.
    """
    saved_deadline = interp._deadline
    saved_steps = interp.steps
    saved_output = list(interp.output_buffer)
    saved_output_length = interp._output_length
    interp._deadline = None
    interp.steps = 0
    interp.output_buffer.clear()
    interp._output_length = 0
    try:
        return interp.call_function(
            handler, [request], timeout=interp.sandbox_config.timeout
        )
    finally:
        interp._deadline = saved_deadline
        interp.steps = saved_steps
        interp.output_buffer[:] = saved_output
        interp._output_length = saved_output_length


def install_serve_callbacks(interpreter):
    """Install HTTP server builtins (http_listen, http_route) for --cap serve.

    Note: http_respond is registered as a standalone builtin in interpreter.py
    since it's a pure data constructor that doesn't need the serve capability.
    """
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from .sandbox import SandboxError
    from .values import BuiltinFunction, GenoRuntimeError

    routes: list[tuple[str, str, Any]] = []

    def _http_route(method, path, handler):
        route_count = len(routes) + 1
        route_limit = interpreter.sandbox_config.max_collection_size
        if route_count > route_limit:
            raise GenoRuntimeError(
                f"Route registry size exceeds limit ({route_count} > {route_limit})"
            )
        routes.append((method.upper(), path, handler))
        return None

    def _http_listen(port):
        interp = interpreter

        class Handler(BaseHTTPRequestHandler):
            # Per-connection socket read timeout (StreamRequestHandler applies
            # this to the accepted connection). Without it a single client that
            # opens a connection and sends nothing wedges this single-threaded
            # server indefinitely (H-05). Threading is intentionally not used —
            # the per-request budget save/restore in _run_serve_handler relies
            # on serial handling.
            timeout = 30

            def _handle(self):
                raw = self.headers.get("Content-Length", "0")
                try:
                    content_length = int(raw)
                except ValueError:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Invalid Content-Length header")
                    return
                if content_length < 0:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Invalid Content-Length: must not be negative")
                    return
                if content_length > 1_048_576:
                    self.send_response(413)
                    self.end_headers()
                    self.wfile.write(b"Request body too large")
                    return
                try:
                    body = (
                        _decode_request_body(self.rfile.read(content_length))
                        if content_length
                        else ""
                    )
                except ValueError as exc:
                    _plain_response(self, 400, str(exc))
                    return
                headers = [(k, v) for k, v in self.headers.items()]
                try:
                    request = _build_checked_http_request(
                        interp, self.command, self.path, headers, body
                    )
                except GenoRuntimeError as exc:
                    _plain_response(self, 413, exc.message)
                    return

                path = self.path.split("?", 1)[0]
                for r_method, r_path, handler in routes:
                    if r_method == self.command and r_path == path:
                        try:
                            response = _run_serve_handler(interp, handler, request)
                            status = response.fields["status"]
                            if not isinstance(status, int) or isinstance(status, bool):
                                raise RuntimeError("Invalid response status")
                            response_headers = _validate_response_headers(
                                response.fields.get("headers")
                            )
                            body_value = response.fields["body"]
                            if not isinstance(body_value, str):
                                raise RuntimeError("Invalid response body")
                            response_body = body_value.encode("utf-8")
                        except SandboxError:
                            raise
                        except Exception:
                            # Log the failure server-side (with traceback) but
                            # return a generic 500 — never leak handler internals
                            # (exception message/type) to the HTTP client (M-05).
                            logger.exception(
                                "Unhandled error in serve handler for %s %s",
                                self.command,
                                self.path,
                            )
                            _plain_response(self, 500, "Internal Server Error")
                            return
                        self.send_response(status)
                        for hk, hv in response_headers:
                            self.send_header(hk, hv)
                        self.end_headers()
                        self.wfile.write(response_body)
                        return
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not Found")

            def do_GET(self):
                self._handle()

            def do_POST(self):
                self._handle()

            def do_PUT(self):
                self._handle()

            def do_DELETE(self):
                self._handle()

            def log_message(self, format, *args):
                pass  # Suppress default logging

        try:
            server = HTTPServer(("127.0.0.1", port), Handler)
        except OSError as e:
            raise RuntimeError(f"Cannot bind to port {port}: {e}")
        print(f"Listening on http://127.0.0.1:{port}")
        server.serve_forever()
        return None

    interpreter.global_env.bind(
        "http_route",
        BuiltinFunction("http_route", _http_route, 3, ["method", "path", "handler"]),
    )
    interpreter.global_env.bind(
        "http_listen",
        BuiltinFunction("http_listen", _http_listen, 1, ["port"]),
    )
