"""Typed JSON: ``json_decode[T]`` and ``json_encode`` (proposal 0021).

Both are type-directed. The checker turns the type a call works on into a
codec (``geno/json_shape.py``), and each backend's runtime walks a value and
the codec together, so the three backends decode, encode and report errors
identically.
"""

import json

import pytest

import geno
from geno.diagnostics import ErrorCode
from geno.json_shape import JsonTypeError, build_codec
from geno.test_runner import find_divergences, run_test_suite
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _compiled_js_output,
    _compiled_js_project_output,
    _compiled_python_output,
    _compiled_python_project_output,
    _interpreter_output,
    _interpreter_project_output,
)
from geno.types import FloatType, FuncType, IntType, ListType, MapType, StringType

needs_node = pytest.mark.skipif(not HAS_NODE, reason="Node.js not available")
BACKENDS = [
    _interpreter_output,
    _compiled_python_output,
    pytest.param(_compiled_js_output, marks=needs_node),
]

TYPES = """
type Color = Red | Green
type Shape = Circle(r: Float) | Empty | Rect(w: Int, h: Int)
type User = User(name: String, age: Option[Int], tags: List[String], fav: Color)
type Tree = Leaf | Node(left: Tree, value: Int, right: Tree)
type Box[T] = Box(item: T)
"""


def _geno_string(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


# (type, JSON text, what `print` shows for the decoded Result)
DECODES = [
    ("Int", "42", "Ok(value: 42)"),
    ("Int", "-0", "Ok(value: 0)"),
    ("Float", "1.5", "Ok(value: 1.5)"),
    ("Float", "3", "Ok(value: 3.0)"),
    ("String", '"hé\\n"', 'Ok(value: "hé\\n")'),
    ("Bool", "true", "Ok(value: true)"),
    ("Unit", "null", "Ok(value: ())"),
    ("List[Int]", "[1, 2]", "Ok(value: [1, 2])"),
    ("Array[Float]", "[1, 2.5]", "Ok(value: Array([1.0, 2.5]))"),
    ("Vec[String]", '["a"]', 'Ok(value: Vec(["a"]))'),
    ("Set[Int]", "[3, 1, 3]", "Ok(value: Set({1, 3}))"),
    ("(Int, String)", '[1, "a"]', 'Ok(value: (1, "a"))'),
    ("Option[Int]", "null", "Ok(value: None)"),
    ("Option[Int]", "7", "Ok(value: Some(value: 7))"),
    ("Map[String, Float]", '{"b": 1, "a": 2.5}', 'Ok(value: {"b": 1.0, "a": 2.5})'),
    ("Result[Int, String]", '{"ok": 1}', "Ok(value: Ok(value: 1))"),
    ("Result[Int, String]", '{"err": "no"}', 'Ok(value: Err(error: "no"))'),
    ("JsonValue", '{"k": [1, null]}', None),
    ("Color", '"Green"', "Ok(value: Green)"),
    ("Shape", '{"type": "Circle", "r": 2}', "Ok(value: Circle(r: 2.0))"),
    ("Shape", '{"type": "Empty"}', "Ok(value: Empty)"),
    (
        "User",
        '{"name": "Al", "tags": [], "fav": "Red", "ignored": [1]}',
        'Ok(value: User(name: "Al", age: None, tags: [], fav: Red))',
    ),
    ("Box[List[Color]]", '{"item": ["Red"]}', "Ok(value: Box(item: [Red]))"),
    (
        "Tree",
        '{"type":"Node","left":{"type":"Leaf"},"value":1,"right":{"type":"Leaf"}}',
        "Ok(value: Node(left: Leaf, value: 1, right: Leaf))",
    ),
    # Mismatches: the path, what the type wants, what the JSON holds.
    ("Int", "1.0", 'Err(error: "$: expected Int, got Float")'),
    ("Int", '"1"', 'Err(error: "$: expected Int, got String")'),
    ("Float", "true", 'Err(error: "$: expected Float, got Bool")'),
    ("String", "null", 'Err(error: "$: expected String, got null")'),
    ("Unit", "0", 'Err(error: "$: expected null, got Int")'),
    ("List[Int]", "{}", 'Err(error: "$: expected array, got object")'),
    ("List[Int]", "[1, [2]]", 'Err(error: "$[1]: expected Int, got array")'),
    ("(Int, String)", "[1]", 'Err(error: "$: expected an array of 2 items, got 1")'),
    (
        "Map[String, Int]",
        '{"a b": "x"}',
        'Err(error: "$[\\"a b\\"]: expected Int, got String")',
    ),
    (
        "Result[Int, String]",
        '{"ok": 1, "err": "x"}',
        'Err(error: "$: expected an object with \\"ok\\" or \\"err\\"")',
    ),
    (
        "Result[Int, String]",
        '{"ok": "x"}',
        'Err(error: "$.ok: expected Int, got String")',
    ),
    (
        "Color",
        '"Blue"',
        'Err(error: "$: expected one of \\"Red\\", \\"Green\\", got \\"Blue\\"")',
    ),
    ("Color", "1", 'Err(error: "$: expected String, got Int")'),
    ("Shape", '{"r": 1}', 'Err(error: "$: missing field \\"type\\"")'),
    ("Shape", '{"type": 1}', 'Err(error: "$.type: expected String, got Int")'),
    (
        "Shape",
        '{"type": "Tri"}',
        'Err(error: "$.type: expected one of \\"Circle\\", \\"Empty\\", \\"Rect\\", '
        'got \\"Tri\\"")',
    ),
    ("Shape", '{"type": "Rect", "w": 1}', 'Err(error: "$: missing field \\"h\\"")'),
    ("User", "[]", 'Err(error: "$: expected object, got array")'),
    (
        "User",
        '{"name": "Al", "tags": ["a", 2], "fav": "Red"}',
        'Err(error: "$.tags[1]: expected String, got Int")',
    ),
    (
        "List[User]",
        '[{"name": "Al", "tags": [], "fav": "Red", "age": "old"}]',
        'Err(error: "$[0].age: expected Int, got String")',
    ),
    (
        "Box[Box[Int]]",
        '{"item": {"item": null}}',
        'Err(error: "$.item.item: expected Int, got null")',
    ),
]


def _decode_program() -> tuple[str, str]:
    lines = []
    expected = []
    for type_name, text, shown in DECODES:
        if shown is None:
            # A JsonValue passes through: compare with json_parse.
            lines.append(
                f"    print(json_decode[{type_name}]({_geno_string(text)}) "
                f"== json_parse({_geno_string(text)}))"
            )
            expected.append("true")
            continue
        lines.append(f"    print(json_decode[{type_name}]({_geno_string(text)}))")
        expected.append(shown)
    source = (
        TYPES + "\nfunc main() -> Unit with io\n" + "\n".join(lines) + "\nend func\n"
    )
    return source, "".join(line + "\n" for line in expected)


@pytest.mark.parametrize("backend", BACKENDS)
def test_decoding_every_kind_of_type_and_mismatch(backend):
    source, expected = _decode_program()
    assert backend(source) == expected


ENCODES = (
    TYPES
    + """
func main() -> Unit with io
    print(json_encode(42))
    print(json_encode(1.5))
    print(json_encode(2.0))
    print(json_encode("hé\\n\\"q\\""))
    print(json_encode(true))
    print(json_encode(()))
    print(json_encode([1, 2]))
    print(json_encode(array_from_list([1.0])))
    print(json_encode(vec_from_list(["a"])))
    print(json_encode(set_from_list(["b", "a", "b"])))
    print(json_encode((1, "a", None)))
    let absent: Option[Int] = None
    print(json_encode([Some(1), absent]))
    print(json_encode({"b": 1, "a": 2}))
    print(json_encode([]))
    print(json_encode(None))
    let good: Result[Int, String] = Ok(1)
    let bad: Result[Int, String] = Err("no")
    print(json_encode([good, bad]))
    print(json_encode(JsonObject([("k", JsonArray([JsonInt(1), JsonNull]))])))
    print(json_encode([Red, Green]))
    print(json_encode([Circle(2), Empty, Rect(1, 2)]))
    print(json_encode(User("Al", Some(3), ["x"], Green)))
    print(json_encode(User("Bo", None, [], Red)))
    print(json_encode(Box([Box(1.0)])))
    print(json_encode(Node(Leaf, 1, Node(Leaf, 2, Leaf))))
    print(json_encode_pretty(User("Al", None, ["x"], Red), 2))
    print(json_encode_pretty(indent: 0, value: [1]))
end func
"""
)

ENCODES_OUTPUT = """42
1.5
2.0
"h\\u00e9\\n\\"q\\""
true
null
[1,2]
[1.0]
["a"]
["a","b"]
[1,"a",null]
[1,null]
{"b":1,"a":2}
[]
null
[{"ok":1},{"err":"no"}]
{"k":[1,null]}
["Red","Green"]
[{"type":"Circle","r":2.0},{"type":"Empty"},{"type":"Rect","w":1,"h":2}]
{"name":"Al","age":3,"tags":["x"],"fav":"Green"}
{"name":"Bo","age":null,"tags":[],"fav":"Red"}
{"item":[{"item":1.0}]}
{"type":"Node","left":{"type":"Leaf"},"value":1,"right":{"type":"Node","left":{"type":"Leaf"},"value":2,"right":{"type":"Leaf"}}}
{
  "name": "Al",
  "age": null,
  "tags": [
    "x"
  ],
  "fav": "Red"
}
[1]
"""


@pytest.mark.parametrize("backend", BACKENDS)
def test_encoding_every_kind_of_type(backend):
    assert backend(ENCODES) == ENCODES_OUTPUT


CONTEXT = (
    TYPES
    + """
func from_return(text: String) -> Result[User, String]
    example "1" -> Err("$: expected object, got Int")
    return json_decode(text)
end func

func name_of(user: User) -> String
    example User("Al", None, [], Red) -> "Al"
    return user.name
end func

func load(text: String) -> Result[String, String]
    example "[]" -> Err("$: expected object, got array")
    let user: User = json_decode(text)?
    let again = name_of(json_decode(text)?)
    let colors: List[Color] = json_decode("[\\"Red\\"]")?
    return Ok(user.name + again + to_string(length(colors)))
end func

func round_trip(tree: Tree) -> Bool
    example Leaf -> true
    example Node(Leaf, 3, Node(Leaf, 4, Leaf)) -> true
    return json_decode[Tree](json_encode(tree)) == Ok(tree)
end func

func main() -> Unit with io
    let text = "{\\"name\\": \\"Al\\", \\"tags\\": [], \\"fav\\": \\"Red\\"}"
    print(from_return(text))
    print(load(text))
    print(round_trip(Node(Node(Leaf, 1, Leaf), 2, Leaf)))
    match json_decode[Shape]("{\\"type\\": \\"Empty\\"}") with
        | Ok(shape) -> print(shape)
        | Err(message) -> print(message)
    end match
    print(json_decode[Int]("[1,"))
end func
"""
)


@pytest.mark.parametrize("backend", BACKENDS)
def test_the_type_comes_from_brackets_or_from_the_context(backend):
    """A return type, an annotated `let`, an argument and `[T]` each name it."""
    lines = backend(CONTEXT).splitlines()

    assert lines[:4] == [
        'Ok(value: User(name: "Al", age: None, tags: [], fav: Red))',
        'Ok(value: "AlAl1")',
        "true",
        "Empty",
    ]
    # Text that is not JSON is json_parse's error, worded by each host.
    assert lines[4].startswith("Err(error: ")


def _errors(source: str) -> list[tuple[str, str]]:
    result = geno.run(source)
    assert not result.ok
    return [(d.code.value, d.message) for d in result.diagnostics]


def _function(body: str, extra: str = "") -> str:
    return (
        TYPES
        + extra
        + "\nfunc main() -> Result[Unit, String] with io\n"
        + body
        + "\n    return Ok(())\nend func\n"
    )


@pytest.mark.parametrize(
    ("body", "extra", "fragment"),
    [
        (
            '    print(json_decode[Map[Int, String]]("{}"))',
            "",
            "Map[Int, String] has no JSON form: a JSON object's keys are strings",
        ),
        (
            '    print(json_decode[Handler]("{}"))',
            "type Handler = Handler(run: (Int) -> Int)\n",
            "(Int) -> Int has no JSON form: a function is not data "
            "(in field 'run' of Handler)",
        ),
        (
            "    print(json_encode(fn(x: Int) -> x))",
            "",
            "has no JSON form: a function is not data",
        ),
        (
            "    let m: MutableMap[String, Int] = mutable_map_new()\n"
            "    print(json_encode(m))",
            "",
            "MutableMap[String, Int] has no JSON form: use a Map[String, V]",
        ),
        (
            '    print(json_decode[Box[Handler]]("{}"))',
            "type Handler = Handler(run: (Int) -> Int)\n",
            "(in field 'run' of Handler of field 'item' of Box)",
        ),
    ],
)
def test_a_type_with_no_json_form_is_rejected_by_the_checker(body, extra, fragment):
    [(code, message)] = _errors(_function(body, extra))

    assert code == ErrorCode.TYPE_JSON_FORM.value == "E332"
    assert fragment in message


def test_a_type_parameter_has_no_json_form():
    source = (
        'func save[T](value: T) -> String\n    example 1 -> "1"\n'
        "    return json_encode(value)\nend func\n"
    )

    [(code, message)] = _errors(source)

    assert code == "E332"
    assert "its type is not known where the call is written" in message


def test_decode_needs_to_know_its_type():
    [(code, message)] = _errors(_function('    print(json_decode("1"))'))

    assert code == ErrorCode.TYPE_GENERIC_INFERENCE.value
    assert "json_decode[User](text)" in message


@pytest.mark.parametrize("name", ["json_decode", "json_encode", "json_encode_pretty"])
def test_the_builtins_cannot_be_function_values(name):
    errors = _errors(_function(f"    let f = {name}"))

    assert ("E332", True) in [
        (code, f"'{name}' can only be called directly" in message)
        for code, message in errors
    ]


def test_wrong_arguments_are_type_errors():
    errors = _errors(
        _function(
            "    print(json_decode[Int](1))\n"
            '    print(json_encode_pretty([1], "2"))\n'
            "    print(json_encode())"
        )
    )

    messages = [message for _, message in errors]
    assert "Argument 'text' type mismatch: expected String, got Int" in messages
    assert "Argument 'indent' type mismatch: expected Int, got String" in messages
    assert "json_encode is missing its argument: value" in messages


DEEP = (
    TYPES
    + """
func nest(depth: Int, tree: Tree) -> Tree
    example (0, Leaf) -> Leaf
    if depth == 0 then
        return tree
    end if
    return nest(depth - 1, Node(tree, depth, Leaf))
end func

func main() -> Unit with io
    print(length(json_encode(nest(100, Leaf))) > 0)
    print(json_encode(nest(200, Leaf)))
end func
"""
)


@pytest.mark.parametrize(
    "backend",
    ["interpreter", "python", pytest.param("js", marks=needs_node)],
)
def test_encoding_stops_at_the_json_nesting_limit(backend):
    """As json_parse refuses text nested past 128 levels, so does encoding."""
    from geno.tests.test_tail_calls import _failure

    assert "json_encode: value nested too deeply (max 128)" in _failure(backend, DEEP)


MODULE = """
export type Inner = Inner(n: Int, label: Option[String])

export func inner(n: Int) -> Inner
    example 1 -> Inner(1, None)
    return Inner(n, None)
end func
"""

MIDDLE = """
import Inner

export type Outer = Outer(items: List[Inner], first: Inner)

export func outer(n: Int) -> Outer
    example 1 -> Outer([], Inner(1, None))
    return Outer([], Inner.inner(n))
end func
"""

IMPORTER = """
import Middle

func main() -> Unit with io
    let text = "{\\"items\\": [{\\"n\\": 2, \\"label\\": \\"x\\"}], \\"first\\": {\\"n\\": 1}}"
    let decoded: Result[Outer, String] = json_decode(text)
    print(decoded)
    print(json_encode(Middle.outer(5)))
end func
"""


@pytest.mark.parametrize(
    "backend",
    [
        _interpreter_project_output,
        _compiled_python_project_output,
        pytest.param(_compiled_js_project_output, marks=needs_node),
    ],
)
def test_a_type_from_a_module_the_caller_does_not_import(backend):
    """`Inner` is reached only through `Outer`: its constructor is not in the
    scope the call is written in."""
    assert backend(IMPORTER, {"Inner": MODULE, "Middle": MIDDLE}) == (
        'Ok(value: Outer(items: [Inner(n: 2, label: Some(value: "x"))], '
        "first: Inner(n: 1, label: None)))\n"
        '{"items":[],"first":{"n":5,"label":null}}\n'
    )


def test_an_ordinary_program_registers_no_constructors():
    """Only a program that calls a typed-JSON builtin pays for it."""
    from geno.compiler import compile_to_python
    from geno.js_compiler import compile_to_js

    plain = TYPES + "\nfunc main() -> Unit with io\n    print(Red)\nend func\n"

    def generated(python: str) -> str:
        return python.split("# Generated Code Follows")[-1]

    assert "_GENO_JSON_CTORS[" not in generated(compile_to_python(plain))
    assert "_GENO_JSON_CTORS.set(" not in compile_to_js(plain)
    assert "_GENO_JSON_CTORS[('.Color', 'Red')]" in generated(
        compile_to_python(ENCODES)
    )
    assert '_GENO_JSON_CTORS.set(".Color.Red"' in compile_to_js(ENCODES)


@pytest.mark.parametrize(
    "backend",
    [
        _interpreter_project_output,
        _compiled_python_project_output,
        pytest.param(_compiled_js_project_output, marks=needs_node),
    ],
)
def test_typed_json_resolves_constructors_in_their_defining_modules(backend):
    left = r"""
type Value = Shared(label: String)

export func load() -> String
    example () -> "left"
    match json_decode[Value]("{\"label\":\"left\"}") with
        | Ok(Shared(label)) -> return label
        | Err(_) -> return "invalid"
    end match
end func
"""
    right = r"""
type Value = Shared(first: Int, second: Int)

export func load() -> Int
    example () -> 3
    match json_decode[Value]("{\"first\":1,\"second\":2}") with
        | Ok(Shared(first, second)) -> return first + second
        | Err(_) -> return 0
    end match
end func
"""
    main = """
import Left as L
import Right as R

func main() -> Unit with io
    print(L.load())
    print(R.load())
end func
"""
    assert backend(main, {"Left": left, "Right": right}) == "left\n3\n"


@pytest.mark.parametrize("backend", BACKENDS)
def test_pretty_encoding_mixed_arguments_preserves_evaluation_order(backend):
    source = """
func get_values() -> List[Int] with io
    example () -> [1, 2]
    print("value")
    return [1, 2]
end func

func get_indent() -> Int with io
    example () -> 2
    print("indent")
    return 2
end func

func main() -> Unit with io
    print(json_encode_pretty(value: get_values(), get_indent()))
    print(json_encode_pretty(indent: get_indent(), get_values()))
end func
"""
    assert (
        backend(source)
        == "value\nindent\n[\n  1,\n  2\n]\nindent\nvalue\n[\n  1,\n  2\n]\n"
    )


@needs_node
def test_examples_with_typed_json_agree_on_every_backend(tmp_path):
    path = tmp_path / "codec.geno"
    path.write_text(CONTEXT, encoding="utf-8")

    suites = {
        name: run_test_suite([path], backend=name)
        for name in ("interpreter", "python", "js")
    }

    assert [suite.failed for suite in suites.values()] == [0, 0, 0]
    assert [suite.total for suite in suites.values()] == [5, 5, 5]
    assert find_divergences(suites) == []


def test_the_codec_of_a_type():
    codec = build_codec(MapType(StringType(), ListType(FloatType())), {})

    assert codec.shape == ("map", ("list", ("float",)))
    assert codec.types == {}
    with pytest.raises(JsonTypeError, match="a function is not data"):
        build_codec(FuncType((IntType(),), IntType()), {})


def test_brackets_on_a_function_that_hides_the_builtin_are_rejected():
    source = (
        'func json_decode(text: String) -> Int\n    example "a" -> 1\n'
        "    return length(text)\nend func\n\n"
        "func main() -> Unit with io\n"
        '    print(json_decode("abc"))\n'
        '    print(json_decode[Int]("abc"))\nend func\n'
    )

    [(code, message)] = _errors(source)

    assert code == "E332"
    assert "is not the builtin json_decode" in message


ALIASED = """
type Point = Point(x: Float, y: Float)
type Points = List[Point]

func main() -> Unit with io
    print(json_decode[Points](text: "[{\\"x\\": 1, \\"y\\": 2.5}]"))
    let points: Points = [Point(1, 2)]
    print(json_encode(value: points))
    print(json_decode[Set[Point]]("[{\\"x\\": 1, \\"y\\": 2}, {\\"x\\": 1, \\"y\\": 2}]"))
    print(json_decode[Option[Option[Int]]]("null"))
end func
"""


@pytest.mark.parametrize("backend", BACKENDS)
def test_aliases_named_arguments_and_sets_of_records(backend):
    assert backend(ALIASED) == (
        "Ok(value: [Point(x: 1.0, y: 2.5)])\n"
        '[{"x":1.0,"y":2.0}]\n'
        "Ok(value: Set({Point(x: 1.0, y: 2.0)}))\n"
        "Ok(value: None)\n"
    )


FIRST = """
export type AValue = Shared(left: Int)

export func parse_a(text: String) -> Result[AValue, String]
    example "1" -> Err("$: expected object, got Int")
    return json_decode(text)
end func

export func show_a(value: AValue) -> String
    example Shared(1) -> "{\\"left\\":1}"
    return json_encode(value)
end func
"""

SECOND = """
export type BValue = Shared(right: String)

export func parse_b(text: String) -> Result[BValue, String]
    example "1" -> Err("$: expected object, got Int")
    return json_decode(text)
end func

export func show_b(value: BValue) -> String
    example Shared("x") -> "{\\"right\\":\\"x\\"}"
    return json_encode(value)
end func
"""

BOTH = """
import A as First
import B as Second

func main() -> Unit with io
    match First.parse_a("{\\"left\\": 1}") with
        | Ok(value) -> print(First.show_a(value))
        | Err(message) -> print(message)
    end match
    match Second.parse_b("{\\"right\\": \\"z\\"}") with
        | Ok(value) -> print(Second.show_b(value))
        | Err(message) -> print(message)
    end match
end func
"""


@pytest.mark.parametrize(
    "backend",
    [
        _compiled_python_project_output,
        pytest.param(_compiled_js_project_output, marks=needs_node),
    ],
)
def test_two_types_may_each_have_a_constructor_of_one_name(backend):
    """Found in review: constructors were registered by their own name alone,
    so the module compiled later replaced the other's `Shared`. A constructor
    is found by its type's name and its own."""
    assert backend(BOTH, {"A": FIRST, "B": SECOND}) == '{"left":1}\n{"right":"z"}\n'


def test_a_type_that_nests_itself_at_ever_larger_types_is_rejected():
    """Found in review: building this codec recursed until the host's stack
    ran out. `Nest[Int]` contains `Nest[List[Int]]`, which contains
    `Nest[List[List[Int]]]`, without end."""
    source = (
        "type Nest[T] = Stop | More(next: Nest[List[T]])\n\n"
        "func main() -> Unit with io\n    let nest: Nest[Int] = Stop\n"
        "    print(json_encode(nest))\nend func\n"
    )

    [(code, message)] = _errors(source)

    assert code == "E332"
    assert "too complex to describe" in message


def test_a_regular_recursive_generic_type_still_has_a_codec():
    source = (
        "type Chain[T] = End | Link(head: T, rest: Chain[T])\n\n"
        "func main() -> Unit with io\n"
        "    let chain: Chain[Int] = Link(1, Link(2, End))\n"
        "    print(json_encode(chain))\nend func\n"
    )

    assert _interpreter_output(source) == (
        '{"type":"Link","head":1,"rest":{"type":"Link","head":2,'
        '"rest":{"type":"End"}}}\n'
    )
