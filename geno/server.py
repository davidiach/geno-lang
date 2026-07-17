"""Hosted runtime server for health, metrics, and Geno execution."""

from __future__ import annotations

import argparse
import collections
import ipaddress
import json
import logging
import math
import multiprocessing
import os
import re
import secrets
import signal
import socket
import ssl
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import AbstractSet, Any, Iterator, cast
from urllib.parse import urlsplit

from ._darwin_resource import rlimit_as_ceiling
from .api import RunConfig, constrain_prefix, run
from .builtin_registry import DEFAULT_ALLOWED_CAPABILITIES
from .capabilities import CapabilityParseError, normalize_capability_values
from .monitoring import RunMetrics, RunOutcome, RuntimeMetricsCollector
from .version_support import is_supported_python, unsupported_python_message

logger = logging.getLogger(__name__)

_CONNECTION_SEMAPHORE_FACTORY = threading.BoundedSemaphore
_WORKER_READY = "ready"
_WORKER_JOB_HANDLE: Any | None = None
_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


# ---------------------------------------------------------------------------
# Configuration — all limits can be overridden via environment variables.
# ---------------------------------------------------------------------------


def _env_float(key: str, default: float) -> float:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        parsed = float(val)
    except ValueError as exc:
        raise ValueError(
            f"Environment variable {key} must be a float, got {val!r}"
        ) from exc
    if not math.isfinite(parsed):
        raise ValueError(
            f"Environment variable {key} must be a finite float, got {val!r}"
        )
    return parsed


def _env_positive_float(key: str, default: float) -> float:
    parsed = _env_float(key, default)
    if parsed <= 0:
        raise ValueError(f"Environment variable {key} must be positive")
    return parsed


def _env_non_negative_float(key: str, default: float) -> float:
    parsed = _env_float(key, default)
    if parsed < 0:
        raise ValueError(f"Environment variable {key} must be non-negative")
    return parsed


def _env_int(key: str, default: int) -> int:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError as exc:
        raise ValueError(
            f"Environment variable {key} must be an integer, got {val!r}"
        ) from exc


def _env_non_negative_int(key: str, default: int) -> int:
    parsed = _env_int(key, default)
    if parsed < 0:
        raise ValueError(f"Environment variable {key} must be non-negative")
    return parsed


def _env_positive_int(key: str, default: int) -> int:
    parsed = _env_int(key, default)
    if parsed <= 0:
        raise ValueError(f"Environment variable {key} must be positive")
    return parsed


def _env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    normalized = val.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"Environment variable {key} must be a boolean "
        "(one of 1/0, true/false, yes/no, on/off)"
    )


def _env_optional_bool(key: str) -> bool | None:
    if key not in os.environ:
        return None
    return _env_bool(key, False)


MAX_REQUEST_BODY_BYTES: int = _env_positive_int(
    "GENO_MAX_REQUEST_BODY_BYTES", 1_048_576
)  # 1 MB
MAX_RESPONSE_BODY_BYTES: int = _env_positive_int(
    "GENO_MAX_RESPONSE_BODY_BYTES", 8_388_608
)  # 8 MB
MAX_JSON_NESTING_DEPTH: int = _env_positive_int("GENO_MAX_JSON_NESTING_DEPTH", 128)
MAX_MODULE_SOURCE_BYTES: int = _env_non_negative_int(
    "GENO_MAX_MODULE_SOURCE_BYTES", 1_000_000
)  # 1 MB
MAX_MODULES: int = _env_non_negative_int("GENO_MAX_MODULES", 50)
MAX_TIMEOUT_SECONDS: float = _env_positive_float("GENO_MAX_TIMEOUT_SECONDS", 30.0)
MAX_WALL_CLOCK_SECONDS: float = _env_positive_float("GENO_MAX_WALL_CLOCK_SECONDS", 30.0)
CONSTRAIN_WALL_CLOCK_SECONDS: float = _env_positive_float(
    "GENO_CONSTRAIN_WALL_CLOCK_SECONDS", 5.0
)
DEFAULT_MAX_STEPS: int = _env_int("GENO_DEFAULT_MAX_STEPS", 10_000)
MAX_STEPS: int = _env_int("GENO_MAX_STEPS", 10_000_000)
if DEFAULT_MAX_STEPS <= 0:
    raise ValueError("GENO_DEFAULT_MAX_STEPS must be positive")
if DEFAULT_MAX_STEPS > MAX_STEPS:
    raise ValueError("GENO_DEFAULT_MAX_STEPS must be <= GENO_MAX_STEPS")
WORKER_STARTUP_GRACE_SECONDS: float = _env_positive_float(
    "GENO_WORKER_STARTUP_GRACE_SECONDS", 10.0
)
WORKER_MAX_MEMORY_BYTES: int = _env_non_negative_int(
    "GENO_WORKER_MAX_MEMORY_BYTES", 256 * 1024 * 1024
)
WORKER_MAX_CPU_TIME: float = _env_non_negative_float(
    "GENO_WORKER_MAX_CPU_TIME", MAX_WALL_CLOCK_SECONDS
)
WORKER_MAX_FILE_SIZE_BYTES: int = _env_non_negative_int(
    "GENO_WORKER_MAX_FILE_SIZE_BYTES", 0
)
WORKER_MAX_PROCESSES: int = _env_positive_int("GENO_WORKER_MAX_PROCESSES", 1)
MAX_CONCURRENT_REQUESTS: int = _env_positive_int("GENO_MAX_CONCURRENT_REQUESTS", 16)
MAX_CONNECTIONS: int = _env_positive_int("GENO_MAX_CONNECTIONS", 64)
_REQUEST_TIMEOUT_SECONDS: int = _env_positive_int("GENO_REQUEST_TIMEOUT_SECONDS", 30)

# Rate limiting: sliding-window per client IP on POST endpoints.
# Set GENO_RATE_LIMIT_REQUESTS=0 to disable rate limiting entirely.
_RATE_LIMIT_REQUESTS: int = _env_int("GENO_RATE_LIMIT_REQUESTS", 60)
_RATE_LIMIT_WINDOW_SECONDS: float = _env_float("GENO_RATE_LIMIT_WINDOW_SECONDS", 60.0)
_RATE_LIMIT_MAX_BUCKETS: int = _env_positive_int("GENO_RATE_LIMIT_MAX_BUCKETS", 4096)

# Trusted proxy: when set to a specific IP (e.g. "127.0.0.1"), the server
# will read the real client IP from the X-Forwarded-For header when the
# TCP connection originates from that address.  Leave empty to use the TCP
# peer address directly (safe when no reverse proxy is in front).
_TRUSTED_PROXY: str | None = os.environ.get("GENO_TRUSTED_PROXY") or None

_REQUEST_FIXED_RESOURCE_LIMIT_FIELDS = frozenset(
    {
        "max_memory_bytes",
        "max_cpu_time",
        "max_file_size_bytes",
        "max_processes",
        "max_recursion_depth",
        "max_output_length",
        "max_collection_size",
        "max_integer_bits",
    }
)


def _env_csv_set(key: str) -> frozenset[str]:
    val = os.environ.get(key, "")
    return frozenset(part.strip() for part in val.split(",") if part.strip())


_CORS_ALLOWED_ORIGINS: frozenset[str] = _env_csv_set("GENO_CORS_ALLOWED_ORIGINS")
_ALLOWED_HOSTS: frozenset[str] = _env_csv_set("GENO_ALLOWED_HOSTS")
_ALLOWED_ENV_NAMES: frozenset[str] = _env_csv_set("GENO_ALLOWED_ENV_NAMES")
_ALLOWED_ENV_PREFIXES: frozenset[str] = _env_csv_set("GENO_ALLOWED_ENV_PREFIXES")
_REQUIRE_AUTH_FOR_METRICS: bool | None = _env_optional_bool(
    "GENO_REQUIRE_AUTH_FOR_METRICS"
)
_REQUIRE_AUTH_FOR_PLAYGROUND: bool = _env_bool(
    "GENO_REQUIRE_AUTH_FOR_PLAYGROUND",
    False,
)


class RequestError(ValueError):
    """Raised when the request body is malformed or unsupported."""


class ResponseTooLarge(RuntimeError):
    """Raised before headers are sent when a JSON response exceeds its bound."""


@dataclass(frozen=True)
class RunRequest:
    """Parsed hosted ``/run`` request payload."""

    source: str
    filename: str
    source_bytes: int
    config: RunConfig


@dataclass(frozen=True)
class ConstrainRequest:
    """Parsed hosted ``/constrain`` request payload."""

    prefix: str
    source_bytes: int


# ---------------------------------------------------------------------------
# Bind safety
# ---------------------------------------------------------------------------


def _is_loopback_host(host: str) -> bool:
    """Return True when the requested bind host is loopback-only."""
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _ip_address_or_none(value: str) -> _IPAddress | None:
    normalized = value.strip()
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    try:
        return ipaddress.ip_address(normalized)
    except ValueError:
        return None


def _is_unspecified_host(host: str) -> bool:
    """Return True for wildcard bind addresses such as 0.0.0.0 or ::."""
    ip = _ip_address_or_none(host)
    return ip is not None and ip.is_unspecified


def _client_ip_from_x_forwarded_for(
    forwarded_for: str, trusted_proxy: str | None
) -> str | None:
    """Return the rightmost valid, non-proxy X-Forwarded-For client IP."""
    trusted_proxy_ip = (
        _ip_address_or_none(trusted_proxy) if trusted_proxy is not None else None
    )
    for raw_part in reversed(forwarded_for.split(",")):
        candidate = _ip_address_or_none(raw_part)
        if candidate is None:
            continue
        if trusted_proxy_ip is not None and candidate == trusted_proxy_ip:
            continue
        return str(candidate)
    return None


def _enforce_secure_bind(
    host: str,
    api_key: str | None,
    *,
    allow_insecure: bool = False,
    allowed_hosts: AbstractSet[str] | None = None,
) -> None:
    """Refuse an unsafe non-loopback bind unless explicitly opted in.

    Two independent guards for non-loopback binds (both bypassed by
    ``--allow-insecure``):

    * authentication — require ``GENO_API_KEY``;
    * Host allow-list — require ``GENO_ALLOWED_HOSTS``. The allow-list cannot be
      derived from a wildcard bind such as ``0.0.0.0``, so without it every
      request is rejected with HTTP 421 and the deployment is silently dead. Fail
      fast with an actionable message instead.
    """
    if _is_loopback_host(host) or allow_insecure:
        return

    if not api_key:
        logger.error(
            "Refusing to bind to non-loopback address without authentication. "
            "Set GENO_API_KEY to enable API key auth, or pass --allow-insecure "
            "to explicitly opt in to unauthenticated access."
        )
        sys.exit(1)

    if allowed_hosts is None:
        allowed_hosts = _ALLOWED_HOSTS
    if not allowed_hosts:
        logger.error(
            "Refusing to bind to non-loopback address %s without GENO_ALLOWED_HOSTS. "
            "The Host allow-list cannot be derived from a wildcard bind, so every "
            "request would be rejected with HTTP 421. Set GENO_ALLOWED_HOSTS to the "
            "comma-separated hostnames clients use (e.g. 'api.example.com'), or set "
            "GENO_ALLOWED_HOSTS='*' to disable Host-header validation.",
            host,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class _RateLimiter:
    """Thread-safe sliding-window rate limiter keyed by string (e.g. client IP)."""

    def __init__(
        self, max_requests: int, window_seconds: float, *, max_buckets: int = 4096
    ) -> None:
        if (
            isinstance(max_requests, bool)
            or not isinstance(max_requests, int)
            or max_requests <= 0
        ):
            raise ValueError("rate limit max_requests must be a positive integer")
        if (
            isinstance(window_seconds, bool)
            or not isinstance(window_seconds, (int, float))
            or not math.isfinite(window_seconds)
            or window_seconds <= 0
        ):
            raise ValueError(
                "rate limit window_seconds must be a positive finite number"
            )
        if (
            isinstance(max_buckets, bool)
            or not isinstance(max_buckets, int)
            or max_buckets <= 0
        ):
            raise ValueError("rate limit max_buckets must be a positive integer")
        self._max = max_requests
        self._window = float(window_seconds)
        self._max_buckets = max_buckets
        self._lock = threading.Lock()
        self._buckets: collections.OrderedDict[str, collections.deque[float]] = (
            collections.OrderedDict()
        )

    def _sweep_expired(self, cutoff: float) -> None:
        for key, bucket in list(self._buckets.items()):
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if not bucket:
                del self._buckets[key]

    def is_allowed(self, key: str) -> bool:
        """Return True and record the hit, or False if the limit is exceeded."""
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            self._sweep_expired(cutoff)
            bucket = self._buckets.get(key)
            if bucket is None:
                while len(self._buckets) >= self._max_buckets:
                    self._buckets.popitem(last=False)
                bucket = collections.deque()
                self._buckets[key] = bucket
            else:
                self._buckets.move_to_end(key)
            if len(bucket) >= self._max:
                return False
            bucket.append(now)
            return True


# ---------------------------------------------------------------------------
# Startup health validation
# ---------------------------------------------------------------------------


def _run_startup_checks() -> list[str]:
    """Run pre-flight checks. Returns a list of error messages (empty = all good)."""
    errors: list[str] = []

    if not is_supported_python():
        errors.append(unsupported_python_message())

    try:
        from .api import RunConfig as _RC
        from .api import run as _api_run

        _result = _api_run(
            "func id(x: Int) -> Int\n    example 1 -> 1\n    return x\nend func id",
            config=_RC(timeout=5.0),
        )
        if not _result.ok:
            errors.append(
                "Sandbox self-test failed: "
                + "; ".join(d.message for d in _result.diagnostics)
            )
    except Exception as exc:  # pragma: no cover
        errors.append(f"Sandbox self-test raised {type(exc).__name__}: {exc}")

    return errors


# ---------------------------------------------------------------------------
# Playground HTML
# ---------------------------------------------------------------------------

_PLAYGROUND_EXAMPLES = {
    "Hello World": """\
func greet(name: String) -> String
    example "Alice" -> "Hello, Alice!"
    return "Hello, " + name + "!"
end func

func main() -> String
    return greet("World")
end func
""",
    "Pattern Matching": """\
type Shape = Circle(radius: Float) | Rectangle(width: Float, height: Float)

func area(s: Shape) -> Float
    example Circle(1.0) -> 3.141592653589793
    example Rectangle(3.0, 4.0) -> 12.0
    match s with
        | Circle(r) -> return 3.141592653589793 * r * r
        | Rectangle(w, h) -> return w * h
    end match
end func

func main() -> Float
    let shapes: List[Shape] = [Circle(5.0), Rectangle(3.0, 4.0)]
    var total: Float = 0.0
    for s: Shape in shapes do
        total = total + area(s)
    end for
    return total
end func
""",
    "Pipeline Operator": """\
func double(x: Int) -> Int
    example 3 -> 6
    return x * 2
end func

func add_one(x: Int) -> Int
    example 5 -> 6
    return x + 1
end func

func is_even(x: Int) -> Bool
    example 4 -> true
    example 3 -> false
    return x % 2 == 0
end func

func main() -> List[Int]
    let nums: List[Int] = [1, 2, 3, 4, 5]
    return nums
        |> map(_, fn(x: Int) -> double(x))
        |> filter(_, fn(x: Int) -> is_even(x))
        |> map(_, fn(x: Int) -> add_one(x))
end func
""",
    "Example Clauses": """\
func fibonacci(n: Int) -> Int
    requires n >= 0
    example 0 -> 0
    example 1 -> 1
    example 5 -> 5
    example 10 -> 55
    if n <= 1 then
        return n
    end if
    var a: Int = 0
    var b: Int = 1
    for i: Int in range(2, n + 1) do
        let next: Int = a + b
        a = b
        b = next
    end for
    return b
end func

func main() -> List[Int]
    return map(range(0, 10), fn(n: Int) -> fibonacci(n))
end func
""",
    "Error Handling": """\
func safe_divide(a: Int, b: Int) -> Result[Int, String]
    example (10, 2) -> Ok(5)
    example (10, 0) -> Err("division by zero")
    if b == 0 then
        return Err("division by zero")
    end if
    return Ok(a / b)
end func

func describe(r: Result[Int, String]) -> String
    example Ok(5) -> "Ok: 5"
    example Err("oops") -> "Err: oops"
    match r with
        | Ok(v) -> return "Ok: " + to_string(v)
        | Err(e) -> return "Err: " + e
    end match
end func

func main() -> List[String]
    return [
        describe(safe_divide(10, 2)),
        describe(safe_divide(8, 0)),
        describe(safe_divide(9, 3))
    ]
end func
""",
}


def _playground_html() -> str:
    """Return the self-contained playground HTML page."""
    examples_js = "{\n"
    for name, code in _PLAYGROUND_EXAMPLES.items():
        # Escape for JS template literals inside <script> (NOT html.escape —
        # browsers don't decode HTML entities inside <script> tags).
        escaped = (
            code.replace("\\", "\\\\")
            .replace("`", "\\`")
            .replace("${", "\\${")
            .replace("</script>", "<\\/script>")
        )
        examples_js += f"  {json.dumps(name)}: `{escaped}`,\n"
    examples_js += "}"

    page = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Geno Playground</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: #1a1a2e; color: #e0e0e0; height: 100vh; display: flex; flex-direction: column; }}
header {{ background: #16213e; padding: 0.6rem 1rem; display: flex; align-items: center;
          gap: 1rem; flex-wrap: wrap; border-bottom: 1px solid #0f3460; }}
header h1 {{ font-size: 1.1rem; color: #e94560; white-space: nowrap; }}
header select, header button {{
    background: #0f3460; color: #e0e0e0; border: 1px solid #533483;
    padding: 0.35rem 0.7rem; border-radius: 4px; font-size: 0.85rem; cursor: pointer; }}
header button {{ background: #e94560; border-color: #e94560; color: #fff; font-weight: 600; }}
header button:hover {{ background: #c73650; }}
header button:disabled {{ opacity: 0.5; cursor: wait; }}
.container {{ flex: 1; display: flex; min-height: 0; }}
@media (max-width: 768px) {{ .container {{ flex-direction: column; }} }}
.editor-pane, .output-pane {{ flex: 1; display: flex; flex-direction: column; min-height: 0; }}
.editor-pane {{ border-right: 1px solid #0f3460; }}
@media (max-width: 768px) {{ .editor-pane {{ border-right: none; border-bottom: 1px solid #0f3460; }} }}
.pane-header {{ background: #16213e; padding: 0.3rem 0.8rem; font-size: 0.75rem;
                color: #888; text-transform: uppercase; letter-spacing: 0.05em; }}
textarea {{ flex: 1; background: #0a0a1a; color: #e0e0e0; border: none; padding: 0.8rem;
           font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; font-size: 0.85rem;
           line-height: 1.5; resize: none; tab-size: 4; outline: none; }}
textarea::placeholder {{ color: #444; }}
.output {{ flex: 1; overflow: auto; padding: 0.8rem; font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
           font-size: 0.82rem; line-height: 1.5; background: #0a0a1a; white-space: pre-wrap; word-break: break-word; }}
.output .result {{ color: #4ade80; }}
.output .stdout {{ color: #e0e0e0; }}
.output .error {{ color: #f87171; }}
.output .timing {{ color: #666; font-size: 0.75rem; margin-top: 0.5rem; }}
.output .diagnostics {{ color: #fbbf24; }}
footer {{ background: #16213e; padding: 0.3rem 1rem; font-size: 0.7rem; color: #555;
          display: flex; justify-content: space-between; border-top: 1px solid #0f3460; }}
footer a {{ color: #e94560; text-decoration: none; }}
.share-btn {{ background: none; border: 1px solid #533483; color: #888; font-size: 0.75rem;
              padding: 0.2rem 0.5rem; border-radius: 3px; cursor: pointer; }}
.share-btn:hover {{ color: #e0e0e0; border-color: #e94560; }}
</style>
</head>
<body>
<header>
  <h1>Geno Playground</h1>
  <select id="examples"></select>
  <button id="runBtn" onclick="runCode()">Run (Ctrl+Enter)</button>
  <button class="share-btn" onclick="shareUrl()">Share</button>
</header>
<div class="container">
  <div class="editor-pane">
    <div class="pane-header">Source</div>
    <textarea id="editor" spellcheck="false" placeholder="Write Geno code here..."></textarea>
  </div>
  <div class="output-pane">
    <div class="pane-header">Output</div>
    <div class="output" id="output"><span class="timing">Press Run or Ctrl+Enter to execute.</span></div>
  </div>
</div>
<footer>
  <span>Geno &mdash; a language for LLM-generated code</span>
  <span><a href="https://github.com/davidiach/geno-lang" target="_blank">GitHub</a></span>
</footer>
<script>
const EXAMPLES = {examples_js};
const BASE_URL = window.location.origin;

// Populate example dropdown
const sel = document.getElementById('examples');
Object.keys(EXAMPLES).forEach((name, i) => {{
  const opt = document.createElement('option');
  opt.value = name; opt.textContent = name;
  sel.appendChild(opt);
}});
sel.addEventListener('change', () => {{
  document.getElementById('editor').value = EXAMPLES[sel.value] || '';
}});

// Load from URL hash or default example
function loadFromHash() {{
  const hash = window.location.hash.slice(1);
  if (hash) {{
    try {{
      document.getElementById('editor').value = decodeURIComponent(atob(hash));
      return;
    }} catch(e) {{}}
  }}
  document.getElementById('editor').value = EXAMPLES[Object.keys(EXAMPLES)[0]] || '';
}}
loadFromHash();
window.addEventListener('hashchange', loadFromHash);

// Keyboard shortcut
document.getElementById('editor').addEventListener('keydown', (e) => {{
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {{
    e.preventDefault(); runCode();
  }}
  // Tab inserts spaces
  if (e.key === 'Tab') {{
    e.preventDefault();
    const ta = e.target; const start = ta.selectionStart;
    ta.value = ta.value.substring(0, start) + '    ' + ta.value.substring(ta.selectionEnd);
    ta.selectionStart = ta.selectionEnd = start + 4;
  }}
}});

async function runCode() {{
  const btn = document.getElementById('runBtn');
  const out = document.getElementById('output');
  const source = document.getElementById('editor').value;
  btn.disabled = true; btn.textContent = 'Running...';
  out.innerHTML = '<span class="timing">Running...</span>';
  try {{
    const resp = await fetch(BASE_URL + '/run', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ source, check_examples: true, capabilities: ["print"] }})
    }});
    const data = await resp.json();
    let html = '';
    if (data.output) {{
      html += '<span class="stdout">' + escapeHtml(data.output) + '</span>\\n';
    }}
    if (data.ok) {{
      html += '<span class="result">=&gt; ' + escapeHtml(formatValue(data.value)) + '</span>\\n';
    }} else {{
      html += '<span class="error">' + escapeHtml(formatValue(data.value || 'Error')) + '</span>\\n';
    }}
    if (data.diagnostics && data.diagnostics.length > 0) {{
      data.diagnostics.forEach(d => {{
        html += '<span class="diagnostics">' + escapeHtml(d.message) + '</span>\\n';
      }});
    }}
    if (data.timing) {{
      const t = data.timing;
      html += '<span class="timing">Total: ' + t.total_ms.toFixed(1) + 'ms';
      if (t.typecheck_ms) html += ' (typecheck: ' + t.typecheck_ms.toFixed(1) + 'ms, run: ' + (t.run_ms || 0).toFixed(1) + 'ms)';
      html += '</span>';
    }}
    out.innerHTML = html || '<span class="timing">No output.</span>';
  }} catch(err) {{
    out.innerHTML = '<span class="error">Connection error: ' + escapeHtml(err.message) + '\\n\\nMake sure geno serve is running at ' + escapeHtml(BASE_URL) + '</span>';
  }}
  btn.disabled = false; btn.textContent = 'Run (Ctrl+Enter)';
}}

function shareUrl() {{
  const source = document.getElementById('editor').value;
  const hash = btoa(encodeURIComponent(source));
  const url = window.location.origin + window.location.pathname + '#' + hash;
  navigator.clipboard.writeText(url).then(() => {{
    const btn = document.querySelector('.share-btn');
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = 'Share', 1500);
  }}).catch(() => {{
    prompt('Copy this URL:', url);
  }});
}}

function escapeHtml(s) {{
  const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
}}

function formatValue(value) {{
  if (value === null || value === undefined) return String(value);
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {{
    return String(value);
  }}
  try {{
    return JSON.stringify(value, null, 2);
  }} catch (_err) {{
    return String(value);
  }}
}}
</script>
</body>
</html>"""
    return page


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------


_HostPort = tuple[str, int | None]


def _normalize_host_port(value: str) -> _HostPort | None:
    value = value.strip()
    if not value or "," in value or any(ch.isspace() for ch in value):
        return None
    try:
        parsed = urlsplit(f"//{value}")
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return None
    host = parsed.hostname
    if not host:
        return None
    return host.rstrip(".").lower(), port


# Sentinel entry meaning "Host validation is disabled" (GENO_ALLOWED_HOSTS=*).
# This pair is only ever placed in the allow-set by _build_allowed_host_set when
# the operator configured the literal "*"; that function rejects any other
# configured entry that would normalize to it (e.g. "*."), so a request is
# allowed via this sentinel only when validation was explicitly disabled. Note
# that _normalize_host_port("*") does yield this pair — the safety comes from set
# membership being operator-controlled, not from the header being unrepresentable.
_ANY_HOST: _HostPort = ("*", None)


def _default_allowed_host_names(bind_host: str) -> set[str]:
    normalized = bind_host.strip().removeprefix("[").removesuffix("]").lower()
    allowed = set() if _is_unspecified_host(bind_host) else {normalized}
    allowed.discard("")
    if _is_loopback_host(bind_host):
        allowed.update({"localhost", "127.0.0.1", "::1"})
    return allowed


def _build_allowed_host_set(
    bind_host: str,
    bound_port: int,
    configured_hosts: AbstractSet[str],
) -> frozenset[_HostPort]:
    if "*" in configured_hosts:
        # Operator explicitly disabled Host validation.
        return frozenset({_ANY_HOST})
    allowed: set[_HostPort] = {
        (host, bound_port) for host in _default_allowed_host_names(bind_host)
    }
    for entry in configured_hosts:
        parsed = _normalize_host_port(entry)
        if parsed is None or parsed == _ANY_HOST:
            # Reject entries that normalize to the wildcard sentinel (e.g. "*.")
            # so a typo cannot silently disable Host validation — only the exact
            # literal "*" (handled above) opts out.
            raise ValueError(f"invalid allowed host entry: {entry!r}")
        allowed.add(parsed)
    return frozenset(allowed)


def _host_allowed(host_header: str, allowed_hosts: AbstractSet[_HostPort]) -> bool:
    if _ANY_HOST in allowed_hosts:
        return True
    parsed = _normalize_host_port(host_header)
    if parsed is None:
        return False
    host, port = parsed
    return (host, port) in allowed_hosts or (host, None) in allowed_hosts


def _host_header_is_loopback(host_header: str) -> bool:
    """Return True when the Host header names a loopback host (any port)."""
    parsed = _normalize_host_port(host_header)
    if parsed is None:
        return False
    return _is_loopback_host(parsed[0])


def _peer_is_loopback(handler: BaseHTTPRequestHandler) -> bool:
    """Return True when the request's actual TCP peer is a loopback address.

    Uses the real socket peer (``client_address``), which a remote client cannot
    spoof — unlike the Host header — so it is a safe discriminator for "this is a
    genuinely local connection such as a container health probe".
    """
    client_address = getattr(handler, "client_address", None)
    if not client_address:
        return False
    return _is_loopback_host(str(client_address[0]))


def _reject_bad_host(handler: BaseHTTPRequestHandler) -> None:
    body = b'{"error": "invalid Host header"}\n'
    handler.send_response(HTTPStatus.MISDIRECTED_REQUEST)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    _add_security_headers(handler)
    handler.end_headers()
    handler.wfile.write(body)


def _header_values(handler: BaseHTTPRequestHandler, name: str) -> tuple[str, ...]:
    """Return every field value without collapsing duplicate HTTP headers."""
    headers = handler.headers
    get_all = getattr(headers, "get_all", None)
    if callable(get_all):
        values = get_all(name, [])
        return tuple(str(value) for value in values)
    value = headers.get(name)
    if value is None:
        return ()
    return (str(value),)


def _cors_origin_allowed(origin: str, allowed_origins: AbstractSet[str]) -> bool:
    return origin in allowed_origins


def _request_origin_allowed(handler: BaseHTTPRequestHandler) -> bool:
    """Allow absent, same-origin, and explicitly configured Origin values."""
    origins = _header_values(handler, "Origin")
    if len(origins) > 1:
        return False
    origin = origins[0] if origins else ""
    if not origin:
        return True
    allowed_origins = cast(
        AbstractSet[str],
        getattr(handler, "_cors_allowed_origins", _CORS_ALLOWED_ORIGINS),
    )
    if _cors_origin_allowed(origin, allowed_origins):
        return True
    try:
        parsed = urlsplit(origin)
        origin_port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
    ):
        return False
    origin_default_port = 443 if parsed.scheme == "https" else 80
    origin_host = (
        parsed.scheme,
        parsed.hostname.rstrip(".").lower(),
        origin_port if origin_port is not None else origin_default_port,
    )
    hosts = _header_values(handler, "Host")
    if len(hosts) != 1:
        return False
    request_host = _normalize_host_port(hosts[0])
    if request_host is None:
        return False
    request_name, request_port = request_host
    request_scheme = (
        "https"
        if isinstance(getattr(handler, "connection", None), ssl.SSLSocket)
        else "http"
    )
    trusted_proxy = getattr(handler, "_trusted_proxy", None)
    if (
        trusted_proxy is not None
        and getattr(handler, "client_address", (None,))[0] == trusted_proxy
    ):
        forwarded_proto_values = _header_values(handler, "X-Forwarded-Proto")
        if forwarded_proto_values:
            if len(forwarded_proto_values) != 1:
                return False
            forwarded_proto = forwarded_proto_values[0].strip().lower()
            if forwarded_proto not in {"http", "https"}:
                return False
            request_scheme = forwarded_proto

    request_default_port = 443 if request_scheme == "https" else 80
    request_origin = (
        request_scheme,
        request_name,
        request_port if request_port is not None else request_default_port,
    )
    return request_origin == origin_host


def _add_cors_headers(handler: BaseHTTPRequestHandler) -> None:
    """Add CORS headers for browser clients."""
    allowed_origins = cast(
        AbstractSet[str],
        getattr(handler, "_cors_allowed_origins", _CORS_ALLOWED_ORIGINS),
    )
    origins = _header_values(handler, "Origin")
    origin = origins[0] if len(origins) == 1 else ""

    if origin and _cors_origin_allowed(origin, allowed_origins):
        handler.send_header("Access-Control-Allow-Origin", origin)
        handler.send_header("Vary", "Origin")

    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header(
        "Access-Control-Allow-Headers",
        "Content-Type, Authorization, X-API-Key",
    )
    handler.send_header("Access-Control-Max-Age", "3600")


def _add_security_headers(handler: BaseHTTPRequestHandler) -> None:
    """Add hardening headers common to every response (L-17).

    ``nosniff`` stops content-type sniffing; ``no-store`` keeps auth-gated
    build info (/healthz) and metrics out of shared caches; the referrer and
    frame policies are conservative defaults for a JSON/health API.
    """
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Referrer-Policy", "no-referrer")


def _json_ascii_string_size(value: str) -> int:
    """Return JSONEncoder's ensure_ascii string size without allocating it."""
    size = 2  # quotes
    for character in value:
        codepoint = ord(character)
        if character in {'"', "\\"} or character in "\b\f\n\r\t":
            size += 2
        elif codepoint < 0x20:
            size += 6
        elif codepoint < 0x80:
            size += 1
        elif codepoint <= 0xFFFF:
            size += 6
        else:
            size += 12
        if size > MAX_RESPONSE_BODY_BYTES:
            raise ResponseTooLarge("JSON response body exceeds configured limit")
    return size


def _dict_json_children(value: dict[Any, Any]) -> Iterator[Any]:
    for key, item in value.items():
        yield key
        yield item


def _bounded_json_int_text(value: int) -> str:
    """Render an integer without Python's process-global decimal digit limit."""
    negative = value < 0
    magnitude = -value if negative else value
    if magnitude == 0:
        return "0"

    # Reject obviously oversized integers before repeated division.  30104 / 100000
    # is a conservative upper bound for log10(2), so this cannot admit a value
    # whose decimal representation alone exceeds the response limit.
    estimated_digits = (magnitude.bit_length() * 30_104) // 100_000 + 1
    if estimated_digits + int(negative) > MAX_RESPONSE_BODY_BYTES:
        raise ResponseTooLarge("JSON response body exceeds configured limit")

    base = 1_000_000_000
    chunks: list[int] = []
    while magnitude:
        magnitude, remainder = divmod(magnitude, base)
        chunks.append(remainder)
    prefix = "-" if negative else ""
    return (
        prefix
        + str(chunks[-1])
        + "".join(f"{chunk:09d}" for chunk in reversed(chunks[:-1]))
    )


class _BoundedJSONEncoder(json.JSONEncoder):
    """JSON encoder with a local large-integer renderer.

    Python's standard encoder uses ``int.__repr__``, which can raise for a valid
    Geno integer when the interpreter's decimal digit safety limit is enabled.
    Supplying the integer renderer to the pure-Python encoder avoids changing
    that process-global setting (which would be unsafe in a threaded server).
    """

    def iterencode(self, value: Any, _one_shot: bool = False) -> Iterator[str]:
        markers: dict[int, Any] | None = {} if self.check_circular else None
        string_encoder = json.encoder.encode_basestring_ascii

        def float_text(number: float) -> str:
            if math.isfinite(number):
                return float.__repr__(number)
            if not self.allow_nan:
                raise ValueError(
                    "Out of range float values are not JSON compliant: " + repr(number)
                )
            if math.isnan(number):
                return "NaN"
            return "Infinity" if number > 0 else "-Infinity"

        make_iterencode = json.encoder.__dict__["_make_iterencode"]
        iterator = make_iterencode(
            markers,
            self.default,
            string_encoder,
            self.indent,
            float_text,
            self.key_separator,
            self.item_separator,
            self.sort_keys,
            self.skipkeys,
            _one_shot,
            _intstr=_bounded_json_int_text,
        )
        return cast(Iterator[str], iterator(value, 0))


def _validate_json_response_prefix(payload: Any) -> None:
    """Bound every string before encoding and reject amplified object graphs."""
    minimum_size = 0
    active_containers: set[int] = set()
    stack: list[tuple[Any, int | None]] = [(iter((payload,)), None)]
    while stack:
        iterator, container_id = stack[-1]
        try:
            value = next(iterator)
        except StopIteration:
            stack.pop()
            if container_id is not None:
                active_containers.remove(container_id)
            continue

        if isinstance(value, str):
            minimum_size += _json_ascii_string_size(value)
        elif value is None or value is True:
            minimum_size += 4
        elif value is False:
            minimum_size += 5
        elif isinstance(value, int):
            minimum_size += len(_bounded_json_int_text(value))
        elif isinstance(value, float):
            minimum_size += 1
        elif isinstance(value, dict):
            identity = id(value)
            if identity in active_containers:
                raise ValueError("Circular reference detected")
            count = len(value)
            minimum_size += 2 + (count - 1 if count else 0) + count
            active_containers.add(identity)
            stack.append((iter(_dict_json_children(value)), identity))
        elif isinstance(value, (list, tuple)):
            identity = id(value)
            if identity in active_containers:
                raise ValueError("Circular reference detected")
            count = len(value)
            minimum_size += 2 + (count - 1 if count else 0)
            active_containers.add(identity)
            stack.append((iter(value), identity))
        else:
            # JSONEncoder will report unsupported values. Count one byte so
            # walking a hostile graph remains bounded even before that error.
            minimum_size += 1

        if minimum_size > MAX_RESPONSE_BODY_BYTES:
            raise ResponseTooLarge("JSON response body exceeds configured limit")


def _bounded_json_response_body(payload: Any) -> bytes:
    """Serialize JSON while retaining at most the configured response bound."""
    _validate_json_response_prefix(payload)
    encoder = _BoundedJSONEncoder(separators=(",", ":"), allow_nan=False)
    body = bytearray()
    for chunk in encoder.iterencode(payload):
        encoded = chunk.encode("utf-8")
        if len(body) + len(encoded) > MAX_RESPONSE_BODY_BYTES:
            raise ResponseTooLarge("JSON response body exceeds configured limit")
        body.extend(encoded)
    return bytes(body)


def _json_response(
    handler: BaseHTTPRequestHandler,
    status: HTTPStatus,
    payload: dict,
    *,
    request_id: str | None = None,
    bounded: bool = True,
) -> None:
    # Compact separators: indent=2 pretty-printing inflated every response with
    # whitespace proportional to the (sandbox-bounded but still large) result,
    # needlessly amplifying bytes buffered and sent for each request.
    body = (
        _bounded_json_response_body(payload)
        if bounded
        else json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    )
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    _add_cors_headers(handler)
    _add_security_headers(handler)
    if request_id:
        handler.send_header("X-Request-Id", request_id)
    handler.end_headers()
    handler.wfile.write(body)
    handler.wfile.flush()


def _text_response(
    handler: BaseHTTPRequestHandler,
    status: HTTPStatus,
    body: str,
) -> None:
    encoded = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    _add_security_headers(handler)
    handler.end_headers()
    handler.wfile.write(encoded)
    handler.wfile.flush()


def _load_json_body(handler: BaseHTTPRequestHandler) -> dict:
    if _header_values(handler, "Transfer-Encoding"):
        raise RequestError("Transfer-Encoding is not supported")

    content_types = _header_values(handler, "Content-Type")
    if len(content_types) != 1:
        if not content_types:
            raise RequestError("missing Content-Type header")
        raise RequestError("ambiguous Content-Type header")
    content_type = content_types[0]
    media_type = content_type.partition(";")[0].strip().lower()
    if media_type != "application/json":
        raise RequestError("Content-Type must be application/json")

    content_lengths = _header_values(handler, "Content-Length")
    if not content_lengths:
        raise RequestError("missing Content-Length header")
    if len(content_lengths) != 1:
        raise RequestError("ambiguous Content-Length header")
    content_length = content_lengths[0]
    if content_length.startswith("-"):
        raise RequestError("invalid Content-Length: must not be negative")
    if content_length != "0" and (
        not content_length
        or content_length[0] not in "123456789"
        or any(character not in "0123456789" for character in content_length[1:])
    ):
        raise RequestError("invalid Content-Length header")
    limit_text = str(MAX_REQUEST_BODY_BYTES)
    if len(content_length) > len(limit_text) or (
        len(content_length) == len(limit_text) and content_length > limit_text
    ):
        raise RequestError(
            f"request body too large (max {MAX_REQUEST_BODY_BYTES} bytes)"
        )
    length = int(content_length)
    if length > MAX_REQUEST_BODY_BYTES:  # defensive if the limit changes type
        raise RequestError(
            f"request body too large ({length} bytes, max {MAX_REQUEST_BODY_BYTES})"
        )

    try:
        raw_body = handler.rfile.read(length)
    finally:
        body_complete = getattr(handler, "_body_complete", None)
        if callable(body_complete):
            body_complete()
    if len(raw_body) != length:
        raise RequestError("incomplete request body")
    try:
        body_text = raw_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RequestError("request body must be valid JSON") from exc
    _ensure_json_nesting_within_limit(body_text)
    try:
        payload = json.loads(body_text, parse_constant=_reject_json_constant)
    except ValueError as exc:
        raise RequestError("request body must be valid JSON") from exc
    except RecursionError as exc:
        # Keep the parser-specific fallback even though the explicit limit above
        # makes behavior deterministic across supported Python versions.
        raise RequestError("request body JSON is nested too deeply") from exc
    if not isinstance(payload, dict):
        raise RequestError("request body must be a JSON object")
    return payload


def _ensure_json_nesting_within_limit(body: str) -> None:
    """Reject deeply nested JSON without relying on Python recursion limits."""
    depth = 0
    in_string = False
    escaped = False
    for char in body:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_NESTING_DEPTH:
                raise RequestError("request body JSON is nested too deeply")
        elif char in "]}" and depth:
            depth -= 1


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value is not allowed: {value}")


def _coerce_modules(payload: dict) -> dict[str, str] | None:
    modules = payload.get("modules")
    if modules is None:
        return None
    if not isinstance(modules, dict):
        raise RequestError("'modules' must be an object of {name: source}")
    if len(modules) > MAX_MODULES:
        raise RequestError(f"too many modules ({len(modules)}, max {MAX_MODULES})")
    normalized: dict[str, str] = {}
    for name, source in modules.items():
        if not isinstance(name, str) or not isinstance(source, str):
            raise RequestError("'modules' entries must be string -> string")
        if not re.fullmatch(r"[A-Z][A-Za-z0-9_]*", name):
            raise RequestError(
                f"invalid module name {name!r}: expected a simple PascalCase identifier"
            )
        source_bytes = len(source.encode("utf-8"))
        if source_bytes > MAX_MODULE_SOURCE_BYTES:
            raise RequestError(
                f"module '{name}' exceeds size limit "
                f"({source_bytes} bytes, max {MAX_MODULE_SOURCE_BYTES})"
            )
        normalized[name] = source
    return normalized


def _coerce_capabilities(
    payload: dict,
    allowed_capabilities: AbstractSet[str],
    default_request_capabilities: AbstractSet[str] = frozenset(),
) -> set[str]:
    capabilities = payload.get("capabilities")
    if capabilities is None:
        return set(default_request_capabilities)
    if not isinstance(capabilities, list) or any(
        not isinstance(item, str) for item in capabilities
    ):
        raise RequestError("'capabilities' must be a list of strings")

    try:
        requested = normalize_capability_values(capabilities)
    except CapabilityParseError as exc:
        raise RequestError(str(exc)) from exc
    disallowed = requested - allowed_capabilities
    if disallowed:
        raise RequestError(
            "unsupported capabilities requested: " + ", ".join(sorted(disallowed))
        )
    return cast(set[str], requested)


def _normalize_env_allowlist_values(
    values: AbstractSet[str],
    field_name: str,
) -> frozenset[str]:
    if isinstance(values, str):
        raise ValueError(f"{field_name} must be a collection of non-empty strings")
    try:
        normalized = frozenset(values)
    except TypeError as exc:
        raise ValueError(
            f"{field_name} must be a collection of non-empty strings"
        ) from exc
    if any(not isinstance(item, str) or item == "" for item in normalized):
        raise ValueError(f"{field_name} must contain only non-empty strings")
    return normalized


def _build_run_config(
    payload: dict,
    collector: RuntimeMetricsCollector,
    allowed_capabilities: set[str],
    default_request_capabilities: set[str] | frozenset[str] = frozenset(),
    allowed_env_names: AbstractSet[str] | None = None,
    allowed_env_prefixes: AbstractSet[str] | None = None,
) -> RunConfig:
    fixed_limit_fields = _REQUEST_FIXED_RESOURCE_LIMIT_FIELDS & payload.keys()
    if fixed_limit_fields:
        joined = ", ".join(sorted(fixed_limit_fields))
        raise RequestError(
            "hosted /run clients may configure only timeout and max_steps; "
            f"operator-controlled resource limits are fixed: {joined}"
        )

    timeout = payload.get("timeout", 5.0)
    max_steps = payload.get("max_steps", DEFAULT_MAX_STEPS)
    if max_steps is None:
        max_steps = DEFAULT_MAX_STEPS
    check_examples = payload.get("check_examples", True)

    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise RequestError("'timeout' must be a positive number")
    if timeout > MAX_TIMEOUT_SECONDS:
        raise RequestError(f"'timeout' exceeds server maximum ({MAX_TIMEOUT_SECONDS}s)")
    if max_steps is not None and (
        isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps <= 0
    ):
        raise RequestError("'max_steps' must be a positive integer when provided")
    if max_steps is not None and max_steps > MAX_STEPS:
        raise RequestError(f"'max_steps' exceeds server maximum ({MAX_STEPS})")
    if not isinstance(check_examples, bool):
        raise RequestError("'check_examples' must be a boolean")

    capabilities = _coerce_capabilities(
        payload,
        allowed_capabilities,
        default_request_capabilities,
    )
    env_names = (
        frozenset() if allowed_env_names is None else frozenset(allowed_env_names)
    )
    env_prefixes = (
        frozenset() if allowed_env_prefixes is None else frozenset(allowed_env_prefixes)
    )
    if "env" in capabilities and not env_names and not env_prefixes:
        raise RequestError(
            "env capability requires GENO_ALLOWED_ENV_NAMES or "
            "GENO_ALLOWED_ENV_PREFIXES"
        )

    return RunConfig(
        timeout=float(timeout),
        max_steps=max_steps,
        check_examples=check_examples,
        modules=_coerce_modules(payload),
        capabilities=capabilities,
        env_allowed_names=env_names,
        env_allowed_prefixes=env_prefixes,
        monitoring_hook=collector.record,
    )


def _parse_run_request_payload(
    payload: dict,
    collector: RuntimeMetricsCollector,
    allowed_capabilities: set[str],
    default_request_capabilities: set[str] | frozenset[str] = frozenset(),
    allowed_env_names: AbstractSet[str] | None = None,
    allowed_env_prefixes: AbstractSet[str] | None = None,
) -> RunRequest:
    """Parse and validate the hosted ``/run`` request payload."""
    source = payload.get("source")
    filename = payload.get("filename", "<http>")
    if not isinstance(source, str):
        raise RequestError("'source' must be a string")
    if not isinstance(filename, str):
        raise RequestError("'filename' must be a string when provided")
    return RunRequest(
        source=source,
        filename=filename,
        source_bytes=len(source.encode("utf-8")),
        config=_build_run_config(
            payload,
            collector,
            allowed_capabilities,
            default_request_capabilities,
            allowed_env_names,
            allowed_env_prefixes,
        ),
    )


def _parse_constrain_request_payload(payload: dict) -> ConstrainRequest:
    """Parse and validate the hosted ``/constrain`` request payload."""
    prefix = payload.get("prefix")
    if not isinstance(prefix, str):
        raise RequestError("'prefix' must be a string")
    return ConstrainRequest(
        prefix=prefix,
        source_bytes=len(prefix.encode("utf-8")),
    )


def _serialize_run_result(result) -> dict:
    return {
        "ok": result.ok,
        "value": result.value,
        "output": result.output,
        "diagnostics": [diag.to_dict() for diag in result.diagnostics],
        "timing": {
            "total_ms": round(result.timing.total_ms, 2),
            "lex_ms": round(result.timing.lex_ms, 2),
            "parse_ms": round(result.timing.parse_ms, 2),
            "typecheck_ms": round(result.timing.typecheck_ms, 2),
            "run_ms": round(result.timing.run_ms, 2),
        },
        "steps_used": result.steps_used,
    }


def _serialize_constraint_result(result) -> dict[str, Any]:
    return cast(dict[str, Any], result.to_dict())


def _subprocess_run_config(config: RunConfig) -> RunConfig:
    """Drop process-local callbacks before sending RunConfig to a child process."""
    return replace(config, monitoring_hook=None)


def _try_set_worker_rlimit(
    resource_module: Any,
    limit_name: str,
    soft: int,
    hard: int,
) -> str | None:
    """Apply one worker rlimit when supported by the current platform."""
    limit = getattr(resource_module, limit_name, None)
    if limit is None:
        raise RuntimeError(
            f"required worker resource limit {limit_name} is unavailable"
        )
    try:
        resource_module.setrlimit(limit, (soft, hard))
    except (ValueError, OSError) as exc:
        raise RuntimeError(
            f"failed to set required worker resource limit {limit_name}: {exc}"
        ) from exc
    return limit_name


def _apply_windows_worker_resource_limits() -> tuple[str, ...]:
    """Assign the hosted worker itself to a fail-closed Windows Job Object."""
    import ctypes
    from ctypes import wintypes

    from .sandbox import ProcessSandbox, ProcessSandboxConfig

    config = ProcessSandboxConfig(
        timeout=MAX_WALL_CLOCK_SECONDS,
        max_memory_bytes=WORKER_MAX_MEMORY_BYTES or None,
        max_cpu_time=WORKER_MAX_CPU_TIME or None,
        max_file_size_bytes=WORKER_MAX_FILE_SIZE_BYTES,
        max_processes=WORKER_MAX_PROCESSES,
        strict=False,
    )
    limiter = ProcessSandbox(config)
    ctypes_api = cast(Any, ctypes)
    kernel32 = ctypes_api.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    raw_handle = kernel32.GetCurrentProcess()
    handle_value = (
        raw_handle
        if isinstance(raw_handle, int)
        else getattr(raw_handle, "value", None)
    )
    if handle_value is None:
        raise RuntimeError("could not obtain hosted worker process handle")
    current_process = type(
        "_CurrentWorkerProcess",
        (),
        {"_handle": handle_value},
    )()
    job_handle = limiter._create_windows_job(
        current_process,
        redirector_overhead=0,
    )
    if job_handle is None:
        raise RuntimeError("Windows Job Object resource limits are unavailable")

    global _WORKER_JOB_HANDLE
    _WORKER_JOB_HANDLE = job_handle
    applied = ["JOB_ACTIVE_PROCESS_LIMIT"]
    if WORKER_MAX_MEMORY_BYTES > 0:
        applied.append("JOB_MEMORY_LIMIT")
    if WORKER_MAX_CPU_TIME > 0:
        applied.append("JOB_CPU_LIMIT")
    # Hosted RunConfig never receives host callbacks, so Geno code has no file
    # write primitive on Windows. POSIX additionally applies RLIMIT_FSIZE.
    applied.append("NO_HOST_FILESYSTEM_CALLBACKS")
    return tuple(applied)


def _apply_worker_resource_limits() -> tuple[str, ...]:
    """Apply operator-controlled OS resource limits inside worker children."""
    if os.name == "nt":
        return _apply_windows_worker_resource_limits()
    try:
        import resource
    except ImportError as exc:
        raise RuntimeError(
            "required POSIX worker resource limits are unavailable"
        ) from exc

    applied: list[str] = []
    if WORKER_MAX_CPU_TIME > 0:
        cpu_limit = math.ceil(WORKER_MAX_CPU_TIME)
        name = _try_set_worker_rlimit(resource, "RLIMIT_CPU", cpu_limit, cpu_limit)
        if name is not None:
            applied.append(name)
    if WORKER_MAX_FILE_SIZE_BYTES >= 0:
        name = _try_set_worker_rlimit(
            resource,
            "RLIMIT_FSIZE",
            WORKER_MAX_FILE_SIZE_BYTES,
            WORKER_MAX_FILE_SIZE_BYTES,
        )
        if name is not None:
            applied.append(name)
    if WORKER_MAX_PROCESSES > 0:
        name = _try_set_worker_rlimit(
            resource, "RLIMIT_NPROC", WORKER_MAX_PROCESSES, WORKER_MAX_PROCESSES
        )
        if name is not None:
            applied.append(name)
    if WORKER_MAX_MEMORY_BYTES > 0:
        try:
            memory_ceiling = rlimit_as_ceiling(
                WORKER_MAX_MEMORY_BYTES,
                resource,
            )
        except (ValueError, OSError) as exc:
            raise RuntimeError(
                f"failed to prepare required worker RLIMIT_AS: {exc}"
            ) from exc
        name = _try_set_worker_rlimit(
            resource, "RLIMIT_AS", memory_ceiling, memory_ceiling
        )
        if name is not None:
            applied.append(name)
    return tuple(applied)


def _run_request_worker(
    result_conn: Connection,
    source: str,
    config: RunConfig,
    filename: str,
    runner=run,
) -> None:
    """Execute geno.run() in a child process and send back the outcome."""
    try:
        sys.dont_write_bytecode = True
        _apply_worker_resource_limits()
        result_conn.send((_WORKER_READY, None))
        result = runner(source, config=config, filename=filename)
        result_conn.send(("result", replace(result, value_raw=None)))
    except Exception as exc:  # pragma: no cover - exercised via parent contract
        try:
            result_conn.send(
                (
                    "error",
                    {
                        "type": exc.__class__.__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                )
            )
        except Exception:
            logger.debug("Failed to send child-process error payload", exc_info=True)
    finally:
        result_conn.close()


def _constrain_request_worker(
    result_conn: Connection,
    prefix: str,
    constrain=constrain_prefix,
) -> None:
    """Execute geno.constrain_prefix() in a child process and send back the result."""
    try:
        sys.dont_write_bytecode = True
        _apply_worker_resource_limits()
        result_conn.send((_WORKER_READY, None))
        result_conn.send(("result", constrain(prefix)))
    except Exception as exc:  # pragma: no cover - exercised via parent contract
        try:
            result_conn.send(
                (
                    "error",
                    {
                        "type": exc.__class__.__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                )
            )
        except Exception:
            logger.debug("Failed to send child-process error payload", exc_info=True)
    finally:
        result_conn.close()


def _stop_worker(worker: BaseProcess) -> None:
    """Terminate a worker process and wait for it to exit."""
    worker.terminate()
    worker.join(timeout=1.0)
    if worker.is_alive() and hasattr(worker, "kill"):
        worker.kill()
        worker.join(timeout=1.0)


def _worker_exit_error(worker: BaseProcess) -> tuple[str, dict[str, str]]:
    """Return a normalized error payload for a worker that exited unexpectedly."""
    return (
        "error",
        {
            "type": "WorkerExited",
            "message": f"worker exited with code {worker.exitcode}",
            "traceback": "",
        },
    )


def _recv_worker_message(
    parent_conn: Connection,
    worker: BaseProcess,
    timeout: float,
) -> tuple[str, Any] | None:
    """Receive one worker message or return None when no message arrives."""
    if not parent_conn.poll(timeout=timeout):
        return None
    try:
        return cast(tuple[str, Any], parent_conn.recv())
    except EOFError:
        worker.join(timeout=0.1)
        return _worker_exit_error(worker)


def _execute_worker_with_wall_timeout(
    target,
    args: tuple,
    wall_timeout: float,
    *,
    startup_grace: float = WORKER_STARTUP_GRACE_SECONDS,
):
    """Run a worker with separate startup and execution timeout budgets."""
    parent_conn = None
    child_conn = None
    try:
        ctx = multiprocessing.get_context("spawn")
        parent_conn, child_conn = ctx.Pipe(duplex=False)
        worker = ctx.Process(target=target, args=(child_conn, *args))
        worker.start()
    except (OSError, PermissionError) as exc:
        logger.error(
            "Failed to spawn killable worker process; refusing unsafe fallback: %s",
            exc,
        )
        if parent_conn is not None:
            parent_conn.close()
        if child_conn is not None:
            child_conn.close()
        return (
            "error",
            {
                "type": "WorkerSpawnFailed",
                "message": str(exc),
                "traceback": "",
            },
        )
    child_conn.close()
    try:
        startup_message = _recv_worker_message(
            cast(Connection, parent_conn), worker, startup_grace
        )
        if startup_message is None:
            if not worker.is_alive():
                worker.join(timeout=0.1)
                return _worker_exit_error(worker)
            _stop_worker(worker)
            return "startup_timeout", None
        if startup_message[0] != _WORKER_READY:
            worker.join(timeout=1.0)
            if worker.is_alive():
                _stop_worker(worker)
            return startup_message

        result = _recv_worker_message(
            cast(Connection, parent_conn), worker, wall_timeout
        )
        if result is not None:
            worker.join(timeout=1.0)
            if worker.is_alive():
                _stop_worker(worker)
            return result

        if not worker.is_alive():
            if parent_conn.poll(timeout=0.1):
                try:
                    return parent_conn.recv()
                except EOFError:
                    pass
            worker.join(timeout=0.1)
            return _worker_exit_error(worker)

        if worker.is_alive():
            _stop_worker(worker)
            return "timeout", None

        worker.join(timeout=0.1)
        return _worker_exit_error(worker)
    finally:
        if parent_conn is not None:
            parent_conn.close()


def _execute_run_with_wall_timeout(
    source: str,
    config: RunConfig,
    filename: str,
    wall_timeout: float,
    runner=run,
):
    """Run geno.run() in a killable child process with a hard wall-clock timeout."""
    return _execute_worker_with_wall_timeout(
        _run_request_worker,
        (source, _subprocess_run_config(config), filename, runner),
        wall_timeout,
    )


def _execute_constrain_with_wall_timeout(
    prefix: str,
    wall_timeout: float,
    constrain=constrain_prefix,
):
    """Run geno.constrain_prefix() in a killable child process with a hard timeout."""
    return _execute_worker_with_wall_timeout(
        _constrain_request_worker,
        (prefix, constrain),
        wall_timeout,
    )


def create_handler(
    collector: RuntimeMetricsCollector,
    allowed_capabilities: set[str] | None = None,
    *,
    default_request_capabilities: set[str] | None = None,
    api_key: str | None = None,
    allowed_env_names: AbstractSet[str] | None = None,
    allowed_env_prefixes: AbstractSet[str] | None = None,
    require_auth_for_metrics: bool | None = _REQUIRE_AUTH_FOR_METRICS,
    require_auth_for_playground: bool = _REQUIRE_AUTH_FOR_PLAYGROUND,
    rate_limit_requests: int = _RATE_LIMIT_REQUESTS,
    rate_limit_window_seconds: float = _RATE_LIMIT_WINDOW_SECONDS,
    rate_limit_max_buckets: int = _RATE_LIMIT_MAX_BUCKETS,
    trusted_proxy: str | None = _TRUSTED_PROXY,
):
    """Create a request handler bound to the provided collector.

    Parameters
    ----------
    api_key:
        When set, POST /run and POST /constrain require
        ``Authorization: Bearer <key>`` or ``X-API-Key: <key>``. When ``None``
        (default), authentication is disabled and any client may call them.
    default_request_capabilities:
        Capabilities granted to a ``/run`` request that omits the
        ``capabilities`` field. Defaults to an empty set, so the server's
        allowed capabilities act only as a ceiling unless callers opt in.
    allowed_env_names:
        Exact environment variable names that hosted ``/run`` may expose when
        the ``env`` capability is granted. Defaults to
        ``GENO_ALLOWED_ENV_NAMES``.
    allowed_env_prefixes:
        Environment variable name prefixes that hosted ``/run`` may expose when
        the ``env`` capability is granted. Sensitive names such as
        ``GENO_API_KEY`` remain denied unless exactly listed above. Defaults to
        ``GENO_ALLOWED_ENV_PREFIXES``.
    require_auth_for_metrics:
        When true, ``GET /metrics`` requires API key authentication. When
        false, metrics remain public. When ``None`` (the default unless
        ``GENO_REQUIRE_AUTH_FOR_METRICS`` is set), metrics require auth when
        ``api_key`` is configured and stay public for unauthenticated local
        servers.
    require_auth_for_playground:
        When true, ``GET /`` and ``GET /playground`` require API key
        authentication. Defaults to ``GENO_REQUIRE_AUTH_FOR_PLAYGROUND``.
    rate_limit_requests:
        Maximum number of POST /run or POST /constrain requests allowed per
        client IP within
        ``rate_limit_window_seconds``.  Set to 0 to disable rate limiting.
    rate_limit_window_seconds:
        Sliding-window duration for per-IP rate limiting.
    rate_limit_max_buckets:
        Maximum number of distinct active client buckets retained by the
        limiter before least-recently-used buckets are evicted.
    trusted_proxy:
        TCP peer address of a trusted reverse proxy (e.g. ``"127.0.0.1"``).
        When the connection comes from this address, the real client IP is
        read from the rightmost syntactically valid, non-proxy
        ``X-Forwarded-For`` entry and a single exact ``X-Forwarded-Proto``
        value of ``http`` or ``https`` defines the public scheme for
        same-origin checks. Invalid client-IP entries are ignored; duplicate
        or malformed forwarded-scheme values fail closed. When ``None``
        (default), the TCP peer address and connection scheme are used
        directly. Only set this when you control the proxy and it overwrites
        both headers; forwarded headers can otherwise be spoofed by clients.

    Production deployments should place a reverse proxy (nginx, Caddy)
    in front of this server for TLS termination and connection management.
    """

    if api_key is not None and not api_key.strip():
        raise ValueError("api_key must not be empty or whitespace")

    if (
        isinstance(rate_limit_requests, bool)
        or not isinstance(rate_limit_requests, int)
        or rate_limit_requests < 0
    ):
        raise ValueError("rate_limit_requests must be a non-negative integer")
    if require_auth_for_metrics is not None and not isinstance(
        require_auth_for_metrics, bool
    ):
        raise ValueError("require_auth_for_metrics must be a boolean or None")
    if not isinstance(require_auth_for_playground, bool):
        raise ValueError("require_auth_for_playground must be a boolean")
    metrics_auth_required = (
        api_key is not None
        if require_auth_for_metrics is None
        else require_auth_for_metrics
    )

    allowed = (
        set(DEFAULT_ALLOWED_CAPABILITIES)
        if allowed_capabilities is None
        else normalize_capability_values(allowed_capabilities)
    )
    default_grants = (
        set()
        if default_request_capabilities is None
        else normalize_capability_values(default_request_capabilities)
    )
    if not default_grants <= allowed:
        disallowed_defaults = default_grants - allowed
        raise ValueError(
            "default_request_capabilities must be a subset of allowed_capabilities: "
            + ", ".join(sorted(disallowed_defaults))
        )
    env_names = _normalize_env_allowlist_values(
        _ALLOWED_ENV_NAMES if allowed_env_names is None else allowed_env_names,
        "allowed_env_names",
    )
    env_prefixes = _normalize_env_allowlist_values(
        _ALLOWED_ENV_PREFIXES if allowed_env_prefixes is None else allowed_env_prefixes,
        "allowed_env_prefixes",
    )
    _request_semaphore = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
    _rate_limiter: _RateLimiter | None = (
        _RateLimiter(
            rate_limit_requests,
            rate_limit_window_seconds,
            max_buckets=rate_limit_max_buckets,
        )
        if rate_limit_requests > 0
        else None
    )

    def _client_ip(handler: BaseHTTPRequestHandler) -> str:
        """Return the effective client IP, honouring X-Forwarded-For when trusted.

        Uses the rightmost non-trusted entry instead of the leftmost, because
        the leftmost value can be spoofed by the client while the rightmost
        value is the one actually appended by the trusted proxy. Invalid XFF
        entries are ignored instead of becoming rate-limit or log identities.
        """
        peer = handler.client_address[0]
        if trusted_proxy is not None and peer == trusted_proxy:
            forwarded_values = _header_values(handler, "X-Forwarded-For")
            if forwarded_values:
                forwarded_for = ",".join(forwarded_values)
                client_ip = _client_ip_from_x_forwarded_for(
                    forwarded_for, trusted_proxy
                )
                if client_ip is not None:
                    return client_ip
        return peer

    def _check_auth(handler: BaseHTTPRequestHandler) -> bool:
        """Return True if the request passes auth, or return False if not."""
        if api_key is None:
            return True
        expected = api_key.encode()
        auth_values = _header_values(handler, "Authorization")
        if len(auth_values) == 1 and auth_values[0].startswith("Bearer "):
            auth_header = auth_values[0]
            provided = auth_header[len("Bearer ") :].encode()
            if secrets.compare_digest(provided, expected):
                return True
        key_values = _header_values(handler, "X-API-Key")
        if len(key_values) != 1:
            return False
        x_api_key = key_values[0].encode()
        return bool(x_api_key and secrets.compare_digest(x_api_key, expected))

    def _check_get_auth(
        handler: BaseHTTPRequestHandler,
        *,
        required: bool,
    ) -> bool:
        if not required:
            return True
        if api_key is None:
            _json_response(
                handler,
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "error": (
                        "authentication is required for this endpoint, but "
                        "GENO_API_KEY is not configured"
                    )
                },
            )
            return False
        if _check_auth(handler):
            return True
        _json_response(
            handler,
            HTTPStatus.UNAUTHORIZED,
            {"error": "authentication required"},
        )
        return False

    def _health_response_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        report = cast(dict[str, Any], collector.health_report().to_dict())
        if api_key is None or _check_auth(handler):
            return report
        return {"status": report.get("status", "unknown")}

    def _check_host(handler: BaseHTTPRequestHandler) -> bool:
        allowed_hosts = cast(
            AbstractSet[_HostPort],
            getattr(handler.server, "allowed_hosts", frozenset()),
        )
        host_values = _header_values(handler, "Host")
        if len(host_values) != 1:
            _reject_bad_host(handler)
            return False
        host_header = host_values[0]
        if _host_allowed(host_header, allowed_hosts):
            return True
        # Accept a loopback Host header, but only from a genuinely local (loopback)
        # peer such as a container health probe. This keeps healthchecks working on
        # a non-loopback bind (0.0.0.0) without letting an external client bypass
        # GENO_ALLOWED_HOSTS by sending "Host: 127.0.0.1" — the TCP peer address is
        # unspoofable, unlike the Host header.
        if (
            handler.path == "/healthz"
            and _peer_is_loopback(handler)
            and _host_header_is_loopback(host_header)
        ):
            return True
        _reject_bad_host(handler)
        return False

    class MonitoringHandler(BaseHTTPRequestHandler):
        server_version = "GenoMonitoringAdapter/1.0"

        def setup(self) -> None:
            super().setup()
            # The inactivity timeout and absolute deadline jointly prevent a
            # client from retaining a thread while dribbling headers or a body.
            self.request.settimeout(_REQUEST_TIMEOUT_SECONDS)
            self._cors_allowed_origins = _CORS_ALLOWED_ORIGINS
            self._trusted_proxy = trusted_proxy
            self._request_deadline = threading.Timer(
                _REQUEST_TIMEOUT_SECONDS, self._expire_request_read
            )
            self._request_deadline.daemon = True
            self._request_deadline.start()

        def _expire_request_read(self) -> None:
            try:
                self.request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        def _headers_complete(self, *, expect_body: bool = False) -> None:
            if not expect_body:
                self._request_deadline.cancel()

        def _body_complete(self) -> None:
            self._request_deadline.cancel()

        def finish(self) -> None:
            try:
                super().finish()
            finally:
                deadline = getattr(self, "_request_deadline", None)
                if deadline is not None:
                    deadline.cancel()

        def do_OPTIONS(self) -> None:
            """Handle CORS preflight requests."""
            self._headers_complete()
            if not _check_host(self):
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            _add_cors_headers(self)
            _add_security_headers(self)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            self._headers_complete()
            if not _check_host(self):
                return
            if self.path in {"", "/", "/playground"}:
                if not _check_get_auth(
                    self,
                    required=require_auth_for_playground,
                ):
                    return
                self._serve_playground()
                return
            if self.path == "/healthz":
                _json_response(
                    self,
                    HTTPStatus.OK,
                    _health_response_body(self),
                )
                return
            if self.path == "/metrics":
                if not _check_get_auth(self, required=metrics_auth_required):
                    return
                _text_response(
                    self,
                    HTTPStatus.OK,
                    collector.snapshot().to_prometheus_text(),
                )
                return
            _json_response(
                self,
                HTTPStatus.NOT_FOUND,
                {"error": f"unknown endpoint: {self.path}"},
            )

        def _serve_playground(self) -> None:
            """Serve the built-in playground HTML page."""
            html = _playground_html()
            body = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            _add_security_headers(self)
            # The page is a self-contained bundle with inline <script>/<style>
            # and inline event handlers, so 'unsafe-inline' is required; the
            # rest of the policy still blocks external/object/frame vectors and
            # confines fetch to the same origin (the /run endpoint).
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; "
                "script-src 'unsafe-inline'; "
                "style-src 'unsafe-inline'; "
                "connect-src 'self'; "
                "img-src 'self' data:; "
                "base-uri 'none'; "
                "form-action 'none'; "
                "frame-ancestors 'none'",
            )
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            self._headers_complete(expect_body=True)
            if not _check_host(self):
                return
            if self.path not in {"/run", "/constrain"}:
                _json_response(
                    self,
                    HTTPStatus.NOT_FOUND,
                    {"error": f"unknown endpoint: {self.path}"},
                )
                return

            if not _request_origin_allowed(self):
                _json_response(
                    self,
                    HTTPStatus.FORBIDDEN,
                    {"error": "cross-origin request is not allowed"},
                )
                return

            request_id = str(uuid.uuid4())
            started_at = time.monotonic()
            client_ip = _client_ip(self)
            source_bytes = 0
            outcome = "error"
            http_status = HTTPStatus.INTERNAL_SERVER_ERROR
            semaphore_acquired = False
            try:
                # --- Per-IP rate limiting ---
                if _rate_limiter is not None and not _rate_limiter.is_allowed(
                    client_ip
                ):
                    http_status = HTTPStatus.TOO_MANY_REQUESTS
                    outcome = "rate_limited"
                    _json_response(
                        self,
                        http_status,
                        {"error": "rate limit exceeded"},
                        request_id=request_id,
                    )
                    logger.warning(
                        "rate_limited request_id=%s client_ip=%s",
                        request_id,
                        client_ip,
                    )
                    return

                # --- Authentication ---
                if not _check_auth(self):
                    http_status = HTTPStatus.UNAUTHORIZED
                    outcome = "auth_failed"
                    _json_response(
                        self,
                        http_status,
                        {"error": "authentication required"},
                        request_id=request_id,
                    )
                    logger.warning(
                        "auth_failed request_id=%s client_ip=%s",
                        request_id,
                        client_ip,
                    )
                    return

                # --- Concurrency cap ---
                if not _request_semaphore.acquire(blocking=False):
                    http_status = HTTPStatus.SERVICE_UNAVAILABLE
                    outcome = "concurrency_rejected"
                    _json_response(
                        self,
                        http_status,
                        {"error": "Too many concurrent requests"},
                        request_id=request_id,
                    )
                    return
                semaphore_acquired = True

                try:
                    payload = _load_json_body(self)
                    if self.path == "/run":
                        run_request = _parse_run_request_payload(
                            payload,
                            collector,
                            allowed,
                            default_grants,
                            env_names,
                            env_prefixes,
                        )
                        source_bytes = run_request.source_bytes
                    else:
                        constrain_request = _parse_constrain_request_payload(payload)
                        source_bytes = constrain_request.source_bytes
                except RequestError as exc:
                    http_status = HTTPStatus.BAD_REQUEST
                    outcome = "bad_request"
                    _json_response(
                        self,
                        http_status,
                        {"error": str(exc)},
                        request_id=request_id,
                    )
                    return

                try:
                    if self.path == "/constrain":
                        constrain_wall_timeout = min(
                            CONSTRAIN_WALL_CLOCK_SECONDS,
                            MAX_WALL_CLOCK_SECONDS,
                        )
                        constrain_started_at = time.monotonic()
                        status, payload = _execute_constrain_with_wall_timeout(
                            constrain_request.prefix, constrain_wall_timeout
                        )
                        constrain_elapsed_ms = (
                            time.monotonic() - constrain_started_at
                        ) * 1000
                        if status == "result":
                            result = payload
                            collector.record_constrain_result(
                                valid=result.valid,
                                wall_time_ms=constrain_elapsed_ms,
                            )
                            http_status = HTTPStatus.OK
                            outcome = (
                                "constrain_ok" if result.valid else "constrain_invalid"
                            )
                            _json_response(
                                self,
                                http_status,
                                _serialize_constraint_result(result),
                                request_id=request_id,
                                bounded=True,
                            )
                        elif status == "timeout":
                            collector.record_constrain_result(
                                valid=None,
                                wall_time_ms=constrain_elapsed_ms,
                            )
                            http_status = HTTPStatus.GATEWAY_TIMEOUT
                            outcome = "constrain_timeout"
                            _json_response(
                                self,
                                http_status,
                                {
                                    "error": (
                                        f"Constraint timeout "
                                        f"({constrain_wall_timeout:.1f}s)"
                                    )
                                },
                                request_id=request_id,
                            )
                        elif status == "startup_timeout":
                            collector.record_constrain_result(
                                valid=None,
                                wall_time_ms=constrain_elapsed_ms,
                            )
                            http_status = HTTPStatus.GATEWAY_TIMEOUT
                            outcome = "constrain_timeout"
                            _json_response(
                                self,
                                http_status,
                                {
                                    "error": (
                                        "Worker startup timeout "
                                        f"({WORKER_STARTUP_GRACE_SECONDS:.1f}s)"
                                    )
                                },
                                request_id=request_id,
                            )
                        else:
                            logger.error(
                                "Unhandled child-process error in /constrain "
                                "request_id=%s type=%s message=%s\n%s",
                                request_id,
                                payload.get("type"),
                                payload.get("message"),
                                payload.get("traceback"),
                            )
                            http_status = HTTPStatus.INTERNAL_SERVER_ERROR
                            outcome = "internal_error"
                            _json_response(
                                self,
                                http_status,
                                {"error": "internal server error"},
                                request_id=request_id,
                            )
                    else:
                        # Run api.run() inside a child process with a wall-clock
                        # budget that covers lex+parse+typecheck+execute. This
                        # provides hard cancellation for pathological parsing,
                        # type-checking, or blocked runtime work that
                        # cooperative timeouts cannot stop.
                        wall_timeout = min(
                            (run_request.config.timeout or 5.0) * 3,
                            MAX_WALL_CLOCK_SECONDS,
                        )
                        status, payload = _execute_run_with_wall_timeout(
                            run_request.source,
                            run_request.config,
                            run_request.filename,
                            wall_timeout,
                        )
                        if status == "result":
                            result = payload
                            collector.record_run_result(result)
                            http_status = (
                                HTTPStatus.OK if result.ok else HTTPStatus.BAD_REQUEST
                            )
                            outcome = "ok" if result.ok else "run_error"
                            _json_response(
                                self,
                                http_status,
                                _serialize_run_result(result),
                                request_id=request_id,
                                bounded=True,
                            )
                        elif status == "timeout":
                            collector.record(
                                RunMetrics(
                                    outcome=RunOutcome.TIMEOUT,
                                    ok=False,
                                    wall_time_ms=wall_timeout * 1000,
                                    steps_used=0,
                                )
                            )
                            http_status = HTTPStatus.GATEWAY_TIMEOUT
                            outcome = "wall_timeout"
                            _json_response(
                                self,
                                http_status,
                                {"error": f"Wall-clock timeout ({wall_timeout:.1f}s)"},
                                request_id=request_id,
                            )
                        elif status == "startup_timeout":
                            collector.record(
                                RunMetrics(
                                    outcome=RunOutcome.TIMEOUT,
                                    ok=False,
                                    wall_time_ms=WORKER_STARTUP_GRACE_SECONDS * 1000,
                                    steps_used=0,
                                )
                            )
                            http_status = HTTPStatus.GATEWAY_TIMEOUT
                            outcome = "wall_timeout"
                            _json_response(
                                self,
                                http_status,
                                {
                                    "error": (
                                        "Worker startup timeout "
                                        f"({WORKER_STARTUP_GRACE_SECONDS:.1f}s)"
                                    )
                                },
                                request_id=request_id,
                            )
                        else:
                            logger.error(
                                "Unhandled child-process error in /run request_id=%s "
                                "type=%s message=%s\n%s",
                                request_id,
                                payload.get("type"),
                                payload.get("message"),
                                payload.get("traceback"),
                            )
                            http_status = HTTPStatus.INTERNAL_SERVER_ERROR
                            outcome = "internal_error"
                            _json_response(
                                self,
                                http_status,
                                {"error": "internal server error"},
                                request_id=request_id,
                            )
                except ResponseTooLarge:
                    logger.warning(
                        "Response exceeded configured limit request_id=%s path=%s",
                        request_id,
                        self.path,
                    )
                    http_status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                    outcome = "response_too_large"
                    _json_response(
                        self,
                        http_status,
                        {"error": "response too large"},
                        request_id=request_id,
                        bounded=False,
                    )
                except Exception:
                    logger.exception(
                        "Unhandled error in %s handler request_id=%s",
                        self.path,
                        request_id,
                    )
                    http_status = HTTPStatus.INTERNAL_SERVER_ERROR
                    outcome = "internal_error"
                    _json_response(
                        self,
                        http_status,
                        {"error": "internal server error"},
                        request_id=request_id,
                    )
            finally:
                if semaphore_acquired:
                    _request_semaphore.release()
                collector.record_http_post_request(
                    endpoint=self.path,
                    status=http_status.value,
                    outcome=outcome,
                )
                duration_ms = (time.monotonic() - started_at) * 1000
                logger.info(
                    "request_id=%s client_ip=%s status=%d duration_ms=%.2f "
                    "source_bytes=%d outcome=%s",
                    request_id,
                    client_ip,
                    http_status.value,
                    duration_ms,
                    source_bytes,
                    outcome,
                )

        def log_message(self, format: str, *args) -> None:
            return

    return MonitoringHandler


class _BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server with admission control before thread creation."""

    daemon_threads = True

    def __init__(
        self,
        *args: Any,
        max_connections: int = MAX_CONNECTIONS,
        **kwargs: Any,
    ) -> None:
        self._connection_slots = _CONNECTION_SEMAPHORE_FACTORY(max_connections)
        super().__init__(*args, **kwargs)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._connection_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._connection_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connection_slots.release()


def create_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    service: str = "geno-api",
    revision: str | None = None,
    allowed_capabilities: set[str] | None = None,
    default_request_capabilities: set[str] | None = None,
    api_key: str | None = None,
    allowed_hosts: AbstractSet[str] | None = None,
    allowed_env_names: AbstractSet[str] | None = None,
    allowed_env_prefixes: AbstractSet[str] | None = None,
    require_auth_for_metrics: bool | None = _REQUIRE_AUTH_FOR_METRICS,
    require_auth_for_playground: bool = _REQUIRE_AUTH_FOR_PLAYGROUND,
    rate_limit_requests: int = _RATE_LIMIT_REQUESTS,
    rate_limit_window_seconds: float = _RATE_LIMIT_WINDOW_SECONDS,
    rate_limit_max_buckets: int = _RATE_LIMIT_MAX_BUCKETS,
    trusted_proxy: str | None = _TRUSTED_PROXY,
    allow_insecure: bool = False,
    bind_and_activate: bool = True,
    startup_errors: list[str] | None = None,
) -> ThreadingHTTPServer:
    """Create the hosted Geno HTTP server."""

    configured_allowed_hosts = (
        _ALLOWED_HOSTS if allowed_hosts is None else allowed_hosts
    )
    if allow_insecure and allowed_hosts is None and not configured_allowed_hosts:
        # --allow-insecure is an explicit opt-out. Make that opt-out usable when
        # no environment policy was supplied, while preserving every explicit
        # programmatic allow-list (including an intentionally empty one).
        configured_allowed_hosts = frozenset({"*"})
    _enforce_secure_bind(
        host,
        api_key,
        allow_insecure=allow_insecure,
        allowed_hosts=configured_allowed_hosts,
    )

    collector = RuntimeMetricsCollector(service=service, revision=revision)
    if startup_errors is None and bind_and_activate:
        startup_errors = _run_startup_checks()
    if startup_errors:
        collector.record_startup_errors(startup_errors)
    server = _BoundedThreadingHTTPServer(
        (host, port),
        create_handler(
            collector,
            allowed_capabilities=allowed_capabilities,
            default_request_capabilities=default_request_capabilities,
            api_key=api_key,
            allowed_env_names=allowed_env_names,
            allowed_env_prefixes=allowed_env_prefixes,
            require_auth_for_metrics=require_auth_for_metrics,
            require_auth_for_playground=require_auth_for_playground,
            rate_limit_requests=rate_limit_requests,
            rate_limit_window_seconds=rate_limit_window_seconds,
            rate_limit_max_buckets=rate_limit_max_buckets,
            trusted_proxy=trusted_proxy,
        ),
        bind_and_activate=bind_and_activate,
    )
    bound_host, bound_port = server.server_address[:2]
    server.allowed_hosts = _build_allowed_host_set(  # type: ignore[attr-defined]
        str(bound_host),
        int(bound_port),
        configured_allowed_hosts,
    )
    server.collector = collector  # type: ignore[attr-defined]
    return server


def _configure_logging() -> None:
    """Attach a stderr handler to the ``geno`` logger namespace.

    Called from the hosted-runtime executable entry points (``server.main`` and
    ``geno.cli.serve.serve_runtime``) so that the structured access log, startup
    audit lines, and security warnings the server already emits actually reach an
    operator. Without this the ``geno`` loggers have no handler, so INFO records
    are dropped and WARNING/ERROR records surface only as bare, timestamp-less
    lines via Python's last-resort handler.

    Honors ``GENO_LOG_LEVEL`` (default ``INFO``). Scoped to the ``geno`` namespace
    so it never touches the root logger, and a no-op when a handler is already
    present so an embedder that configured logging (or a second entry-point call)
    is respected. ``propagate`` is left enabled so ``caplog`` and parent handlers
    still observe records.
    """
    geno_logger = logging.getLogger("geno")
    if geno_logger.handlers:
        return
    level_name = os.environ.get("GENO_LOG_LEVEL", "INFO").strip().upper()
    level = logging.getLevelName(level_name or "INFO")
    if not isinstance(level, int):
        level = logging.INFO
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    geno_logger.addHandler(handler)
    geno_logger.setLevel(level)


def serve_forever(
    host: str = "127.0.0.1",
    port: int = 8000,
    *,
    service: str = "geno-api",
    revision: str | None = None,
    allowed_capabilities: set[str] | None = None,
    default_request_capabilities: set[str] | None = None,
    api_key: str | None = None,
    allowed_hosts: AbstractSet[str] | None = None,
    allowed_env_names: AbstractSet[str] | None = None,
    allowed_env_prefixes: AbstractSet[str] | None = None,
    require_auth_for_metrics: bool | None = _REQUIRE_AUTH_FOR_METRICS,
    require_auth_for_playground: bool = _REQUIRE_AUTH_FOR_PLAYGROUND,
    rate_limit_requests: int = _RATE_LIMIT_REQUESTS,
    rate_limit_window_seconds: float = _RATE_LIMIT_WINDOW_SECONDS,
    rate_limit_max_buckets: int = _RATE_LIMIT_MAX_BUCKETS,
    trusted_proxy: str | None = _TRUSTED_PROXY,
    allow_insecure: bool = False,
) -> None:
    """Run the hosted Geno HTTP server until interrupted."""

    _enforce_secure_bind(
        host,
        api_key,
        allow_insecure=allow_insecure,
        allowed_hosts=_ALLOWED_HOSTS if allowed_hosts is None else allowed_hosts,
    )

    errors = _run_startup_checks()
    if errors:
        if os.environ.get("GENO_SKIP_STARTUP_CHECKS") == "1":
            for err in errors:
                logger.warning(
                    "Startup check failed but GENO_SKIP_STARTUP_CHECKS=1 "
                    "is set — continuing: %s",
                    err,
                )
        else:
            for err in errors:
                logger.error("Startup check failed: %s", err)
            raise SystemExit(
                "Refusing to start: startup checks failed. "
                "Fix the issues above or set GENO_SKIP_STARTUP_CHECKS=1 to override."
            )
    else:
        logger.info("Startup checks passed")

    if api_key:
        logger.info("API key authentication enabled")
    else:
        logger.warning(
            "No GENO_API_KEY configured — /run and /constrain are "
            "unauthenticated. Set GENO_API_KEY or place an authenticating "
            "reverse proxy in front."
        )

    server = create_server(
        host,
        port,
        service=service,
        revision=revision,
        allowed_capabilities=allowed_capabilities,
        default_request_capabilities=default_request_capabilities,
        api_key=api_key,
        allowed_hosts=allowed_hosts,
        allowed_env_names=allowed_env_names,
        allowed_env_prefixes=allowed_env_prefixes,
        require_auth_for_metrics=require_auth_for_metrics,
        require_auth_for_playground=require_auth_for_playground,
        rate_limit_requests=rate_limit_requests,
        rate_limit_window_seconds=rate_limit_window_seconds,
        rate_limit_max_buckets=rate_limit_max_buckets,
        trusted_proxy=trusted_proxy,
        allow_insecure=allow_insecure,
        startup_errors=errors,
    )
    logger.info("Serving Geno runtime on http://%s:%s", host, port)

    def _graceful_shutdown(signum: int, _frame: object) -> None:
        logger.info(
            "Received %s, shutting down gracefully", signal.Signals(signum).name
        )
        # server.shutdown() blocks until serve_forever() returns, so it cannot run
        # in this handler (which executes on the main thread inside serve_forever).
        threading.Thread(target=server.shutdown, daemon=True).start()

    # Handle SIGTERM so `docker stop` (and orchestrators) shut the server down
    # cleanly — draining in-flight requests via server_close() — instead of being
    # ignored (as PID 1) until the 10s grace period elapses and SIGKILL lands.
    # signal.signal only works on the main thread; skip it elsewhere (embedders).
    previous_sigterm: Any = None
    installed_sigterm = False
    try:
        previous_sigterm = signal.signal(signal.SIGTERM, _graceful_shutdown)
        installed_sigterm = True
    except ValueError:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if installed_sigterm:
            signal.signal(signal.SIGTERM, previous_sigterm)
        server.server_close()


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for the hosted Geno HTTP server."""

    _configure_logging()
    parser = argparse.ArgumentParser(
        description=(
            "Run the Geno hosted runtime with /healthz, /metrics, /run, and /constrain"
        ),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--service", default=os.getenv("GENO_SERVICE", "geno-api"))
    parser.add_argument("--revision", default=os.getenv("GENO_REVISION"))
    parser.add_argument(
        "--allow-capability",
        action="append",
        dest="capabilities",
        help=(
            "Capability allowed on POST /run. Repeatable. "
            "Default: print, clock, random."
        ),
    )
    parser.add_argument(
        "--allow-insecure",
        action="store_true",
        default=False,
        help="Allow non-loopback binding without GENO_API_KEY (unsafe).",
    )
    args = parser.parse_args(argv)

    if not is_supported_python():
        logger.error("%s", unsupported_python_message())
        sys.exit(1)

    try:
        allowed_capabilities = (
            normalize_capability_values(args.capabilities, allow_comma=True)
            if args.capabilities
            else set(DEFAULT_ALLOWED_CAPABILITIES)
        )
    except CapabilityParseError as exc:
        parser.error(str(exc))
    api_key = os.environ.get("GENO_API_KEY") or None
    trusted_proxy = os.environ.get("GENO_TRUSTED_PROXY") or None
    _enforce_secure_bind(
        args.host,
        api_key,
        allow_insecure=args.allow_insecure,
    )
    serve_forever(
        args.host,
        args.port,
        service=args.service,
        revision=args.revision,
        allowed_capabilities=allowed_capabilities,
        api_key=api_key,
        trusted_proxy=trusted_proxy,
        allow_insecure=args.allow_insecure,
    )


__all__ = [
    "DEFAULT_ALLOWED_CAPABILITIES",
    "DEFAULT_MAX_STEPS",
    "MAX_JSON_NESTING_DEPTH",
    "MAX_MODULES",
    "MAX_MODULE_SOURCE_BYTES",
    "MAX_REQUEST_BODY_BYTES",
    "MAX_RESPONSE_BODY_BYTES",
    "MAX_STEPS",
    "MAX_TIMEOUT_SECONDS",
    "RequestError",
    "create_handler",
    "create_server",
    "main",
    "serve_forever",
]
