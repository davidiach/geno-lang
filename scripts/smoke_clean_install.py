#!/usr/bin/env python3
"""Smoke-test the public first-run path from an installed Geno command.

This script intentionally shells out to ``geno`` from a temporary directory
instead of importing the source tree. It covers the README/getting-started
happy path plus the major user-facing commands.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HELLO_SOURCE = """\
func greet(name: String) -> String
    example "Alice" -> "Hello, Alice!"
    return "Hello, " + name + "!"
end func

func main() -> String
    return greet("World")
end func
"""


APP_SOURCE = """\
type Model = Model(count: Int)

func init() -> Model
    return Model(0)
end func

func update(model: Model, dt: Float) -> Model
    return Model(model.count + 1)
end func

func render(model: Model) -> Unit
    clear_screen("#111111")
    draw_text(
        text: "Count: " + to_string(model.count),
        x: 24,
        y: 32,
        size: 18,
        color: "#ffffff"
    )
    return ()
end func
"""


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"Command failed: {' '.join(command)}", file=sys.stderr)
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    return result


def _run_expect_failure(
    command: list[str], cwd: Path
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        raise SystemExit(f"command unexpectedly passed: {' '.join(command)}")
    return result


def _find_command(name: str) -> str | None:
    return shutil.which(name)


def main() -> int:
    geno_path = _find_command("geno")
    geno = [geno_path] if geno_path is not None else [sys.executable, "-m", "geno"]
    node = shutil.which("node")

    with tempfile.TemporaryDirectory(prefix="geno-clean-install-") as raw_tmp:
        tmp = Path(raw_tmp)
        hello = tmp / "hello.geno"
        hello.write_text(HELLO_SOURCE, encoding="utf-8")

        app = tmp / "app.geno"
        app.write_text(APP_SOURCE, encoding="utf-8")

        policy_dir = tmp / "target-policy"
        policy_dir.mkdir()
        (policy_dir / "geno.toml").write_text(
            'entrypoint = "Main"\nfiles = ["Main"]\ntargets = ["python-cli"]\n',
            encoding="utf-8",
        )
        (policy_dir / "Main.geno").write_text(
            "func main() -> Int\n    return screen_width()\nend func\n",
            encoding="utf-8",
        )

        run_result = _run([*geno, "run", str(hello)], tmp)
        if "Hello, World!" not in run_result.stdout:
            raise SystemExit(f"unexpected geno run output: {run_result.stdout!r}")

        _run([*geno, "check", str(hello)], tmp)
        _run([*geno, "test", str(hello)], tmp)
        rejected = _run_expect_failure([*geno, "test", str(policy_dir)], tmp)
        if "screen_width" not in rejected.stdout:
            raise SystemExit(
                "installed target policy smoke did not report screen_width"
            )

        py_out = tmp / "hello.py"
        _run([*geno, "compile", str(hello), "-o", str(py_out)], tmp)
        py_result = _run([sys.executable, str(py_out)], tmp)
        if "Hello, World!" not in py_result.stdout:
            raise SystemExit(f"unexpected compiled Python output: {py_result.stdout!r}")

        if node is not None:
            js_out = tmp / "hello.js"
            _run(
                [*geno, "compile", "--target", "js", str(hello), "-o", str(js_out)],
                tmp,
            )
            js_result = _run([node, str(js_out)], tmp)
            if "Hello, World!" not in js_result.stdout:
                raise SystemExit(f"unexpected compiled JS output: {js_result.stdout!r}")

        html_out = tmp / "app.html"
        _run([*geno, "build", str(app), "--single-file", "-o", str(html_out)], tmp)
        html_text = html_out.read_text(encoding="utf-8")
        if "requestAnimationFrame" not in html_text:
            raise SystemExit("built app is missing browser runtime bootstrap")

    print("Clean install smoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
