"""User-defined generic functions: ``func name[T, U](...)`` (proposal 0004)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from geno.diagnostics import ErrorCode
from geno.doc_generator import _render_signature_html, parse_module
from geno.js_compiler import compile_to_js, generate_dts
from geno.lsp_server import _extract_user_func_sig
from geno.parser import ParseError, ParseErrors, parse
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _assert_expected_backend_outputs,
    _compiled_js_output,
    _compiled_js_project_output,
    _compiled_python_output,
    _compiled_python_project_output,
    _interpreter_output,
    _interpreter_project_output,
)
from geno.typechecker import TypeChecker
from geno.types import TypeError as GenoTypeError

REPO_ROOT = Path(__file__).resolve().parents[2]

SHOW = """func show[T](x: T) -> String
    example 1 -> "1"
    example "a" -> "a"
    return to_string(x)
end func

"""


def _check(source: str) -> None:
    TypeChecker().check_program(parse(source))


def _errors(source: str) -> list[GenoTypeError]:
    with pytest.raises(GenoTypeError) as info:
        _check(source)
    return list(getattr(info.value, "errors", [info.value]))


def _messages(source: str) -> str:
    return "\n".join(error.message for error in _errors(source))


def _assert_parity(source: str, expected: str) -> None:
    interp = _interpreter_output(source)
    py = _compiled_python_output(source)
    js = _compiled_js_output(source) if HAS_NODE else expected
    _assert_expected_backend_outputs(
        label="generic",
        context=source,
        expected=expected,
        interp_out=interp,
        py_out=py,
        js_out=js,
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_type_parameters_are_parsed_in_order() -> None:
    program = parse(
        "func swap[A, B](p: (A, B)) -> (B, A)\n"
        "    example (1, true) -> (true, 1)\n"
        "    let (a, b): (A, B) = p\n"
        "    return (b, a)\n"
        "end func\n"
    )
    assert program.definitions[0].type_params == ["A", "B"]


def test_function_without_brackets_has_no_type_parameters() -> None:
    program = parse(
        "func f(x: Int) -> Int\n    example 1 -> 1\n    return x\nend func\n"
    )
    assert program.definitions[0].type_params == []


def test_lowercase_type_parameter_is_a_parse_error_naming_the_fix() -> None:
    with pytest.raises((ParseError, ParseErrors)) as info:
        parse("func f[t](x: t) -> t\n    example 1 -> 1\n    return x\nend func\n")
    assert "must start with an uppercase letter, like 'T'" in str(info.value)


def test_empty_type_parameter_list_is_a_parse_error() -> None:
    with pytest.raises((ParseError, ParseErrors)):
        parse("func f[](x: Int) -> Int\n    example 1 -> 1\n    return x\nend func\n")


# ---------------------------------------------------------------------------
# Typing: what callers may do
# ---------------------------------------------------------------------------


def test_each_call_instantiates_type_parameters_independently() -> None:
    _check(
        SHOW + "func main() -> Unit\n"
        "    let a: String = show(1)\n"
        '    let b: String = show("x")\n'
        "    let c: String = show([1.5])\n"
        "    print(a + b + c)\n"
        "end func\n"
    )


def test_inconsistent_arguments_for_one_type_parameter_are_rejected() -> None:
    message = _messages(
        "func same[T](a: T, b: T) -> Bool\n"
        "    example 1, 1 -> true\n"
        "    return a == b\n"
        "end func\n"
        "func main() -> Unit\n"
        '    print(same(1, "x"))\n'
        "end func\n"
    )
    assert "Argument 2 type mismatch: expected Int, got String" in message


def test_result_type_follows_the_instantiation() -> None:
    message = _messages(
        "func first_or[T](xs: List[T], fallback: T) -> T\n"
        "    example [1], 0 -> 1\n"
        "    match xs with\n"
        "        | [] -> return fallback\n"
        "        | [x, ...] -> return x\n"
        "    end match\n"
        "end func\n"
        "func main() -> Unit\n"
        '    let n: Int = first_or(["a"], "b")\n'
        "end func\n"
    )
    assert "declared type Int, but the value has type String" in message


def test_each_example_is_its_own_instantiation() -> None:
    _check(SHOW)


def test_example_mismatch_names_the_instantiated_type() -> None:
    message = _messages(
        "func pair[T](a: T, b: T) -> List[T]\n"
        '    example 1, "x" -> [1]\n'
        "    return [a, b]\n"
        "end func\n"
    )
    assert "Example input 2 type mismatch: expected Int, got String" in message


def test_generic_function_can_call_generic_functions() -> None:
    _check(
        SHOW + "func show_all[T](xs: List[T]) -> List[String]\n"
        '    example [1, 2] -> ["1", "2"]\n'
        "    return map(xs, show)\n"
        "end func\n"
        "func twice[T](x: T) -> String\n"
        '    example 1 -> "1 1"\n'
        '    return show(x) + " " + show(x)\n'
        "end func\n"
    )


def test_return_only_type_parameter_is_settled_by_the_expected_type() -> None:
    _check(
        "func empty[T]() -> List[T]\n"
        "    example () -> []\n"
        "    return []\n"
        "end func\n"
        "func main() -> Unit\n"
        "    let xs: List[String] = empty()\n"
        "    print(length(xs))\n"
        "end func\n"
    )


# ---------------------------------------------------------------------------
# Typing: the body sees an opaque type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ("return x + x", "Cannot apply '+' to T and T"),
        ("if x > x then return x end if\n    return x", "Cannot apply '>' to T and T"),
        ("return 0 - x", "Cannot apply '-' to Int and T"),
    ],
)
def test_body_cannot_assume_operations_on_a_type_parameter(
    body: str, fragment: str
) -> None:
    message = _messages(
        f"func f[T](x: T) -> T\n    example 1 -> 1\n    {body}\nend func\n"
    )
    assert fragment in message


def test_body_cannot_return_a_type_parameter_as_a_concrete_type() -> None:
    message = _messages(
        "func f[T](x: T) -> Int\n    example 1 -> 1\n    return x\nend func\n"
    )
    assert "Return type mismatch: expected Int, got T" in message


def test_body_cannot_mix_two_type_parameters() -> None:
    message = _messages(
        "func f[A, B](a: A, b: B) -> A\n"
        '    example 1, "x" -> 1\n'
        "    return b\n"
        "end func\n"
    )
    assert "Return type mismatch: expected A, got B" in message


def test_body_cannot_pass_a_type_parameter_to_a_concrete_parameter() -> None:
    message = _messages(
        "func inc(x: Int) -> Int\n"
        "    example 1 -> 2\n"
        "    return x + 1\n"
        "end func\n"
        "func f[T](x: T) -> Int\n"
        "    example 1 -> 2\n"
        "    return inc(x)\n"
        "end func\n"
    )
    assert "Argument 1 type mismatch: expected Int, got T" in message


def test_type_parameter_cannot_be_a_hash_key_without_a_bound() -> None:
    message = _messages(
        "func uniq[T](xs: List[T]) -> List[T]\n"
        "    example [1, 1] -> [1]\n"
        "    let s: Set[T] = set_from_list(xs)\n"
        "    return set_to_list(s)\n"
        "end func\n"
    )
    assert "Declare the bound: [T: Hash]" in message


def test_body_may_compare_print_and_store_type_parameter_values() -> None:
    _check(
        "func describe[T](x: T, y: T) -> String\n"
        '    example 1, 1 -> "1 equals 1"\n'
        "    if x == y then\n"
        '        return f"{x} equals {y}"\n'
        "    end if\n"
        "    return to_string([x, y])\n"
        "end func\n"
        "func boxed[T](x: T) -> Option[(T, List[T])]\n"
        "    example 1 -> Some((1, [1]))\n"
        "    let wrap = fn(v: T) -> [v]\n"
        "    let out: List[T] = wrap(x)\n"
        "    return Some((x, out))\n"
        "end func\n"
    )


# ---------------------------------------------------------------------------
# Typing: the type parameter list itself (E320)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "fragment"),
    [
        ("func f[T, T](x: T) -> T", "Duplicate type parameter 'T'"),
        ("func f[String](x: String) -> String", "same name as the type 'String'"),
        ("func f[T](x: Int) -> Int", "is not used in any parameter type"),
    ],
)
def test_invalid_type_parameter_lists_report_e320(header: str, fragment: str) -> None:
    errors = _errors(f"{header}\n    example 1 -> 1\n    return x\nend func\n")
    matching = [error for error in errors if fragment in error.message]
    assert matching, [error.message for error in errors]
    assert matching[0].error_code == ErrorCode.TYPE_GENERIC_PARAMS


def test_type_parameter_may_not_shadow_a_user_type() -> None:
    message = _messages(
        "type Point = Point(x: Int)\n"
        "func f[Point](p: Point) -> Point\n"
        "    example Point(1) -> Point(1)\n"
        "    return p\n"
        "end func\n"
    )
    assert "same name as the type 'Point'" in message


def test_entrypoint_cannot_be_generic() -> None:
    errors = _errors("func main[T]() -> Unit\n    print(1)\nend func\n")
    assert any(
        error.error_code == ErrorCode.TYPE_GENERIC_PARAMS
        and "entrypoint" in error.message
        for error in errors
    )


def test_impl_method_cannot_declare_type_parameters() -> None:
    message = _messages(
        "trait Show\n"
        "    func show(self: Self) -> String\n"
        "end trait\n"
        "type P = P(x: Int)\n"
        "impl Show for P\n"
        "    func show[T](self: P) -> String\n"
        '        example P(1) -> "P"\n'
        '        return "P"\n'
        "    end func\n"
        "end impl\n"
    )
    assert "Impl method 'show' cannot declare type parameters" in message


# ---------------------------------------------------------------------------
# Typing: generic functions used as values (E321)
# ---------------------------------------------------------------------------


def test_generic_function_value_is_instantiated_by_its_context() -> None:
    _check(
        SHOW + "func apply[A, B](f: (A) -> B, x: A) -> B\n"
        "    example fn(v: Int) -> v + 1, 1 -> 2\n"
        "    return f(x)\n"
        "end func\n"
        "func main() -> Unit\n"
        "    let s: (Float) -> String = show\n"
        "    let fs: List[(Int) -> String] = [show]\n"
        "    print(map([1.5], show))\n"
        "    print(apply(show, 2.5))\n"
        "    print(s(1.0) + fs[0](1))\n"
        "end func\n"
    )


def test_uninstantiated_generic_function_value_reports_e321() -> None:
    errors = _errors(SHOW + "func main() -> Unit\n    print(map([], show))\nend func\n")
    assert [error.error_code for error in errors] == [ErrorCode.TYPE_GENERIC_INFERENCE]
    assert "Cannot infer the type argument T of generic function 'show'" in (
        errors[0].message
    )


def test_unannotated_binding_of_a_generic_function_reports_one_error() -> None:
    errors = _errors(SHOW + "func main() -> Unit\n    let f = show\nend func\n")
    assert len(errors) == 1
    assert "add an explicit type annotation" in errors[0].message


def test_local_binding_shadowing_a_generic_function_is_not_generic() -> None:
    _check(
        SHOW + "func main() -> Unit\n"
        "    let show = fn(x: Int) -> x\n"
        "    let f = show\n"
        "    print(f(1))\n"
        "end func\n"
    )


# ---------------------------------------------------------------------------
# Execution and backend parity
# ---------------------------------------------------------------------------


def test_generic_functions_run_identically_on_every_backend() -> None:
    _assert_parity(
        "func first_or[T](xs: List[T], fallback: T) -> T\n"
        "    example [1, 2], 0 -> 1\n"
        '    example [], "none" -> "none"\n'
        "    match xs with\n"
        "        | [] -> return fallback\n"
        "        | [x, ...] -> return x\n"
        "    end match\n"
        "end func\n"
        "func swap[A, B](p: (A, B)) -> (B, A)\n"
        '    example (1, "x") -> ("x", 1)\n'
        "    let (a, b): (A, B) = p\n"
        "    return (b, a)\n"
        "end func\n"
        "func count_if[T](xs: List[T], pred: (T) -> Bool) -> Int\n"
        "    example [1, 2, 3], fn(x: Int) -> x > 1 -> 2\n"
        "    var n = 0\n"
        "    for x: T in xs do\n"
        "        if pred(x) then\n"
        "            n = n + 1\n"
        "        end if\n"
        "    end for\n"
        "    return n\n"
        "end func\n"
        "func main() -> Unit\n"
        "    print(first_or([3, 4], 0))\n"
        '    print(first_or([], "empty"))\n'
        "    print(swap((1, true)))\n"
        '    print(count_if(["a", "bb", "ccc"], fn(s: String) -> length(s) > 1))\n'
        "end func\n",
        "3\nempty\n(true, 1)\n2\n",
    )


def test_float_passed_through_a_type_parameter_formats_as_float_everywhere() -> None:
    """JavaScript formats numbers by static type, so it specializes the body."""
    _assert_parity(
        "type Box[T] = Box(value: T)\n" + SHOW + "func boxed[T](x: T) -> Box[T]\n"
        "    example 1 -> Box(1)\n"
        "    return Box(x)\n"
        "end func\n"
        "func show_both[T](xs: List[T]) -> String\n"
        '    example [1] -> "[1] / 1"\n'
        '    return f"{xs} / {xs[0]}"\n'
        "end func\n"
        "func main() -> Unit\n"
        "    print(show(2.0))\n"
        "    print(show(2))\n"
        "    print(show(Some(6.0)))\n"
        "    print(show_both([1.0, 2.5]))\n"
        "    print(show(boxed(3.0)))\n"
        "    print(map([1.0, 2.0], show))\n"
        "    let s: (Float) -> String = show\n"
        "    print(s(5.0))\n"
        "end func\n",
        "2.0\n2\nSome(value: 6.0)\n[1.0, 2.5] / 1.0\nBox(value: 3.0)\n"
        '["1.0", "2.0"]\n5.0\n',
    )


def test_generic_calls_inside_generic_bodies_carry_the_instantiation() -> None:
    _assert_parity(
        SHOW + "func show_twice[T](x: T) -> String\n"
        '    example 1 -> "1,1"\n'
        '    return show(x) + "," + show(x)\n'
        "end func\n"
        "func apply[A, B](f: (A) -> B, x: A) -> B\n"
        "    example fn(v: Int) -> v + 1, 1 -> 2\n"
        "    return f(x)\n"
        "end func\n"
        "func last[T](xs: List[T]) -> String\n"
        '    example [1, 2] -> "2"\n'
        "    match xs with\n"
        '        | [] -> return "empty"\n'
        "        | [x] -> return show(x)\n"
        "        | [_, ...rest] -> return last(rest)\n"
        "    end match\n"
        "end func\n"
        "func main() -> Unit\n"
        "    print(show_twice(1.0))\n"
        "    print(show_twice(1))\n"
        "    print(apply(show, 2.0))\n"
        "    print(last([1.0, 2.0, 3.0]))\n"
        "end func\n",
        "1.0,1.0\n1,1\n2.0\n3.0\n",
    )


def test_polymorphic_recursion_keeps_float_formatting() -> None:
    _assert_parity(
        "func nest[T](x: T, depth: Int) -> String\n"
        '    example 1, 0 -> "1"\n'
        "    if depth == 0 then\n"
        "        return to_string(x)\n"
        "    end if\n"
        "    return nest([x], depth - 1)\n"
        "end func\n"
        "func main() -> Unit\n"
        "    print(nest(1.0, 3))\n"
        "end func\n",
        "[[[1.0]]]\n",
    )


def test_generic_functions_cross_module_boundaries() -> None:
    lib = (
        "export func show[T](x: T) -> String\n"
        '    example 1 -> "<1>"\n'
        "    return wrap(x)\n"
        "end func\n"
        "func wrap[T](x: T) -> String\n"
        '    example 1 -> "<1>"\n'
        '    return "<" + to_string(x) + ">"\n'
        "end func\n"
        "export func pair[A, B](a: A, b: B) -> (A, B)\n"
        '    example 1, "x" -> (1, "x")\n'
        "    return (a, b)\n"
        "end func\n"
    )
    main = (
        "import Lib\n"
        "func main() -> Unit\n"
        "    print(show(2.0))\n"
        "    print(Lib.show(3.0))\n"
        '    print(Lib.pair(1.0, "a"))\n'
        "    print(map([4.0], Lib.show))\n"
        "    print(show(5))\n"
        "end func\n"
    )
    expected = '<2.0>\n<3.0>\n(1.0, "a")\n["<4.0>"]\n<5>\n'
    modules = {"Lib": lib}
    interp = _interpreter_project_output(main, modules)
    py = _compiled_python_project_output(main, modules)
    js = _compiled_js_project_output(main, modules) if HAS_NODE else expected
    _assert_expected_backend_outputs(
        label="generic project",
        context=main,
        expected=expected,
        interp_out=interp,
        py_out=py,
        js_out=js,
    )


def _cli_outputs(tmp_path: Path, source: str) -> dict[str, str]:
    """Run *source* through `geno` the way a user does, on every backend."""
    program = tmp_path / "Main.geno"
    program.write_text(source, encoding="utf-8")
    outputs = {}
    interp = subprocess.run(
        [
            sys.executable,
            "-m",
            "geno",
            "run",
            "--unsafe",
            "--cap",
            "print",
            str(program),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO_ROOT,
    )
    assert interp.returncode == 0, interp.stderr
    outputs["interpreter"] = interp.stdout
    targets = [("python", "py", [sys.executable])]
    if HAS_NODE:
        targets.append(("js", "js", ["node"]))
    for target, suffix, runner in targets:
        artifact = tmp_path / f"out.{suffix}"
        compiled = subprocess.run(
            [
                sys.executable,
                "-m",
                "geno",
                "compile",
                str(program),
                "--target",
                "python" if target == "python" else "js",
                "-o",
                str(artifact),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=REPO_ROOT,
        )
        assert compiled.returncode == 0, compiled.stderr
        ran = subprocess.run(
            [*runner, str(artifact), "--cap", "print"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert ran.returncode == 0, ran.stderr
        outputs[target] = ran.stdout
    return outputs


def test_generic_standard_library_works_at_any_element_type(tmp_path: Path) -> None:
    outputs = _cli_outputs(
        tmp_path,
        "import List\n"
        "import Option as O\n"
        "import Result\n"
        "func main() -> Unit\n"
        '    print(List.zip(["a", "b"], [1.5, 2.0]))\n'
        "    print(List.map([1.0, 2.0], fn(x: Float) -> x * 2.0))\n"
        '    print(List.find(["x", "yy"], fn(s: String) -> s == "yy"))\n'
        "    print(O.map(Some(2.0), fn(x: Float) -> [x]))\n"
        '    print(Result.map_err(Err("bad"), fn(s: String) -> to_upper(s)))\n'
        "end func\n",
    )
    expected = (
        '[("a", 1.5), ("b", 2.0)]\n[2.0, 4.0]\nSome(value: "yy")\n'
        'Some(value: [2.0])\nErr(error: "BAD")\n'
    )
    assert outputs == dict.fromkeys(outputs, expected)


# ---------------------------------------------------------------------------
# JavaScript specialization and tooling surfaces
# ---------------------------------------------------------------------------


def test_js_emits_no_copies_when_no_float_passes_through() -> None:
    js = compile_to_js(
        SHOW + "func main() -> Unit\n"
        '    print(show(1) + show("a") + show([true]))\n'
        "end func\n"
    )
    assert "show$g" not in js


def test_js_emits_one_copy_per_float_shape() -> None:
    js = compile_to_js(
        SHOW + "func main() -> Unit\n"
        "    print(show(1.0) + show(2.0) + show([1.0]) + show(1))\n"
        "end func\n"
    )
    assert "function show$g1(" in js
    assert "function show$g2(" in js
    assert "function show$g3(" not in js


def test_typescript_declarations_declare_type_parameters() -> None:
    dts = generate_dts(
        parse(
            "func zip_with[A, B](a: List[A], b: B) -> (A, B)\n"
            "    example [1], 2 -> (1, 2)\n"
            "    return (a[0], b)\n"
            "end func\n"
        )
    )
    assert "export declare function zip_with<A, B>(a: Array<A>, b: B): [A, B];" in dts


def test_editor_signature_help_shows_type_parameters() -> None:
    label, params = _extract_user_func_sig("show", SHOW)
    assert label == "show[T](x: T) -> String"
    assert len(params) == 1


def test_generated_docs_show_type_parameters() -> None:
    module = parse_module(SHOW, "Show.geno")
    signature = _render_signature_html(module.functions[0])
    text = re.sub(r"<[^>]+>", "", signature)
    assert text == "func show[T](x: T) -> String"
