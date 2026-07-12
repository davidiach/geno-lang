import textwrap

from geno._definition_index import DefinitionIndex, collect_definitions
from geno.lexer import Lexer
from geno.parser import Parser


def _parse_program(source: str):
    lexer = Lexer(textwrap.dedent(source), "<test>")
    parser = Parser(lexer.tokenize())
    return parser.parse_program()


def test_collect_definitions_indexes_program_contents():
    program = _parse_program(
        """
        type Circle = Circle(radius: Float)

        func helper(value: Int) -> Int
            return value
        end func

        trait Describable
            func describe(self: Self) -> String
        end trait

        impl Describable for Circle
            func describe(self: Circle) -> String
                return "Circle"
            end func
        end impl
        """
    )

    index = collect_definitions(program)

    assert set(index.type_defs) == {"Circle"}
    assert index.func_param_names["helper"] == ["value"]
    assert index.func_param_names["describe"] == ["self"]
    assert set(index.trait_defs) == {"Describable"}
    assert set(index.impl_defs) == {("Describable", "Circle")}
    assert index.trait_dispatch == {"describe": [("Describable", "Circle")]}


def test_collect_definitions_accumulates_into_existing_index():
    first = _parse_program(
        """
        type Circle = Circle(radius: Float)

        trait Describable
            func describe(self: Self) -> String
        end trait

        impl Describable for Circle
            func describe(self: Circle) -> String
                return "Circle"
            end func
        end impl
        """
    )
    second = _parse_program(
        """
        type Square = Square(size: Float)

        impl Describable for Square
            func describe(self: Square) -> String
                return "Square"
            end func
        end impl
        """
    )

    index = DefinitionIndex(func_param_names={"length": ["list"]})

    assert collect_definitions(first, into=index) is index
    collect_definitions(second, into=index)

    assert index.func_param_names["length"] == ["list"]
    assert set(index.type_defs) == {"Circle", "Square"}
    assert set(index.impl_defs) == {
        ("Describable", "Circle"),
        ("Describable", "Square"),
    }
    assert index.trait_dispatch["describe"] == [
        ("Describable", "Circle"),
        ("Describable", "Square"),
    ]
