"""
Tests for entrypoint discovery and result classification
========================================================

`classify_entrypoint_result` answers what an executable host must do with the
selected entrypoint's value.  It reads the resolved static return annotation,
so these tests cover both sources of that answer: the type the typechecker
records on a checked program, and the declared annotation resolved directly
when compilation runs with type checking turned off.
"""

import pytest

from geno.ast_nodes import Program
from geno.entrypoint import (
    EntrypointResultKind,
    classify_entrypoint_result,
    find_entrypoint_main,
)
from geno.parser import parse
from geno.typechecker import TypeChecker

UNIT_MAIN = """
func main() -> Unit
  print(1)
end func
"""

INT_MAIN = """
func main() -> Int
  return 2
end func
"""


def _checked(source: str) -> Program:
    """Parse and typecheck *source*, so `main` carries its resolved type."""
    program = parse(source)
    TypeChecker().check_program(program)
    return program


class TestFindEntrypointMain:
    def test_finds_the_programs_own_main(self):
        main_def = find_entrypoint_main(parse(INT_MAIN))
        assert main_def is not None
        assert main_def.name == "main"

    def test_returns_none_without_a_main(self):
        source = """
func helper() -> Int
  example () -> 1
  return 1
end func
"""
        assert find_entrypoint_main(parse(source)) is None


class TestClassifyCheckedProgram:
    """The typechecker's resolved return type drives classification."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            pytest.param(UNIT_MAIN, EntrypointResultKind.UNIT, id="unit"),
            pytest.param(INT_MAIN, EntrypointResultKind.INT, id="int"),
            pytest.param(
                """
type Status = Int
func main() -> Status
  return 2
end func
""",
                EntrypointResultKind.INT,
                id="alias_to_int",
            ),
            pytest.param(
                """
type Status = Int
type Code = Status
func main() -> Code
  return 2
end func
""",
                EntrypointResultKind.INT,
                id="chained_alias_to_int",
            ),
            pytest.param(
                """
type Nothing = Unit
func main() -> Nothing
  print(1)
end func
""",
                EntrypointResultKind.UNIT,
                id="alias_to_unit",
            ),
            pytest.param(
                """
async func main() -> Int
  return 2
end func
""",
                EntrypointResultKind.INT,
                id="async_main_returning_int",
            ),
            pytest.param(
                """
async func twice(x: Int) -> Int
  return x * 2
end func

func main() -> Async[Int]
  return twice(21)
end func
""",
                EntrypointResultKind.OTHER,
                id="unawaited_async_value",
            ),
            pytest.param(
                """
func main() -> String
  return "x"
end func
""",
                EntrypointResultKind.OTHER,
                id="string",
            ),
            pytest.param(
                """
func main() -> List[Int]
  return [1]
end func
""",
                EntrypointResultKind.OTHER,
                id="list",
            ),
            pytest.param(
                """
func helper() -> Int
  example () -> 1
  return 1
end func
""",
                EntrypointResultKind.MISSING,
                id="no_main",
            ),
        ],
    )
    def test_classification(self, source: str, expected: EntrypointResultKind):
        assert classify_entrypoint_result(_checked(source)) is expected


class TestClassifyFromAnnotation:
    """Without a typechecked program the declared annotation is resolved."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            pytest.param(INT_MAIN, EntrypointResultKind.INT, id="int"),
            pytest.param(UNIT_MAIN, EntrypointResultKind.UNIT, id="unit"),
            pytest.param(
                "type Status = Int\nfunc main() -> Status\n  return 2\nend func\n",
                EntrypointResultKind.INT,
                id="alias_to_int",
            ),
            pytest.param(
                "type S = Int\ntype C = S\nfunc main() -> C\n  return 2\nend func\n",
                EntrypointResultKind.INT,
                id="chained_alias_to_int",
            ),
            pytest.param(
                "type Boxed[T] = T\nfunc main() -> Boxed[Int]\n  return 2\nend func\n",
                EntrypointResultKind.INT,
                id="generic_alias_applied_to_int",
            ),
            pytest.param(
                "func main() -> Async[Int]\n  return twice(21)\nend func\n",
                EntrypointResultKind.OTHER,
                id="unawaited_async_value",
            ),
            pytest.param(
                "func main() -> Widget\n  return build()\nend func\n",
                EntrypointResultKind.OTHER,
                id="user_defined_type",
            ),
        ],
    )
    def test_classification(self, source: str, expected: EntrypointResultKind):
        assert classify_entrypoint_result(parse(source)) is expected

    def test_alias_cycle_terminates(self):
        """A cyclic alias must not loop; it simply resolves to nothing."""
        source = "type A = B\ntype B = A\nfunc main() -> A\n  return 2\nend func\n"
        assert classify_entrypoint_result(parse(source)) is EntrypointResultKind.OTHER

    def test_alias_imported_into_the_entry_program_resolves(self):
        """An alias keeps the scope it was declared in, across modules."""
        modules = {"Codes": parse("export type Status = Int\n")}
        entry = parse("import Codes\nfunc main() -> Status\n  return 2\nend func\n")
        assert classify_entrypoint_result(entry, modules) is EntrypointResultKind.INT

    def test_generic_argument_resolves_in_the_scope_it_was_written_in(self):
        """An imported generic alias must not read its argument in its own scope.

        `Identity` is declared in `Lib`, so its body is resolved there, but the
        argument `Status` is written in the entry program and is visible only
        there.  Resolving the argument after the scope switch loses it.  The
        typechecked path answers INT, so the annotation path must agree.
        """
        source = (
            "import Lib\n"
            "type Status = Int\n"
            "func main() -> Identity[Status]\n  return 2\nend func\n"
        )
        library = "export type Identity[T] = T\n"

        unchecked = parse(source)
        assert (
            classify_entrypoint_result(unchecked, {"Lib": parse(library)})
            is EntrypointResultKind.INT
        )

        checked, modules = parse(source), {"Lib": parse(library)}
        TypeChecker().check_program(checked, modules=modules)
        assert classify_entrypoint_result(checked, modules) is EntrypointResultKind.INT

    def test_generic_argument_keeps_the_callers_meaning_of_a_shadowed_name(self):
        """The caller's `Status` is the argument, even though `Lib` has one too."""
        modules = {
            "Lib": parse("export type Identity[T] = T\nexport type Status = String\n")
        }
        entry = parse(
            "import Lib\n"
            "type Status = Int\n"
            "func main() -> Identity[Status]\n  return 2\nend func\n"
        )
        assert classify_entrypoint_result(entry, modules) is EntrypointResultKind.INT

    def test_generic_alias_applied_to_its_own_parameter(self):
        """`Id[T]` inside `Wrap[T]` binds `Id`'s `T` to the caller's, not itself."""
        source = (
            "type Id[T] = T\n"
            "type Wrap[T] = Id[T]\n"
            "func main() -> Wrap[Int]\n  return 2\nend func\n"
        )
        assert classify_entrypoint_result(parse(source)) is EntrypointResultKind.INT

    def test_generic_argument_nested_in_another_alias_application(self):
        """A parameter reached through a nested application still resolves."""
        source = (
            "type Id[T] = T\n"
            "type Box[T] = T\n"
            "type Loop[T] = Id[Box[T]]\n"
            "func main() -> Loop[Int]\n  return 2\nend func\n"
        )
        assert classify_entrypoint_result(parse(source)) is EntrypointResultKind.INT


class TestEntrypointOwnership:
    def test_imported_main_is_not_the_entrypoint(self):
        """Import discovery never moves entrypoint ownership."""
        modules = {"Lib": parse(INT_MAIN)}
        entry = parse("import Lib\nfunc start() -> Unit\n  print(1)\nend func\n")

        assert find_entrypoint_main(entry) is None
        assert (
            classify_entrypoint_result(entry, modules) is EntrypointResultKind.MISSING
        )
