"""Path builtins give the same answers on every backend (#146)."""

from __future__ import annotations

import json

import pytest

from geno.tests.test_backend_parity import (
    HAS_NODE,
    _assert_expected_backend_outputs,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)

# (path, parent, extension, filename, is_absolute)
CASES = [
    ("a//b", "a", "", "b", False),
    ("a///b", "a", "", "b", False),
    ("//a", "//", "", "a", True),
    ("///", "///", "", "", True),
    ("/a/b/", "/a/b", "", "", True),
    ("a/", "a", "", "", False),
    ("..", "", "", "..", False),
    ("...", "", "", "...", False),
    (".a", "", "", ".a", False),
    (".a.b", "", ".b", ".a.b", False),
    ("a.b.", "", ".", "a.b.", False),
    ("..a.b", "", ".b", "..a.b", False),
    ("dir/.hidden.txt", "dir", ".txt", ".hidden.txt", False),
    ("é:/x", "é:", "", "x", False),
    ("C:/x", "C:", "", "x", True),
    ("1:/x", "1:", "", "x", False),
]


def _source() -> str:
    lines = []
    for path, *_ in CASES:
        lit = json.dumps(path, ensure_ascii=False)
        lines.append(
            f'    print("[" + path_parent({lit}) + "|" + path_extension({lit})'
            f' + "|" + path_filename({lit}) + "]")'
        )
        lines.append(f"    print(path_is_absolute({lit}))")
    return "func main() -> Unit\n" + "\n".join(lines) + "\n    return ()\nend func\n"


def _expected() -> str:
    out = []
    for _, parent, ext, name, absolute in CASES:
        out.append(f"[{parent}|{ext}|{name}]")
        out.append("true" if absolute else "false")
    return "\n".join(out) + "\n"


@pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
def test_path_builtins_agree_on_every_backend() -> None:
    source = _source()
    _assert_expected_backend_outputs(
        label="path builtins",
        context=source,
        expected=_expected(),
        interp_out=_interpreter_output(source),
        py_out=_compiled_python_output(source),
        js_out=_compiled_js_output(source),
    )
