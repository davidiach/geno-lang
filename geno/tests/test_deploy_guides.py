"""
Smoke tests for deploy guide commands.

Validates that the CLI commands referenced in docs/deploy/*.md
actually work as documented, plus artifact quality tests for
source maps, .d.ts output, and ESM interop (#227).
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from geno.tests._script_runner import run_node_code

_REPO_ROOT = Path(__file__).resolve().parents[2]


_CLI_SOURCE = (
    "func greet(name: String) -> String\n"
    '    example "World" -> "Hello, World!"\n'
    '    return "Hello, " + name + "!"\n'
    "end func\n"
    "\n"
    '@untested("entry point")\n'
    "func main() -> String\n"
    '    return greet("World")\n'
    "end func\n"
)

_BROWSER_SOURCE = (
    "type Model = Model(count: Int)\n"
    "\n"
    '@untested("lifecycle")\n'
    "func init() -> Model\n"
    "    return Model(0)\n"
    "end func\n"
    "\n"
    '@untested("lifecycle")\n'
    "func update(model: Model, dt: Float) -> Model\n"
    "    return model\n"
    "end func\n"
    "\n"
    '@untested("rendering")\n'
    "func render(model: Model) -> Unit\n"
    "    return ()\n"
    "end func\n"
)

_BROWSER_CAP_SOURCE = (
    '@untested("browser capability bootstrap")\n'
    "func main() -> Int\n"
    "    return random_int(min: 1, max: 1)\n"
    "end func\n"
)

_BROWSER_SOURCE_MAP_SOURCE = (
    "func double(x: Int) -> Int\n"
    "    example 2 -> 4\n"
    "    return x * 2\n"
    "end func\n"
    "\n"
    '@untested("entry point")\n'
    "func main() -> Int\n"
    "    return double(21)\n"
    "end func\n"
)


# Multi-function source used by artifact quality tests (#227).
_MULTI_FN_SOURCE = (
    "type Color = Red | Green | Blue\n"
    "\n"
    "func color_name(c: Color) -> String\n"
    '    example Red -> "red"\n'
    '    example Green -> "green"\n'
    "    match c with\n"
    '        | Red -> return "red"\n'
    '        | Green -> return "green"\n'
    '        | Blue -> return "blue"\n'
    "    end match\n"
    "end func\n"
    "\n"
    "func sum_two(a: Int, b: Int) -> Int\n"
    "    example 1, 2 -> 3\n"
    "    return a + b\n"
    "end func\n"
    "\n"
    "func double(n: Int) -> Int\n"
    "    example 5 -> 10\n"
    "    return n * 2\n"
    "end func\n"
)


class TestCliDeployGuide:
    """Smoke tests for docs/deploy/cli.md commands."""

    def test_compile_to_python(self, tmp_path):
        """geno compile Main.geno -o app.py"""
        (tmp_path / "Main.geno").write_text(_CLI_SOURCE)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "compile",
                str(tmp_path / "Main.geno"),
                "-o",
                str(tmp_path / "app.py"),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "app.py").exists()

    def test_compiled_python_runs(self, tmp_path):
        """python3 app.py"""
        (tmp_path / "Main.geno").write_text(_CLI_SOURCE)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "compile",
                str(tmp_path / "Main.geno"),
                "-o",
                str(tmp_path / "app.py"),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        result = subprocess.run(
            [sys.executable, str(tmp_path / "app.py")],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr

    def test_compile_to_js(self, tmp_path):
        """geno compile Main.geno --target js -o app.js"""
        (tmp_path / "Main.geno").write_text(_CLI_SOURCE)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "compile",
                str(tmp_path / "Main.geno"),
                "--target",
                "js",
                "-o",
                str(tmp_path / "app.js"),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "app.js").exists()

    def test_compile_esm(self, tmp_path):
        """geno compile Main.geno --target js --esm -o app.mjs"""
        (tmp_path / "Main.geno").write_text(_CLI_SOURCE)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "compile",
                str(tmp_path / "Main.geno"),
                "--target",
                "js",
                "--esm",
                "-o",
                str(tmp_path / "app.mjs"),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "app.mjs").exists()


class TestBrowserDeployGuide:
    """Smoke tests for docs/deploy/browser.md commands."""

    def test_build_dist_directory(self, tmp_path):
        """geno build Main.geno -o dist/"""
        (tmp_path / "Main.geno").write_text(_BROWSER_SOURCE)
        dist = tmp_path / "dist"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "build",
                str(tmp_path / "Main.geno"),
                "-o",
                str(dist),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert (dist / "index.html").exists()
        assert (dist / "app.js").exists()
        assert not (dist / "app.js.map").exists()

    def test_dist_build_rejects_injected_canvas_dimension(self, tmp_path):
        """Directory browser builds validate dimensions before HTML output."""
        from typing import Any

        from geno.cli.build import build_app

        (tmp_path / "Main.geno").write_text(_BROWSER_SOURCE)
        bad_dimension: Any = '1" autofocus onfocus="alert(1)'

        with pytest.raises(ValueError, match="width must be an integer"):
            build_app(
                str(tmp_path / "Main.geno"),
                output=str(tmp_path / "dist"),
                width=bad_dimension,
            )

    def test_dist_build_grants_browser_target_capabilities(self, tmp_path):
        """Directory browser builds grant the capabilities accepted by typecheck."""
        (tmp_path / "Main.geno").write_text(_BROWSER_CAP_SOURCE)
        dist = tmp_path / "dist"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "build",
                str(tmp_path / "Main.geno"),
                "-o",
                str(dist),
                "--source-map",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, result.stderr
        app_js = (dist / "app.js").read_text()
        assert (
            'globalThis.__GENO_CAPS = ["clock", "http", "print", "random", "regex"];'
            in app_js
        )

        node_result = _run_browser_like_js(app_js)
        assert node_result.returncode == 0, node_result.stderr
        assert node_result.stdout.strip() == "1"

    def test_build_single_file(self, tmp_path):
        """geno build Main.geno --single-file -o Main.html"""
        (tmp_path / "Main.geno").write_text(_BROWSER_SOURCE)
        out = tmp_path / "Main.html"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "build",
                str(tmp_path / "Main.geno"),
                "--single-file",
                "--source-map",
                "-o",
                str(out),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert out.exists()
        html = out.read_text()
        assert "<!DOCTYPE html>" in html

    def test_single_file_build_grants_browser_target_capabilities(self, tmp_path):
        """Single-file browser builds grant the capabilities accepted by typecheck."""
        (tmp_path / "Main.geno").write_text(_BROWSER_CAP_SOURCE)
        out = tmp_path / "Main.html"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "build",
                str(tmp_path / "Main.geno"),
                "--single-file",
                "--source-map",
                "-o",
                str(out),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, result.stderr
        html = out.read_text()
        assert (
            'globalThis.__GENO_CAPS = ["clock", "http", "print", "random", "regex"];'
            in html
        )

        script = html.split("<script>", 1)[1].rsplit("</script>", 1)[0]
        node_result = _run_browser_like_js(script)
        assert node_result.returncode == 0, node_result.stderr
        assert node_result.stdout.strip() == "1"

    def test_build_single_file_html_extension_inference(self, tmp_path):
        """geno build Main.geno -o dashboard.html  (no --single-file flag)"""
        (tmp_path / "Main.geno").write_text(_BROWSER_SOURCE)
        out = tmp_path / "dashboard.html"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "build",
                str(tmp_path / "Main.geno"),
                "-o",
                str(out),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert out.exists()
        html = out.read_text()
        assert "<!DOCTYPE html>" in html
        # Must be a single file, not a directory
        assert out.is_file()
        assert not (tmp_path / "dashboard.html" / "index.html").exists()

    def test_source_map_generated_when_requested(self, tmp_path):
        """Source maps (app.js.map) are generated when explicitly requested."""
        (tmp_path / "Main.geno").write_text(_BROWSER_SOURCE)
        dist = tmp_path / "dist"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "build",
                str(tmp_path / "Main.geno"),
                "-o",
                str(dist),
                "--source-map",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        map_file = dist / "app.js.map"
        assert map_file.exists()
        sm = json.loads(map_file.read_text())
        assert sm["version"] == 3

    def test_dist_source_map_accounts_for_browser_bootstrap(self, tmp_path):
        """dist/app.js.map lines account for generated browser prelude lines."""
        (tmp_path / "Main.geno").write_text(_BROWSER_SOURCE_MAP_SOURCE)
        dist = tmp_path / "dist"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "build",
                str(tmp_path / "Main.geno"),
                "-o",
                str(dist),
                "--source-map",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, result.stderr
        app_lines = (dist / "app.js").read_text().splitlines()
        sm = json.loads((dist / "app.js.map").read_text())
        generated_line = _first_generated_line_for_source_line(sm, 0)

        assert "function double" in app_lines[generated_line]

    def test_single_file_source_map_accounts_for_script_prelude(self, tmp_path):
        """Inline HTML source-map lines account for canvas/bootstrap lines."""
        (tmp_path / "Main.geno").write_text(_BROWSER_SOURCE_MAP_SOURCE)
        out = tmp_path / "Main.html"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "build",
                str(tmp_path / "Main.geno"),
                "--single-file",
                "--source-map",
                "-o",
                str(out),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, result.stderr
        script = out.read_text().split("<script>", 1)[1].rsplit("</script>", 1)[0]
        sm = _inline_source_map(script)
        generated_line = _first_generated_line_for_source_line(sm, 0)

        assert "function double" in script.splitlines()[generated_line]

    def test_browser_http_uses_xhr_when_require_unavailable(self):
        """Browser HTTP builtins work in non-Node JS contexts with XMLHttpRequest."""
        from geno.js_runtime_prelude import JS_RUNTIME_PRELUDE

        script = (
            'globalThis.__GENO_CAPS = ["http"];\n'
            "class FakeXHR {\n"
            "    constructor() { this.headers = {}; }\n"
            "    open(method, url, async) {\n"
            "        this.method = method;\n"
            "        this.url = url;\n"
            "        this.async = async;\n"
            "    }\n"
            "    setRequestHeader(key, value) { this.headers[key] = value; }\n"
            "    send(body) {\n"
            "        this.status = this.url.includes('missing') ? 404 : 201;\n"
            "        this.responseText = `${this.method}:${this.url}:${body}`;\n"
            "    }\n"
            '    getAllResponseHeaders() { return "x-test: ok\\r\\n"; }\n'
            "}\n"
            "globalThis.XMLHttpRequest = FakeXHR;\n" + JS_RUNTIME_PRELUDE + "\n"
            'console.log(http_fetch("https://example.test/data"));\n'
            'console.log(http_fetch("https://example.test/missing"));\n'
            "const r = http_request(\n"
            '    "POST",\n'
            '    "https://example.test/post",\n'
            '    {"X-Test": "yes"},\n'
            '    Some("body")\n'
            ");\n"
            "console.log(\n"
            "    r._tag + ':' + r.value.status + ':' + r.value.body + ':'\n"
            "    + r.value.headers['x-test']\n"
            ");\n"
        )

        result = _run_browser_like_js(script)

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().splitlines() == [
            "GET:https://example.test/data:null",
            "GET:https://example.test/missing:null",
            "Ok:201:POST:https://example.test/post:body:ok",
        ]


class TestMultiModuleBuild:
    """Tests for multi-module project builds (e.g. geno-dash)."""

    _GENO_DASH = _REPO_ROOT / "examples" / "apps" / "geno-dash"

    @pytest.mark.skipif(
        not (_REPO_ROOT / "examples" / "apps" / "geno-dash" / "Main.geno").exists(),
        reason="geno-dash example not present",
    )
    def test_multi_module_single_file_build(self, tmp_path):
        """geno build examples/apps/geno-dash -o dashboard.html"""
        out = tmp_path / "dashboard.html"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "build",
                str(self._GENO_DASH),
                "-o",
                str(out),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert out.is_file()
        html = out.read_text()
        assert "<!DOCTYPE html>" in html
        # Verify multi-module content is embedded
        assert "<script" in html

    @pytest.mark.skipif(
        not (_REPO_ROOT / "examples" / "apps" / "geno-dash" / "Main.geno").exists(),
        reason="geno-dash example not present",
    )
    def test_multi_module_dist_directory_build(self, tmp_path):
        """geno build examples/apps/geno-dash -o dist/"""
        dist = tmp_path / "dist"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "build",
                str(self._GENO_DASH),
                "-o",
                str(dist),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert (dist / "index.html").exists()
        assert (dist / "app.js").exists()
        assert not (dist / "app.js.map").exists()


# ===================================================================
# Artifact quality tests (issue #227)
# ===================================================================


class TestSourceMapQuality:
    """Validate source map structure and content for multi-function files."""

    def test_source_map_has_valid_v3_structure(self):
        """Source map JSON conforms to the V3 spec schema."""
        from geno.js_compiler import compile_to_js

        result = compile_to_js(
            _MULTI_FN_SOURCE,
            filename="multi.geno",
            source_map=True,
            source_map_file="multi.js",
        )
        assert isinstance(result, tuple)
        _js_code, sm_json = result
        sm = json.loads(sm_json)

        assert sm["version"] == 3
        assert "sources" in sm
        assert "mappings" in sm
        assert "file" in sm
        assert sm["file"] == "multi.js"
        assert isinstance(sm["sources"], list)
        assert len(sm["sources"]) >= 1

    def test_sources_content_present(self):
        """sourcesContent contains the original Geno source."""
        from geno.js_compiler import compile_to_js

        result = compile_to_js(
            _MULTI_FN_SOURCE,
            filename="multi.geno",
            source_map=True,
            source_map_file="multi.js",
        )
        assert isinstance(result, tuple)
        _js_code, sm_json = result
        sm = json.loads(sm_json)

        assert "sourcesContent" in sm
        contents = sm["sourcesContent"]
        assert isinstance(contents, list)
        assert len(contents) >= 1
        assert contents[0] is not None
        # The original source should be embedded verbatim
        assert contents[0] == _MULTI_FN_SOURCE

    def test_mappings_non_empty(self):
        """mappings field is non-empty for a file with real functions."""
        from geno.js_compiler import compile_to_js

        result = compile_to_js(
            _MULTI_FN_SOURCE,
            filename="multi.geno",
            source_map=True,
        )
        assert isinstance(result, tuple)
        _js_code, sm_json = result
        sm = json.loads(sm_json)

        assert sm["mappings"], "mappings string should be non-empty"
        # V3 mappings use semicolons (line separators) and commas (segment
        # separators).  A multi-function file should produce multiple lines.
        assert ";" in sm["mappings"]

    def test_source_map_via_cli(self, tmp_path):
        """CLI compile --target js produces a .map sidecar file."""
        src = tmp_path / "Multi.geno"
        src.write_text(_MULTI_FN_SOURCE)
        out_js = tmp_path / "multi.js"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "compile",
                str(src),
                "--target",
                "js",
                "--source-map",
                "-o",
                str(out_js),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr

        map_file = tmp_path / "multi.js.map"
        assert map_file.exists(), ".map sidecar file was not created"

        sm = json.loads(map_file.read_text())
        assert sm["version"] == 3
        assert sm["mappings"]


class TestDtsGeneration:
    """Validate TypeScript declaration (.d.ts) output."""

    def test_dts_contains_declare_function(self):
        """generate_dts emits 'declare function' for exported functions."""
        from geno.js_compiler import generate_dts
        from geno.lexer import Lexer
        from geno.parser import Parser
        from geno.typechecker import TypeChecker

        tokens = Lexer(_MULTI_FN_SOURCE, "test.geno").tokenize()
        program = Parser(tokens).parse_program()
        TypeChecker().check_program(program)

        dts = generate_dts(program)

        assert "export declare function" in dts
        # All three functions should appear
        assert "color_name" in dts
        assert "sum_two" in dts
        assert "double" in dts

    def test_dts_contains_type_definitions(self):
        """generate_dts emits interfaces for user-defined types."""
        from geno.js_compiler import generate_dts
        from geno.lexer import Lexer
        from geno.parser import Parser
        from geno.typechecker import TypeChecker

        tokens = Lexer(_MULTI_FN_SOURCE, "test.geno").tokenize()
        program = Parser(tokens).parse_program()
        TypeChecker().check_program(program)

        dts = generate_dts(program)

        # The Color type has three variants; each should get an interface
        assert "export interface Red" in dts
        assert "export interface Green" in dts
        assert "export interface Blue" in dts

    def test_dts_contains_typed_fields(self):
        """generate_dts emits correct TS types for typed fields."""
        from geno.js_compiler import generate_dts
        from geno.lexer import Lexer
        from geno.parser import Parser
        from geno.typechecker import TypeChecker

        source = (
            "type Point = Point(x: Int, y: Int)\n"
            "\n"
            "func origin() -> Point\n"
            "    example () -> Point(0, 0)\n"
            "    return Point(0, 0)\n"
            "end func\n"
        )
        tokens = Lexer(source, "point.geno").tokenize()
        program = Parser(tokens).parse_program()
        TypeChecker().check_program(program)

        dts = generate_dts(program)

        assert "export interface Point" in dts
        assert "x: number" in dts
        assert "y: number" in dts

    def test_dts_generated_via_cli(self, tmp_path):
        """CLI compile --target js produces a .d.ts sidecar file."""
        src = tmp_path / "Multi.geno"
        src.write_text(_MULTI_FN_SOURCE)
        out_js = tmp_path / "multi.js"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "compile",
                str(src),
                "--target",
                "js",
                "-o",
                str(out_js),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr

        dts_file = tmp_path / "multi.d.ts"
        assert dts_file.exists(), ".d.ts file was not created"

        dts = dts_file.read_text()
        assert "export declare function" in dts


class TestEsmOutput:
    """Validate ES module output."""

    def test_esm_contains_export_statements(self):
        """ESM output contains an export { ... } block."""
        from geno.js_compiler import compile_to_js

        js_code = compile_to_js(_MULTI_FN_SOURCE, esm=True)

        assert "export {" in js_code
        # All function names (possibly mangled) should be exported
        assert "color_name" in js_code
        assert "sum_two" in js_code
        assert "double" in js_code

    def test_esm_exports_type_constructors(self):
        """ESM output exports type constructors alongside functions."""
        from geno.js_compiler import compile_to_js

        js_code = compile_to_js(_MULTI_FN_SOURCE, esm=True)

        # The Color variants (Red, Green, Blue) should be exported
        assert "Red" in js_code
        assert "Green" in js_code
        assert "Blue" in js_code

    def test_esm_omits_use_strict(self):
        """ESM output should not contain 'use strict' (implicit in ESM)."""
        from geno.js_compiler import compile_to_js

        js_code = compile_to_js(_MULTI_FN_SOURCE, esm=True)

        assert '"use strict"' not in js_code

    def test_esm_via_cli(self, tmp_path):
        """CLI compile --target js --esm produces ESM output."""
        src = tmp_path / "Multi.geno"
        src.write_text(_MULTI_FN_SOURCE)
        out_js = tmp_path / "multi.mjs"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "compile",
                str(src),
                "--target",
                "js",
                "--esm",
                "-o",
                str(out_js),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert out_js.exists()

        js_code = out_js.read_text()
        assert "export {" in js_code

    def test_esm_parseable_as_module(self, tmp_path):
        """ESM output can be parsed by Node.js as a module (syntax check)."""
        node = _find_node()
        if node is None:
            pytest.skip("Node.js not available")
        assert node is not None  # mypy narrowing

        src = tmp_path / "Multi.geno"
        src.write_text(_MULTI_FN_SOURCE)
        out_js = tmp_path / "multi.mjs"

        subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "compile",
                str(src),
                "--target",
                "js",
                "--esm",
                "-o",
                str(out_js),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )

        # --input-type=module + --check only works via stdin, so pipe the
        # compiled ESM code to Node for a syntax-only parse.
        esm_code = out_js.read_text()
        result = subprocess.run(
            [node, "--input-type=module", "--check"],
            input=esm_code,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, (
            f"Node.js failed to parse ESM output:\n{result.stderr}"
        )


def _find_node() -> "str | None":
    """Return the path to a Node.js binary, or None if not installed."""
    import shutil

    return shutil.which("node")


def _run_browser_like_js(js_code: str) -> subprocess.CompletedProcess[str]:
    """Run JS in a VM context without Node globals but with browser shims."""
    node = _find_node()
    if node is None:
        pytest.skip("Node.js not available")

    harness = (
        "const vm = require('vm');\n"
        "const logs = [];\n"
        "const canvasContext = {\n"
        "  fillRect() {}, strokeRect() {}, beginPath() {}, arc() {},\n"
        "  fill() {}, stroke() {}, moveTo() {}, lineTo() {}, fillText() {},\n"
        "  clearRect() {}, closePath() {}, font: '', fillStyle: '', strokeStyle: ''\n"
        "};\n"
        "const canvas = {\n"
        "  width: 800,\n"
        "  height: 600,\n"
        "  getContext() { return canvasContext; },\n"
        "  getBoundingClientRect() { return {left: 0, top: 0}; },\n"
        "  addEventListener() {}\n"
        "};\n"
        "const document = {\n"
        "  getElementById() { return canvas; },\n"
        "  addEventListener() {}\n"
        "};\n"
        "const context = vm.createContext({\n"
        "  document,\n"
        "  console: {log: (value) => logs.push(String(value))},\n"
        "  requestAnimationFrame: () => 0,\n"
        "  cancelAnimationFrame() {},\n"
        "  setTimeout,\n"
        "  clearTimeout\n"
        "});\n"
        "try {\n"
        f"  vm.runInContext({json.dumps(js_code)}, context, {{timeout: 1000}});\n"
        "  process.stdout.write(logs.join('\\n'));\n"
        "} catch (error) {\n"
        "  process.stderr.write(error && error.stack ? error.stack : String(error));\n"
        "  process.exit(1);\n"
        "}\n"
    )
    return run_node_code(harness, node_executable=node, timeout=10)


_SOURCE_MAP_BASE64_ALPHABET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
)


def _decode_vlq_segment(segment: str) -> list[int]:
    values: list[int] = []
    value = 0
    shift = 0

    for char in segment:
        digit = _SOURCE_MAP_BASE64_ALPHABET.index(char)
        continuation = digit & 32
        digit &= 31
        value += digit << shift
        if continuation:
            shift += 5
            continue

        values.append(-(value >> 1) if value & 1 else value >> 1)
        value = 0
        shift = 0

    return values


def _first_generated_line_for_source_line(
    source_map: dict[str, object], source_line: int
) -> int:
    prev_out_col = 0
    prev_src_idx = 0
    prev_src_line = 0
    prev_src_col = 0

    mappings = source_map["mappings"]
    assert isinstance(mappings, str)
    for generated_line, line in enumerate(mappings.split(";")):
        if not line:
            continue
        prev_out_col = 0
        for segment in line.split(","):
            values = _decode_vlq_segment(segment)
            if len(values) < 4:
                continue
            prev_out_col += values[0]
            prev_src_idx += values[1]
            prev_src_line += values[2]
            prev_src_col += values[3]
            if prev_src_line == source_line:
                return generated_line

    raise AssertionError(f"no mapping found for source line {source_line + 1}")


def _inline_source_map(script: str) -> dict[str, object]:
    marker = "sourceMappingURL=data:application/json;charset=utf-8;base64,"
    for line in script.splitlines():
        if marker in line:
            b64 = line.split(marker, 1)[1]
            return json.loads(base64.b64decode(b64).decode())

    raise AssertionError("inline source map not found")
