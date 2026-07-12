"""
Tests for target-aware typechecking (#125)
==========================================
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.builtin_registry import source_builtin_specs
from geno.compiler import compile_to_python
from geno.dependency_graph import DependencyGraph
from geno.js_compiler import compile_to_js
from geno.project_graph import ProjectGraph
from geno.target_profile import (
    TARGETS_TOML,
    VALID_TARGETS,
    ManifestTargetError,
    TargetProfile,
    resolve_manifest_targets,
    tomllib,
)
from geno.typechecker import TypeChecker
from geno.types import GenoTypeError

# =========================================================================
# TargetProfile loading
# =========================================================================


class TestTargetProfile:
    def test_packaged_targets_toml_matches_repo_source(self):
        repo_root = Path(__file__).resolve().parents[2]
        assert (repo_root / "geno" / "targets.toml").read_text(encoding="utf-8") == (
            repo_root / "targets.toml"
        ).read_text(encoding="utf-8")

    def test_capability_builtins_are_explicitly_listed(self):
        raw = tomllib.loads(TARGETS_TOML.read_text(encoding="utf-8"))
        target_entries = set(raw.get("builtins", {}))
        missing = sorted(
            name
            for name, spec in source_builtin_specs().items()
            if spec.capability is not None and name not in target_entries
        )

        assert missing == []

    def test_capability_gated_entries_match_target_capabilities(self):
        raw = tomllib.loads(TARGETS_TOML.read_text(encoding="utf-8"))
        target_capabilities = {
            name: set(info.get("capabilities", []))
            for name, info in raw.get("targets", {}).items()
        }
        mismatches = []

        for builtin_name, info in raw.get("builtins", {}).items():
            capability = info.get("capability")
            if capability is None:
                continue
            for target in sorted(VALID_TARGETS):
                if (
                    info.get(target, "available") == "capability-gated"
                    and capability not in target_capabilities[target]
                ):
                    mismatches.append(f"{builtin_name}:{target}:{capability}")

        assert mismatches == []

    def test_load_browser_rejects_fs(self):
        profile = TargetProfile.load("browser")
        assert not profile.is_available("fs_read_text")
        assert not profile.is_available("fs_write_text")
        assert not profile.is_available("fs_list_dir")
        assert not profile.is_available("fs_exists")

    def test_load_browser_allows_http(self):
        profile = TargetProfile.load("browser")
        assert profile.is_available("http_fetch")
        assert profile.is_available("http_post")

    def test_load_browser_reads_target_capabilities(self):
        profile = TargetProfile.load("browser")
        assert profile.capabilities == {"clock", "http", "print", "random", "regex"}

    def test_load_browser_rejects_env(self):
        profile = TargetProfile.load("browser")
        assert not profile.is_available("env_get")
        assert not profile.is_available("env_get_or")
        assert not profile.is_available("cli_args")

    def test_load_browser_rejects_serve(self):
        profile = TargetProfile.load("browser")
        assert not profile.is_available("http_listen")
        assert not profile.is_available("http_route")
        assert not profile.is_available("http_respond")

    def test_load_node_rejects_serve_builtins(self):
        profile = TargetProfile.load("node-cli")
        assert not profile.is_available("http_listen")
        assert not profile.is_available("http_route")
        assert not profile.is_available("http_respond")

    def test_load_python_cli_allows_fs(self):
        profile = TargetProfile.load("python-cli")
        assert profile.is_available("fs_read_text")
        assert profile.is_available("exec")
        assert profile.is_available("http_listen")
        assert profile.is_available("http_route")

    def test_load_python_cli_rejects_graphics(self):
        profile = TargetProfile.load("python-cli")
        assert not profile.is_available("is_key_down")
        assert not profile.is_available("clear_screen")
        assert not profile.is_available("draw_rect")

    def test_load_browser_allows_graphics(self):
        profile = TargetProfile.load("browser")
        assert profile.is_available("is_key_down")
        assert profile.is_available("clear_screen")

    def test_invalid_target_raises(self):
        with pytest.raises(ValueError, match="Unknown target"):
            TargetProfile.load("invalid-target")

    def test_manifest_targets_return_all_declared_targets(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\n'
            'files = ["Main"]\n'
            'targets = ["python-cli", "node-cli"]\n',
            encoding="utf-8",
        )

        assert resolve_manifest_targets(tmp_path) == ["python-cli", "node-cli"]

    def test_manifest_targets_fail_closed_on_typo(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main"]\ntargets = ["browzer"]\n',
            encoding="utf-8",
        )

        with pytest.raises(ManifestTargetError, match="Unknown target 'browzer'"):
            resolve_manifest_targets(tmp_path)

    def test_missing_target_metadata_fails_closed(self, tmp_path):
        with pytest.raises(RuntimeError, match="Target metadata not found"):
            TargetProfile.load("browser", toml_path=tmp_path / "missing.toml")

    def test_permissive_allows_all(self):
        profile = TargetProfile.permissive()
        assert profile.is_available("fs_read_text")
        assert profile.is_available("is_key_down")
        assert profile.is_available("exec")

    def test_rejection_message(self):
        profile = TargetProfile.load("browser")
        msg = profile.rejection_message("fs_read_text")
        assert "browser" in msg
        assert "fs_read_text" in msg

    def test_rejection_message_suggests_alternatives(self):
        """Rejection messages should list which targets support the builtin."""
        profile = TargetProfile.load("browser")
        msg = profile.rejection_message("fs_read_text")
        # fs_read_text should be available on python-cli and node-cli
        assert "Available on:" in msg
        assert "python-cli" in msg


# =========================================================================
# Target-aware typechecking
# =========================================================================


class TestTargetAwareTypechecking:
    def test_fs_rejected_on_browser(self, tmp_path):
        """fs_read_text on browser target produces a type error."""
        (tmp_path / "geno.toml").write_text('files = ["Main"]\n')
        (tmp_path / "Main.geno").write_text(
            'func main() -> String\n  return fs_read_text("file.txt")\nend func\n'
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        profile = TargetProfile.load("browser")
        checker = TypeChecker(target_profile=profile)

        with pytest.raises(GenoTypeError) as exc_info:
            checker.check_project_graph(dg)

        assert "fs_read_text" in str(exc_info.value)
        assert "browser" in str(exc_info.value)

    def test_is_key_down_rejected_on_python_cli(self, tmp_path):
        """is_key_down on python-cli target produces a type error."""
        (tmp_path / "geno.toml").write_text('files = ["Main"]\n')
        (tmp_path / "Main.geno").write_text(
            'func main() -> Bool\n  return is_key_down("a")\nend func\n'
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        profile = TargetProfile.load("python-cli")
        checker = TypeChecker(target_profile=profile)

        with pytest.raises(GenoTypeError) as exc_info:
            checker.check_project_graph(dg)

        assert "is_key_down" in str(exc_info.value)
        assert "python-cli" in str(exc_info.value)

    def test_fs_allowed_on_python_cli(self, tmp_path):
        """fs_read_text on python-cli target is allowed."""
        (tmp_path / "geno.toml").write_text('files = ["Main"]\n')
        (tmp_path / "Main.geno").write_text(
            '@untested("io")\n'
            "func main() -> String\n"
            '  return fs_read_text("file.txt")\n'
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        profile = TargetProfile.load("python-cli")
        checker = TypeChecker(target_profile=profile)
        checked = checker.check_project_graph(dg)

        assert "Main" in checked

    def test_no_target_allows_all(self, tmp_path):
        """Without a target profile, all builtins are available."""
        (tmp_path / "geno.toml").write_text('files = ["Main"]\n')
        (tmp_path / "Main.geno").write_text(
            '@untested("io")\n'
            "func main() -> String\n"
            '  return fs_read_text("file.txt")\n'
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()  # No target profile
        checked = checker.check_project_graph(dg)

        assert "Main" in checked


# =========================================================================
# Target-aware compile entrypoints
# =========================================================================


class TestTargetAwareCompile:
    def test_compile_to_js_rejects_node_unavailable_builtin(self):
        source = """
func main() -> Unit
  sleep_ms(0)
end func
"""

        with pytest.raises(GenoTypeError) as exc_info:
            compile_to_js(source)

        assert "sleep_ms" in str(exc_info.value)
        assert "node-cli" in str(exc_info.value)

    def test_compile_to_js_accepts_explicit_browser_profile(self):
        source = """
func main() -> Int
  return screen_width()
end func
"""

        js_code = compile_to_js(
            source,
            target_profile=TargetProfile.load("browser"),
        )

        assert "screen_width" in js_code

    def test_compile_to_js_rejects_browser_cli_args(self):
        source = """
func main() -> List[String]
  return cli_args()
end func
"""

        with pytest.raises(GenoTypeError) as exc_info:
            compile_to_js(source, target_profile=TargetProfile.load("browser"))

        assert "cli_args" in str(exc_info.value)
        assert "browser" in str(exc_info.value)

    def test_compile_to_js_rejects_browser_http_respond(self):
        source = """
func main() -> HttpResponse
  return http_respond(status: 200, headers: [], body: "ok")
end func
"""

        with pytest.raises(GenoTypeError) as exc_info:
            compile_to_js(source, target_profile=TargetProfile.load("browser"))

        assert "http_respond" in str(exc_info.value)
        assert "browser" in str(exc_info.value)

    def test_compile_to_js_rejects_node_server_listener(self):
        source = """
func main() -> Unit
  http_listen(8080)
end func
"""

        with pytest.raises(GenoTypeError) as exc_info:
            compile_to_js(source)

        assert "http_listen" in str(exc_info.value)
        assert "node-cli" in str(exc_info.value)

    def test_compile_to_js_rejects_node_http_respond(self):
        source = """
func main() -> HttpResponse
  return http_respond(status: 200, headers: [], body: "ok")
end func
"""

        with pytest.raises(GenoTypeError) as exc_info:
            compile_to_js(source)

        assert "http_respond" in str(exc_info.value)
        assert "node-cli" in str(exc_info.value)

    def test_compile_to_js_rejects_node_exec(self):
        source = """
func main() -> Result[ProcessResult, String]
  return exec("echo hi")
end func
"""

        with pytest.raises(GenoTypeError) as exc_info:
            compile_to_js(source)

        assert "exec" in str(exc_info.value)
        assert "node-cli" in str(exc_info.value)

    def test_compile_to_python_rejects_python_unavailable_builtin(self):
        source = """
func main() -> Int
  return screen_width()
end func
"""

        with pytest.raises(GenoTypeError) as exc_info:
            compile_to_python(source)

        assert "screen_width" in str(exc_info.value)
        assert "python-cli" in str(exc_info.value)

    def test_cli_compile_js_rejects_node_unavailable_builtin(self, tmp_path):
        source = tmp_path / "Main.geno"
        output = tmp_path / "main.js"
        source.write_text(
            """
func main() -> Unit
  sleep_ms(0)
end func
""",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "compile",
                str(source),
                "--target",
                "js",
                "-o",
                str(output),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert "sleep_ms" in result.stderr
        assert "node-cli" in result.stderr
        assert not output.exists()

    def test_cli_compile_python_rejects_python_unavailable_builtin(self, tmp_path):
        source = tmp_path / "Main.geno"
        output = tmp_path / "main.py"
        source.write_text(
            """
func main() -> Int
  return screen_width()
end func
""",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "compile",
                str(source),
                "-o",
                str(output),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert "screen_width" in result.stderr
        assert "python-cli" in result.stderr
        assert not output.exists()

    def test_cli_run_unsafe_honors_manifest_target(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main"]\ntargets = ["python-cli"]\n',
            encoding="utf-8",
        )
        source = tmp_path / "Main.geno"
        source.write_text(
            """
func main() -> Int
  return screen_width()
end func
""",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, "-m", "geno", "run", "--unsafe", str(source)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert "screen_width" in result.stderr
        assert "python-cli" in result.stderr

    def test_cli_run_json_honors_manifest_target(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main"]\ntargets = ["python-cli"]\n',
            encoding="utf-8",
        )
        source = tmp_path / "Main.geno"
        source.write_text(
            """
func main() -> Int
  return screen_width()
end func
""",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, "-m", "geno", "run", "--json", str(source)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert "screen_width" in result.stdout
        assert "python-cli" in result.stdout

    def test_cli_check_validates_all_manifest_targets(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\n'
            'files = ["Main"]\n'
            'targets = ["python-cli", "node-cli"]\n',
            encoding="utf-8",
        )
        (tmp_path / "Main.geno").write_text(
            """
func main() -> Unit
  http_listen(8080)
end func
""",
            encoding="utf-8",
        )

        explicit_python = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "check",
                "--target",
                "python-cli",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        default_manifest = subprocess.run(
            [sys.executable, "-m", "geno", "check", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert explicit_python.returncode == 0, explicit_python.stderr
        assert default_manifest.returncode != 0
        assert "http_listen" in default_manifest.stderr
        assert "node-cli" in default_manifest.stderr

    def test_cli_check_reports_every_failing_manifest_target(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\n'
            'files = ["Main"]\n'
            'targets = ["python-cli", "node-cli"]\n',
            encoding="utf-8",
        )
        (tmp_path / "Main.geno").write_text(
            """
func main() -> Int
  return screen_width()
end func
""",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, "-m", "geno", "check", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert "Type Error (target: python-cli)" in result.stderr
        assert "Type Error (target: node-cli)" in result.stderr

    def test_cli_check_prints_each_successful_manifest_target(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\n'
            'files = ["Main"]\n'
            'targets = ["python-cli", "node-cli"]\n',
            encoding="utf-8",
        )
        (tmp_path / "Main.geno").write_text(
            """
func main() -> Int
  return 1
end func
""",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, "-m", "geno", "check", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0, result.stderr
        assert "Type check passed:" in result.stdout
        assert "  python-cli: passed" in result.stdout
        assert "  node-cli: passed" in result.stdout

    def test_cli_check_invalid_manifest_target_fails_closed(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main"]\ntargets = ["browzer"]\n',
            encoding="utf-8",
        )
        (tmp_path / "Main.geno").write_text(
            """
func main() -> String
  return fs_read_text("demo.txt")
end func
""",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, "-m", "geno", "check", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert "Unknown target 'browzer'" in result.stderr
        assert "Type check passed" not in result.stdout

    def test_cli_test_honors_manifest_target(self, tmp_path):
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main"]\ntargets = ["python-cli"]\n',
            encoding="utf-8",
        )
        (tmp_path / "Main.geno").write_text(
            """
func main() -> Int
  return screen_width()
end func
""",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, "-m", "geno", "test", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert "screen_width" in result.stdout
        assert "python-cli" in result.stdout


# =========================================================================
# Multi-module typechecking in topological order
# =========================================================================


class TestMultiModuleTypechecking:
    def test_multi_module_topo_order(self, tmp_path):
        """Typechecks all modules carrying type info across boundaries."""
        (tmp_path / "geno.toml").write_text('files = ["Main", "Utils"]\n')
        (tmp_path / "Utils.geno").write_text(
            "func double(x: Int) -> Int\n  example 3 -> 6\n  return x * 2\nend func\n"
        )
        (tmp_path / "Main.geno").write_text(
            "import Utils\n"
            '@untested("integration")\n'
            "func main() -> Int\n"
            "  return double(3)\n"
            "end func\n"
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        checked = checker.check_project_graph(dg)

        assert "Utils" in checked
        assert "Main" in checked

    def test_returns_all_checked_asts(self, tmp_path):
        """All modules in the graph appear in the returned dict."""
        (tmp_path / "geno.toml").write_text('files = ["A", "B", "C"]\n')
        (tmp_path / "A.geno").write_text(
            'import B\n@untested("test")\nfunc a() -> Int\n  return 1\nend func\n'
        )
        (tmp_path / "B.geno").write_text(
            'import C\n@untested("test")\nfunc b() -> Int\n  return 2\nend func\n'
        )
        (tmp_path / "C.geno").write_text(
            '@untested("test")\nfunc c() -> Int\n  return 3\nend func\n'
        )

        pg = ProjectGraph.discover(tmp_path)
        dg = DependencyGraph.resolve(pg)
        checker = TypeChecker()
        checked = checker.check_project_graph(dg)

        assert set(checked.keys()) == {"A", "B", "C"}
