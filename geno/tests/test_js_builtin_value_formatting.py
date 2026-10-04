"""Built-in and copied recursive values print the same on JS (#141)."""

from __future__ import annotations

import pytest

from geno.tests.test_backend_parity import (
    HAS_NODE,
    _assert_expected_backend_outputs,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)

SOURCE = """
type Node = Node(v: Float, kids: List[Node], t: (Float, Int))

func main() -> Unit
    print(JsonFloat(5.0))
    print(JsonObject([("a", JsonFloat(2.0)), ("b", JsonArray([JsonFloat(-0.0), JsonString("s"), JsonInt(3), JsonBool(true), JsonNull]))]))
    print(json_parse("[1.0, {\\"k\\": 2.5}]"))
    print(to_string(Some(JsonFloat(1.0))))
    print(f"{JsonFloat(4.0)}")
    print([JsonFloat(3.0)])
    let leaf = Node(2.0, [], (1.0, 2))
    let t = Node(1.0, [leaf], (3.0, 4))
    print(t)
    print(t with (v: 9.0))
    let copied = [t]
    print(copied[0])
    print(HttpResponse(200, "ok", [("a", "b")]))
    print(HttpRequest("GET", "/x", "q=1", [("h", "v")], "body"))
    return ()
end func
"""


@pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
def test_builtin_and_copied_values_print_identically() -> None:
    interp = _interpreter_output(SOURCE)
    _assert_expected_backend_outputs(
        label="JS formatting of built-in and copied values",
        context=SOURCE,
        expected=interp,
        interp_out=interp,
        py_out=_compiled_python_output(SOURCE),
        js_out=_compiled_js_output(SOURCE),
    )
    assert "JsonFloat(value: 5.0)" in interp
    assert "JsonFloat(value: -0.0)" in interp
    assert 'headers: [("a", "b")]' in interp
