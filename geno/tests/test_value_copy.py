"""Snapshots stay linear and field writes stay local to their path (#129)."""

import subprocess
import sys

import pytest

from geno._runtime_support import _geno_own_index
from geno.compiler import compile_to_python

SHARED_TREE = """type Tree = Leaf | Node(left: Tree, right: Tree, n: Int)

func build(depth: Int) -> Tree
    example 0 -> Leaf
    if depth <= 0 then
        return Leaf
    end if
    let child: Tree = build(depth - 1)
    return Node(child, child, depth)
end func

func height(t: Tree) -> Int
    example Leaf -> 0
    return match t with
        | Leaf -> 0
        | Node(l, _, _) -> 1 + height(l)
    end match
end func

func main() -> Unit
    let fixed: Tree = build(60)
    var tree: Tree = build(60)
    tree = build(61)
    print(height(fixed) + height(tree))
end func
"""


@pytest.mark.parametrize("flags", [[], ["--unsafe"]], ids=["process", "unsafe"])
def test_shared_tree_snapshots_stay_linear(tmp_path, flags) -> None:
    """A tree whose children are one node is never expanded by a snapshot."""
    program = tmp_path / "tree.geno"
    program.write_text(SHARED_TREE, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "geno", "run", *flags, str(program)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.stdout == "121\n", result.stderr[-400:]


def test_compiled_field_write_unshares_its_path() -> None:
    code = compile_to_python(
        "type Pt = Pt(x: Int)\n"
        "type Pair = Pair(a: Pt, b: Pt)\n"
        "func main() -> Unit\n"
        "    var pair = Pair(Pt(1), Pt(1))\n"
        "    pair.a.x = 2\n"
        "end func\n"
    )
    assert "_object_setattr(_geno_own_field(pair, 'a'), 'x'" in code


def test_own_helpers_leave_reference_containers_and_scalars_alone() -> None:
    assert _geno_own_index("abc", 1) == "b"
    shared = [1]
    outer = [shared, shared]
    owned = _geno_own_index(outer, 0)
    assert owned == [1] and owned is not shared and outer[1] is shared
