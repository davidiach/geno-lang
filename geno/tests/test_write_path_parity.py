"""A field write never changes a value that was read from the same place before.

Compiled JavaScript used to diverge here from the interpreter and compiled
Python in two ways: a ``for`` loop over an ``Array`` iterated the live array
(#241), and an element or field read passed as an argument handed the callee
the stored record, so a later field write through that place showed in the
callee's closure (#242). Every backend now agrees on each program below.
"""

import pytest

from geno.tests.test_backend_parity import (
    HAS_NODE,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)

BACKENDS = [
    _interpreter_output,
    _compiled_python_output,
    pytest.param(
        _compiled_js_output,
        marks=pytest.mark.skipif(not HAS_NODE, reason="Node.js not available"),
    ),
]

TYPES = """
type P = P(x: Int)
type Box = Box(p: P)
type Shelf = Shelf(items: List[P])

@untested("returns a closure")
func keep(q: P) -> () -> Int
    return fn() -> q.x
end func

@untested("returns a closure")
func keep_all(qs: List[P]) -> () -> Int
    return fn() -> qs[0].x
end func
"""

PROGRAMS = [
    pytest.param(
        """
func main() -> Unit with io, mutation
    var a = array_from_list([1, 2, 3])
    for x in a do
        a[2] = 99
        print(x)
    end for
    print(a[2])
end func
""",
        "1\n2\n3\n99\n",
        id="array-loop-sees-a-snapshot-of-the-slots",
    ),
    pytest.param(
        """
func main() -> Unit with io, mutation
    var a = array_new(2, P(x: 0))
    for p in a do
        a[1].x = 5
        print(p.x)
    end for
    print(a[1].x)
end func
""",
        "0\n0\n5\n",
        id="array-loop-variable-keeps-the-element-it-was-given",
    ),
    pytest.param(
        """
func main() -> Unit with io, mutation
    var a = array_new(1, P(x: 0))
    let peek = keep(a[0])
    a[0].x = 5
    print(peek())
    print(a[0].x)
end func
""",
        "0\n5\n",
        id="array-element-argument",
    ),
    pytest.param(
        """
func main() -> Unit with io, mutation
    var ys = [P(x: 0)]
    let peek = keep(ys[0])
    ys[0].x = 5
    print(peek())
    print(ys[0].x)
end func
""",
        "0\n5\n",
        id="list-element-argument",
    ),
    pytest.param(
        """
func main() -> Unit with io, mutation
    var b = Box(p: P(x: 0))
    let peek = keep(b.p)
    b.p.x = 5
    print(peek())
    print(b.p.x)
end func
""",
        "0\n5\n",
        id="field-argument",
    ),
    pytest.param(
        """
func main() -> Unit with io, mutation
    var m = {"k": P(x: 0)}
    let peek = keep(m["k"])
    m["k"].x = 5
    print(peek())
    print(m["k"].x)
end func
""",
        "0\n5\n",
        id="map-value-argument",
    ),
    pytest.param(
        """
func main() -> Unit with io, mutation
    var s = Shelf(items: [P(x: 0), P(x: 1)])
    let peek = keep_all(s.items)
    s.items[0].x = 5
    print(peek())
    print(s.items[0].x)
    print(s.items[1].x)
end func
""",
        "0\n5\n1\n",
        id="list-field-argument-then-a-write-below-it",
    ),
    pytest.param(
        """
func main() -> Unit with io, mutation
    var rows = [[P(x: 0)], [P(x: 1)]]
    let peek = keep(rows[0][0])
    rows[0][0].x = 5
    rows[1][0].x = 6
    print(peek())
    print(rows)
end func
""",
        "0\n[[P(x: 5)], [P(x: 6)]]\n",
        id="nested-element-argument",
    ),
]


@pytest.mark.parametrize("source, expected", PROGRAMS)
@pytest.mark.parametrize("backend", BACKENDS)
def test_a_field_write_does_not_reach_an_earlier_read(backend, source, expected):
    assert backend(TYPES + source) == expected
