"""Unit tests for the value snapshot helpers on both Python backends."""

from geno._runtime_support import _geno_deepcopy
from geno.value_copy import copy_value
from geno.values import ConstructorValue


def test_copy_value_gives_aliased_siblings_independent_copies() -> None:
    """Two fields that held one object must not share one copy (#129)."""
    point = ConstructorValue("Pt", {"x": 1})
    pair = ConstructorValue("Pair", {"a": point, "b": point})
    copied = copy_value(pair)
    assert copied == pair
    assert copied.fields["a"] is not copied.fields["b"]
    shared = [point]
    copied_lists = copy_value([shared, shared])
    assert copied_lists[0] is not copied_lists[1]


def test_copy_value_still_terminates_on_a_cycle() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)
    copied = copy_value(cyclic)
    assert copied[0] is copied
    assert copied is not cyclic


def test_geno_deepcopy_gives_aliased_siblings_independent_copies() -> None:
    shared = [1, 2]
    copied = _geno_deepcopy({"a": shared, "b": shared})
    assert copied == {"a": [1, 2], "b": [1, 2]}
    assert copied["a"] is not copied["b"]
    nested = [shared, (shared, shared)]
    copied_nested = _geno_deepcopy(nested)
    assert copied_nested[0] is not copied_nested[1][0]
    assert copied_nested[1][0] is not copied_nested[1][1]


def test_geno_deepcopy_still_terminates_on_a_cycle() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)
    copied = _geno_deepcopy(cyclic)
    assert copied[0] is copied
    assert copied is not cyclic


def test_shared_snapshots_keep_shared_parts_shared() -> None:
    """`let` snapshots stay linear for values built from shared parts."""
    point = ConstructorValue("Pt", {"x": 1})
    copied = copy_value(ConstructorValue("Pair", {"a": point, "b": point}), share=True)
    assert copied.fields["a"] is copied.fields["b"]
    shared = [1]
    copied_py = _geno_deepcopy([shared, shared], None, True)
    assert copied_py[0] is copied_py[1]


def test_let_bound_shared_tree_stays_linear(tmp_path) -> None:
    """A tree whose children are one node must not be expanded by `let`."""
    import subprocess
    import sys

    program = tmp_path / "tree.geno"
    program.write_text(
        "type Tree = Leaf | Node(left: Tree, right: Tree)\n"
        "\n"
        "func build(depth: Int) -> Tree\n"
        "    example 0 -> Leaf\n"
        "    if depth <= 0 then\n"
        "        return Leaf\n"
        "    end if\n"
        "    let child: Tree = build(depth - 1)\n"
        "    return Node(child, child)\n"
        "end func\n"
        "\n"
        "func height(t: Tree) -> Int\n"
        "    example Leaf -> 0\n"
        "    return match t with\n"
        "        | Leaf -> 0\n"
        "        | Node(l, _) -> 1 + height(l)\n"
        "    end match\n"
        "end func\n"
        "\n"
        "func main() -> Unit\n"
        "    let tree: Tree = build(60)\n"
        "    print(height(tree))\n"
        "end func\n",
        encoding="utf-8",
    )
    for extra in ([], ["--unsafe"]):
        result = subprocess.run(
            [sys.executable, "-m", "geno", "run", *extra, str(program)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.stdout.strip() == "60", (extra, result.stderr[-400:])
