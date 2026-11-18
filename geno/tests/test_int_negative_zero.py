"""An Int has no negative zero on any backend.

Compiled JavaScript stores an Int as a Number, and ``Math.ceil(-0.4)``,
``Math.floor(-0.0)`` and ``Math.trunc(-0.5)`` are ``-0``. Such an Int printed
as ``0`` but showed a minus sign once it reached a Float format spec or
``int_to_float`` (``f"{ceil(-0.4):.1f}"`` was ``-0.0``), where the interpreter
and compiled Python print ``0.0``. An extern returning ``-0`` for an ``Int``
did the same (0.7 review).
"""

import subprocess
from pathlib import Path

import pytest

from geno import api
from geno.js_compiler import compile_to_js
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)

needs_node = pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")

BACKENDS = [
    _interpreter_output,
    _compiled_python_output,
    pytest.param(_compiled_js_output, marks=needs_node),
]

PROGRAM = """
@untested("prints")
func show(n: Int) -> Unit with io
    print(f"{n} {n:.1f} {n:+.2f} {n:.0%} {n:e} " + to_string(int_to_float(n)))
end func

func main() -> Unit with io
    show(ceil(-0.4))
    show(floor(-0.0))
    show(float_to_int(-0.5))
    show(round(-0.4))
end func
"""

EXPECTED = "0 0.0 +0.00 0% 0.000000e+00 0.0\n" * 4


@pytest.mark.parametrize("backend", BACKENDS)
def test_an_int_zero_from_a_float_has_no_sign(backend):
    assert backend(PROGRAM) == EXPECTED


EXTERN = """
extern func zero() -> Int with ffi
    python "neg_zero_host:zero"
    js "./neg_zero_host.js#zero"
end func

extern func zero_json() -> JsonValue with ffi
    python "neg_zero_host:zero_float"
    js "./neg_zero_host.js#zero"
end func

func main() -> Unit with ffi, io
    let x = zero()
    print(f"{x} {x:.1f} " + to_string(int_to_float(x)))
    print(json_stringify(zero_json()))
end func
"""


@needs_node
def test_a_js_extern_int_result_has_no_negative_zero(tmp_path: Path):
    (tmp_path / "neg_zero_host.js").write_text(
        "module.exports = { zero: () => -0 };\n", encoding="utf-8"
    )
    js = compile_to_js(EXTERN)
    assert isinstance(js, str)
    program = tmp_path / "program.js"
    program.write_text(js, encoding="utf-8")
    completed = subprocess.run(
        ["node", str(program), "--cap", "ffi,print"],
        capture_output=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    # The Int is 0; as a JsonValue the host's -0 is the Float it is.
    assert completed.stdout == "0 0.0 0.0\n-0.0\n"


def test_the_interpreter_extern_agrees(tmp_path: Path, monkeypatch):
    (tmp_path / "neg_zero_host.py").write_text(
        "def zero():\n    return 0\n\n\ndef zero_float():\n    return -0.0\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    result = api.run(
        EXTERN,
        config=api.RunConfig(
            capabilities={"ffi", "print"}, allow_ffi=True, timeout=10.0
        ),
    )
    assert result.ok, result.diagnostics
    assert result.output == "0 0.0 0.0\n-0.0\n"
