"""Regressions for call evaluation order and lexical closure bindings."""

import pytest

from geno.tests.test_backend_parity import (
    HAS_NODE,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)

NAMED_ARGUMENT_PROGRAM = """
func mark(x: Int) -> Int
    example 1 -> 1
    print(x)
    return x
end func

func combine(a: Int, b: Int = 4, c: Int = 5) -> Int
    example (1, 2, 3) -> 123
    return a * 100 + b * 10 + c
end func

func pair(a: Int, b: Int) -> Int
    example (1, 2) -> 12
    return a * 10 + b
end func

func main() -> Unit
    print(combine(c: mark(3), a: mark(1), b: mark(2)))
    print(combine(c: mark(6), a: mark(2)))
    print(pair(b: mark(2), mark(1)))
    let skipped = false and combine(c: mark(8), a: mark(9)) == 0
    print(skipped)
    print([combine(c: mark(x), a: mark(1)) for x: Int in [2, 3]])
    return ()
end func
"""


CLOSURE_PROGRAMS = [
    pytest.param(
        """
func main() -> Unit
    var x: Int = 0
    let increment = fn() do
        x = x + 1
        return x
    end fn
    print(increment())
    print(increment())
    print(x)
    let assign = fn() do
        x = 10
        return ()
    end fn
    assign()
    print(x)
    return ()
end func
""",
        "1\n2\n2\n10\n",
        id="captured-mutable-binding",
    ),
    pytest.param(
        """
func main() -> Unit
    var fs: List[() -> Int] = []
    var i: Int = 0
    while i < 3 do
        let x: Int = i
        fs = append(fs, fn() -> x)
        i = i + 1
    end while
    print(fs[0]())
    print(fs[1]())
    print(fs[2]())
    return ()
end func
""",
        "0\n1\n2\n",
        id="while-local-capture",
    ),
    pytest.param(
        """
func main() -> Unit
    var fs: List[() -> Int] = []
    for i: Int in [1, 2, 3] do
        var x: Int = i
        fs = append(fs, fn() -> x)
        x = x * 10
    end for
    print(fs[0]())
    print(fs[1]())
    print(fs[2]())
    return ()
end func
""",
        "10\n20\n30\n",
        id="for-local-mutation-after-capture",
    ),
    pytest.param(
        """
func main() -> Unit
    var fs: List[() -> Int] = []
    for i: Int in [1, 2, 3] do
        let x: Int = i
        fs = append(fs, fn() -> x)
        let x: Int = i * 10
    end for
    print(fs[0]())
    print(fs[1]())
    print(fs[2]())
    return ()
end func
""",
        "10\n20\n30\n",
        id="same-iteration-rebinding",
    ),
    pytest.param(
        """
func main() -> Unit
    var fs: List[() -> Int with mutation] = []
    var readers: List[() -> Int] = []
    for i: Int in [1, 2] do
        var x: Int = i
        fs = append(fs, fn() do
            x = x + 10
            return x
        end fn)
        readers = append(readers, fn() -> x)
    end for
    print(fs[0]())
    print(readers[0]())
    print(readers[1]())
    print(fs[0]())
    print(fs[1]())
    return ()
end func
""",
        "11\n11\n2\n21\n12\n",
        id="shared-cell-within-iteration",
    ),
    pytest.param(
        """
func main() -> Unit
    var x: Int = 1
    let outer = fn() do
        let inner = fn() do
            x = x + 1
            return x
        end fn
        return inner()
    end fn
    print(outer())
    print(x)
    return ()
end func
""",
        "2\n2\n",
        id="nested-mutable-capture",
    ),
    pytest.param(
        """
func main() -> Unit
    var fs: List[() -> Int] = []
    for i: Int in [1, 2] do
        if true then
            let (x, y): (Int, Int) = (i, i * 10)
            fs = append(fs, fn() -> x + y)
        end if
        match Some(i) with
            | Some(x) -> fs = append(fs, fn() -> x)
            | None -> print(0)
        end match
    end for
    print(fs[0]())
    print(fs[1]())
    print(fs[2]())
    print(fs[3]())
    return ()
end func
""",
        "11\n1\n22\n2\n",
        id="nested-block-and-pattern-bindings",
    ),
]


BACKENDS = [
    _interpreter_output,
    _compiled_python_output,
    pytest.param(
        _compiled_js_output,
        marks=pytest.mark.skipif(not HAS_NODE, reason="Node.js not available"),
    ),
]


@pytest.mark.parametrize("backend", BACKENDS)
def test_named_argument_effects_follow_source_order(backend):
    assert backend(NAMED_ARGUMENT_PROGRAM) == (
        "3\n1\n2\n123\n6\n2\n246\n2\n1\n12\nfalse\n2\n1\n3\n1\n[142, 143]\n"
    )


@pytest.mark.parametrize("source, expected", CLOSURE_PROGRAMS)
@pytest.mark.parametrize("backend", BACKENDS)
def test_lexical_closure_bindings(backend, source, expected):
    assert backend(source) == expected


@pytest.mark.parametrize("backend", BACKENDS)
def test_empty_loop_body(backend):
    source = """
func main() -> Unit
    for x: Int in [1, 2] do
    end for
    print(1)
    return ()
end func
"""
    assert backend(source) == "1\n"


@pytest.mark.parametrize("backend", BACKENDS)
def test_overflowing_float_literals_and_patterns(backend):
    literal = "9" * 400 + ".0"
    source = f"""
func main() -> Unit
    let x: Float = {literal}
    print(x > 0.0)
    print(-{literal} < 0.0)
    match x with
        | {literal} -> print(true)
        | _ -> print(false)
    end match
    return ()
end func
"""
    assert backend(source) == "true\ntrue\ntrue\n"


@pytest.mark.parametrize("backend", BACKENDS)
def test_comprehension_captures_each_iteration(backend):
    source = """
func main() -> Unit
    let fs = [fn() -> x for x: Int in [1, 2, 3]]
    print(fs[0]())
    print(fs[1]())
    print(fs[2]())
    return ()
end func
"""
    assert backend(source) == "1\n2\n3\n"


@pytest.mark.parametrize("backend", BACKENDS)
def test_comprehension_lambda_local_shadow_keeps_live_binding(backend):
    source = """
func main() -> Unit
    let fs = [fn() do
        var x: Int = 1
        let f = fn() -> x
        x = 2
        return f()
    end fn for x: Int in [10]]
    print(fs[0]())
    return ()
end func
"""
    assert backend(source) == "2\n"
