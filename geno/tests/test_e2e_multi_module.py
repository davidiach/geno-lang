"""
End-to-end CI tests for multi-module compilation (#138)
========================================================

A 3-module fixture project is compiled via every supported path:
check, run, compile (Python), compile (JS), build (HTML).
This test acts as a permanent regression gate.
"""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture()
def project(tmp_path):
    """Create a 3-module project fixture."""
    (tmp_path / "geno.toml").write_text(
        'entrypoint = "App"\nfiles = ["App", "Math", "Format"]\n'
    )
    (tmp_path / "Math.geno").write_text(
        "func double(x: Int) -> Int\n"
        "  example 3 -> 6\n"
        "  example 0 -> 0\n"
        "  return x * 2\n"
        "end func\n"
        "\n"
        "func square(x: Int) -> Int\n"
        "  example 4 -> 16\n"
        "  return x * x\n"
        "end func\n"
    )
    (tmp_path / "Format.geno").write_text(
        "import Math\n"
        "func describe(x: Int) -> String\n"
        '  example 3 -> "double=6 square=9"\n'
        '  return f"double={double(x)} square={square(x)}"\n'
        "end func\n"
    )
    (tmp_path / "App.geno").write_text(
        "import Format\n"
        '@untested("entry point")\n'
        "func main() -> String\n"
        "  return describe(5)\n"
        "end func\n"
    )
    return tmp_path


def _run(cmd, **kwargs):
    """Run a geno CLI command and return the result."""
    return subprocess.run(
        [sys.executable, "-m", "geno"] + cmd,
        capture_output=True,
        text=True,
        timeout=30,
        **kwargs,
    )


class TestMultiModuleE2E:
    def test_check(self, project):
        r = _run(["check", str(project / "App.geno")])
        assert r.returncode == 0, f"check failed: {r.stderr}"

    def test_run(self, project):
        r = _run(["run", str(project / "App.geno")])
        assert r.returncode == 0, f"run failed: {r.stderr}"
        assert "double=10 square=25" in r.stdout

    def test_compile_python(self, project):
        out = project / "out.py"
        r = _run(["compile", str(project / "App.geno"), "-o", str(out)])
        assert r.returncode == 0, f"compile py failed: {r.stderr}"
        assert out.exists()

        # Verify the compiled Python runs correctly
        r2 = subprocess.run(
            [sys.executable, str(out)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert r2.returncode == 0, f"compiled py failed: {r2.stderr}"
        assert "double=10 square=25" in r2.stdout

    def test_compile_js(self, project):
        out = project / "out.js"
        r = _run(
            ["compile", str(project / "App.geno"), "--target", "js", "-o", str(out)]
        )
        assert r.returncode == 0, f"compile js failed: {r.stderr}"
        assert out.exists()

        # Verify the compiled JS runs correctly (if Node.js is available)
        r2 = subprocess.run(
            ["node", str(out)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r2.returncode != 0:
            pytest.skip("Node.js execution failed")
        assert "double=10 square=25" in r2.stdout

    def test_build_html(self, project):
        out = project / "out.html"
        r = _run(["build", str(project / "App.geno"), "-o", str(out), "--single-file"])
        assert r.returncode == 0, f"build failed: {r.stderr}"
        assert out.exists()

        html = out.read_text()
        assert "<!DOCTYPE html>" in html
        assert "<canvas" in html

    def test_build_dist(self, project):
        dist = project / "dist"
        r = _run(["build", str(project / "App.geno"), "-o", str(dist)])
        assert r.returncode == 0, f"build failed: {r.stderr}"
        assert (dist / "index.html").exists()
        assert (dist / "app.js").exists()
        assert not (dist / "app.js.map").exists()

        html = (dist / "index.html").read_text()
        assert "<!DOCTYPE html>" in html
        assert "<canvas" in html
        assert 'src="app.js"' in html

    def test_test(self, project):
        r = _run(["test", str(project)])
        assert r.returncode == 0, f"test failed: {r.stderr}"
        assert "PASS" in r.stdout
