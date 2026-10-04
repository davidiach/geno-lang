"""A name exported by two unaliased imports must be qualified (#132)."""

from __future__ import annotations

import pytest

from geno.parser import parse
from geno.typechecker import TypeChecker
from geno.types import TypeError as GenoTypeError

UTILS = "func double(x: Int) -> Int\n  example 3 -> 6\n  return x * 2\nend func\n"
OTHER = "func double(x: Int) -> Int\n  example 3 -> 7\n  return x * 2 + 1\nend func\n"


def _modules():
    return {
        "Utils": parse(UTILS, filename="<module:Utils>"),
        "Other": parse(OTHER, filename="<module:Other>"),
    }


def _check(main_source: str) -> None:
    TypeChecker().check_program(parse(main_source), modules=_modules())


@pytest.mark.parametrize(
    "use",
    ["print(double(21))", "let f = double\n  print(f(1))"],
    ids=["call", "function-value"],
)
def test_bare_use_of_an_ambiguous_import_is_rejected(use):
    source = f"import Utils\nimport Other\n\nfunc main() -> Unit\n  {use}\nend func\n"
    with pytest.raises(
        GenoTypeError, match=r"ambiguous.*Utils\.double or Other\.double"
    ):
        _check(source)


def test_qualified_uses_are_accepted():
    _check(
        "import Utils\nimport Other\n\nfunc main() -> Unit\n"
        "  print(Utils.double(1))\n  print(Other.double(1))\nend func\n"
    )


def test_local_definition_wins_over_both_imports():
    _check(
        "import Utils\nimport Other\n\n"
        "func double(x: Int) -> Int\n  example 1 -> 0\n  return 0\nend func\n\n"
        "func main() -> Unit\n  print(double(1))\nend func\n"
    )


def test_local_binding_shadows_the_ambiguous_name():
    _check(
        "import Utils\nimport Other\n\nfunc main() -> Unit\n"
        "  let double = 4\n  print(double)\nend func\n"
    )


def test_aliased_import_does_not_make_a_name_ambiguous():
    _check(
        "import Utils\nimport Other as O\n\nfunc main() -> Unit\n"
        "  print(double(1))\n  print(O.double(1))\nend func\n"
    )


def test_project_graph_rejects_the_ambiguous_name(tmp_path):
    from geno.dependency_graph import DependencyGraph
    from geno.project_graph import ProjectGraph

    (tmp_path / "geno.toml").write_text(
        'entrypoint = "Main"\nfiles = ["Main", "Utils", "Other"]\n'
    )
    (tmp_path / "Utils.geno").write_text(UTILS)
    (tmp_path / "Other.geno").write_text(OTHER)
    (tmp_path / "Main.geno").write_text(
        "import Utils\nimport Other\n\nfunc main() -> Unit\n  print(double(21))\nend func\n"
    )
    graph = DependencyGraph.resolve(ProjectGraph.discover(tmp_path))
    with pytest.raises(GenoTypeError, match="ambiguous"):
        TypeChecker().check_project_graph(graph)


def test_local_trait_method_wins_over_both_imports():
    _check(
        "import Utils\nimport Other\n\n"
        "type Point = Point(x: Int)\n\n"
        "trait Doubler\n  func double(self: Self) -> Int\nend trait\n\n"
        "impl Doubler for Point\n"
        "  func double(self: Point) -> Int\n    example Point(1) -> 2\n"
        "    return self.x * 2\n  end func\nend impl\n\n"
        "func main() -> Unit\n  print(double(Point(1)))\nend func\n"
    )
