"""Tests for json_parse and json_stringify builtins."""

from typing import Callable, cast

import pytest

from geno.api import RunConfig, run
from geno.tests._script_runner import run_node_code


def _parse(source: str):
    """Helper to lex+parse source into a Program AST."""
    from geno.lexer import Lexer
    from geno.parser import Parser

    tokens = Lexer(source).tokenize()
    return Parser(tokens).parse_program()


class TestJsonParseInterpreter:
    """Test json_parse via the embedding API."""

    def test_parse_string(self):
        source = """
        func main() -> String
            let result: Result[JsonValue, String] = json_parse(text: "\\\"hello\\\"")
            match result with
                | Ok(val) ->
                    match val with
                        | JsonString(s) -> return s
                        | _ -> return "wrong type"
                    end match
                | Err(e) -> return e
            end match
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == "hello"

    def test_parse_int(self):
        source = """
        func main() -> Int
            let result: Result[JsonValue, String] = json_parse(text: "42")
            match result with
                | Ok(val) ->
                    match val with
                        | JsonInt(n) -> return n
                        | _ -> return 0
                    end match
                | Err(_) -> return 0
            end match
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == 42

    def test_parse_float(self):
        source = """
        func main() -> Float
            let result: Result[JsonValue, String] = json_parse(text: "3.14")
            match result with
                | Ok(val) ->
                    match val with
                        | JsonFloat(f) -> return f
                        | _ -> return 0.0
                    end match
                | Err(_) -> return 0.0
            end match
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert abs(result.value - 3.14) < 0.001

    def test_parse_bool(self):
        source = """
        func main() -> Bool
            let result: Result[JsonValue, String] = json_parse(text: "true")
            match result with
                | Ok(val) ->
                    match val with
                        | JsonBool(b) -> return b
                        | _ -> return false
                    end match
                | Err(_) -> return false
            end match
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value is True

    def test_parse_null(self):
        source = """
        func main() -> Bool
            let result: Result[JsonValue, String] = json_parse(text: "null")
            match result with
                | Ok(val) ->
                    match val with
                        | JsonNull -> return true
                        | _ -> return false
                    end match
                | Err(_) -> return false
            end match
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value is True

    def test_parse_array(self):
        source = """
        func main() -> Int
            let result: Result[JsonValue, String] = json_parse(text: "[1, 2, 3]")
            match result with
                | Ok(val) ->
                    match val with
                        | JsonArray(items) -> return length(items)
                        | _ -> return 0
                    end match
                | Err(_) -> return 0
            end match
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == 3

    def test_parse_object(self):
        source = """
        func main() -> Int
            let result: Result[JsonValue, String] = json_parse(text: "{\\\"a\\\": 1, \\\"b\\\": 2}")
            match result with
                | Ok(val) ->
                    match val with
                        | JsonObject(entries) -> return length(entries)
                        | _ -> return 0
                    end match
                | Err(_) -> return 0
            end match
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == 2

    def test_parse_invalid_json(self):
        source = """
        func main() -> Bool
            let result: Result[JsonValue, String] = json_parse(text: "{invalid}")
            match result with
                | Ok(_) -> return false
                | Err(_) -> return true
            end match
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value is True

    @pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
    def test_parse_rejects_non_standard_constants(self, literal):
        source = f"""
        func main() -> Bool
            let result: Result[JsonValue, String] = json_parse(text: "{literal}")
            match result with
                | Ok(_) -> return false
                | Err(_) -> return true
            end match
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value is True

    @pytest.mark.parametrize("literal", ["1e309", "-1e309"])
    def test_parse_rejects_non_finite_number_overflow(self, literal):
        source = f"""
        func main() -> Bool
            let result: Result[JsonValue, String] = json_parse(text: "{literal}")
            match result with
                | Ok(_) -> return false
                | Err(message) -> return string_contains(message, "non-finite")
            end match
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value is True


class TestJsonStringifyInterpreter:
    """Test json_stringify via the embedding API."""

    def test_stringify_string(self):
        source = """
        func main() -> String
            return json_stringify(value: JsonString("hello"))
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == '"hello"'

    def test_stringify_int(self):
        source = """
        func main() -> String
            return json_stringify(value: JsonInt(42))
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == "42"

    def test_stringify_null(self):
        source = """
        func main() -> String
            return json_stringify(value: JsonNull())
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == "null"

    def test_stringify_bool(self):
        source = """
        func main() -> String
            return json_stringify(value: JsonBool(true))
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == "true"

    def test_stringify_array(self):
        source = """
        func main() -> String
            let arr: JsonValue = JsonArray([JsonInt(1), JsonInt(2)])
            return json_stringify(value: arr)
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == "[1,2]"

    def test_stringify_object(self):
        source = """
        func main() -> String
            let obj: JsonValue = JsonObject([("key", JsonString("val"))])
            return json_stringify(value: obj)
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == '{"key":"val"}'


class TestJsonRoundTrip:
    """Test parse -> stringify round trip."""

    def test_round_trip_nested(self):
        source = """
        func main() -> String
            let input: String = "{\\\"name\\\":\\\"Alice\\\",\\\"age\\\":30,\\\"scores\\\":[95,87]}"
            let parsed: Result[JsonValue, String] = json_parse(text: input)
            match parsed with
                | Ok(val) -> return json_stringify(value: val)
                | Err(e) -> return e
            end match
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == '{"name":"Alice","age":30,"scores":[95,87]}'


class TestJsonCompiledPython:
    """Test JSON builtins in compiled Python output."""

    def test_json_parse_compiled(self):
        source = """
        func main() -> String
            let result: Result[JsonValue, String] = json_parse(text: "42")
            match result with
                | Ok(val) ->
                    match val with
                        | JsonInt(n) -> return to_string(n)
                        | _ -> return "wrong"
                    end match
                | Err(e) -> return e
            end match
        end func
        """
        from geno.compiler import Compiler

        compiler = Compiler()
        python_code = compiler.compile(_parse(source))
        python_code += "\n__result__ = main()\n"
        env: dict[str, object] = {}
        exec(python_code, env)
        assert env["__result__"] == "42"

    def test_json_stringify_compiled(self):
        source = """
        func main() -> String
            return json_stringify(value: JsonString("test"))
        end func
        """
        from geno.compiler import Compiler

        compiler = Compiler()
        python_code = compiler.compile(_parse(source))
        python_code += "\n__result__ = main()\n"
        env: dict[str, object] = {}
        exec(python_code, env)
        assert env["__result__"] == '"test"'

    def test_json_stringify_non_finite_json_float_fails_loudly_python(self):
        source = """
        func main() -> Unit
            return ()
        end func
        """
        from geno.compiler import Compiler

        compiler = Compiler()
        python_code = compiler.compile(_parse(source))
        env: dict[str, object] = {}
        exec(python_code, env)
        json_float = cast(Callable[[float], object], env["JsonFloat"])
        json_stringify = cast(Callable[[object], str], env["json_stringify"])
        with pytest.raises(RuntimeError, match="JsonFloat must be finite"):
            json_stringify(json_float(float("inf")))


class TestJsonCompiledJS:
    """Test JSON builtins in compiled JS output."""

    def test_json_parse_js(self):
        source = """
        func main() -> String
            let result: Result[JsonValue, String] = json_parse(text: "42")
            match result with
                | Ok(val) ->
                    match val with
                        | JsonInt(n) -> return to_string(n)
                        | _ -> return "wrong"
                    end match
                | Err(e) -> return e
            end match
        end func
        """
        from geno.js_compiler import compile_to_js

        js_code = compile_to_js(source)
        assert isinstance(js_code, str)
        result = run_node_code(js_code, timeout=10)
        assert result.returncode == 0
        assert result.stdout.strip() == "42"

    def test_json_parse_large_integer_fails_loudly_js(self):
        source = """
        func main() -> Bool
            let result: Result[JsonValue, String] = json_parse(text: "9007199254740993")
            match result with
                | Ok(_) -> return false
                | Err(message) -> return string_contains(message, "safe integer range")
            end match
        end func
        """
        from geno.js_compiler import compile_to_js

        js_code = compile_to_js(source)
        assert isinstance(js_code, str)
        result = run_node_code(js_code, timeout=10)
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_json_stringify_js(self):
        source = """
        func main() -> String
            return json_stringify(value: JsonInt(99))
        end func
        """
        from geno.js_compiler import compile_to_js

        js_code = compile_to_js(source)
        assert isinstance(js_code, str)
        result = run_node_code(js_code, timeout=10)
        assert result.returncode == 0
        assert result.stdout.strip() == "99"

    def test_json_stringify_large_json_int_fails_loudly_js(self):
        from geno.js_compiler import compile_to_js

        js_code = compile_to_js(
            """
            func main() -> Unit
                return ()
            end func
            """
        )
        assert isinstance(js_code, str)
        js_code += """
try {
    json_stringify({ _tag: "JsonInt", value: 9007199254740992 });
    console.log("no-error");
} catch (error) {
    console.log(String(error.message).includes("safe integer range"));
}
"""
        result = run_node_code(js_code, timeout=10)
        assert result.returncode == 0
        assert result.stdout.strip().splitlines()[-1] == "true"

    def test_json_stringify_non_finite_json_float_fails_loudly_js(self):
        from geno.js_compiler import compile_to_js

        js_code = compile_to_js(
            """
            func main() -> Unit
                return ()
            end func
            """
        )
        assert isinstance(js_code, str)
        js_code += """
for (const value of [Infinity, -Infinity, NaN]) {
    try {
        json_stringify({ _tag: "JsonFloat", value });
        console.log("no-error");
    } catch (error) {
        console.log(String(error.message).includes("JsonFloat must be finite"));
    }
}
"""
        result = run_node_code(js_code, timeout=10)
        assert result.returncode == 0
        assert result.stdout.strip().splitlines()[-3:] == ["true", "true", "true"]


class TestJsonToStringInterpreter:
    """Test json_to_string via the embedding API."""

    def test_primitive_int(self):
        source = """
        func main() -> String
            return json_to_string(value: 42)
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == "42"

    def test_primitive_string(self):
        source = """
        func main() -> String
            return json_to_string(value: "hello")
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == '"hello"'

    def test_primitive_bool(self):
        source = """
        func main() -> String
            return json_to_string(value: true)
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == "true"

    def test_list(self):
        source = """
        func main() -> String
            return json_to_string(value: [1, 2, 3])
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == "[1,2,3]"

    def test_nested_list(self):
        source = """
        func main() -> String
            let data: List[List[Int]] = [[1, 2], [3, 4]]
            return json_to_string(value: data)
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == "[[1,2],[3,4]]"

    def test_option_some(self):
        source = """
        func main() -> String
            let val: Option[Int] = Some(42)
            return json_to_string(value: val)
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == "42"

    def test_option_none(self):
        source = """
        func main() -> String
            let val: Option[Int] = None
            return json_to_string(value: val)
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == "null"

    def test_json_value_passthrough(self):
        source = """
        func main() -> String
            return json_to_string(value: JsonString("hi"))
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value == '"hi"'

    def test_round_trip(self):
        source = """
        func main() -> Bool
            let original: String = "[1,2,3]"
            let parsed: Result[JsonValue, String] = json_parse(text: original)
            match parsed with
                | Ok(val) ->
                    let back: String = json_to_string(value: val)
                    return back == original
                | Err(_) -> return false
            end match
        end func
        """
        result = run(source, config=RunConfig())
        assert result.ok is True
        assert result.value is True


class TestJsonToStringCompiled:
    """Test json_to_string in compiled Python and JS output."""

    def test_compiled_python(self):
        source = """
        func main() -> String
            return json_to_string(value: [1, 2, 3])
        end func
        """
        from geno.compiler import Compiler

        compiler = Compiler()
        python_code = compiler.compile(_parse(source))
        python_code += "\n__result__ = main()\n"
        env: dict[str, object] = {}
        exec(python_code, env)
        assert env["__result__"] == "[1,2,3]"

    def test_non_finite_float_fails_loudly_python(self):
        source = """
        func main() -> Unit
            return ()
        end func
        """
        from geno.compiler import Compiler

        compiler = Compiler()
        python_code = compiler.compile(_parse(source))
        env: dict[str, object] = {}
        exec(python_code, env)
        json_to_string = cast(Callable[[object], str], env["json_to_string"])
        with pytest.raises(RuntimeError, match="Float must be finite"):
            json_to_string(float("inf"))

    def test_compiled_js(self):
        source = """
        func main() -> String
            return json_to_string(value: [1, 2, 3])
        end func
        """
        from geno.js_compiler import compile_to_js

        js_code = compile_to_js(source)
        assert isinstance(js_code, str)
        result = run_node_code(js_code, timeout=10)
        assert result.returncode == 0
        assert result.stdout.strip() == "[1,2,3]"

    def test_non_finite_float_fails_loudly_js(self):
        from geno.js_compiler import compile_to_js

        js_code = compile_to_js(
            """
            func main() -> Unit
                return ()
            end func
            """
        )
        assert isinstance(js_code, str)
        js_code += """
for (const value of [Infinity, -Infinity, NaN]) {
    try {
        json_to_string(value);
        console.log("no-error");
    } catch (error) {
        console.log(String(error.message).includes("Float must be finite"));
    }
}
"""
        result = run_node_code(js_code, timeout=10)
        assert result.returncode == 0
        assert result.stdout.strip().splitlines()[-3:] == ["true", "true", "true"]


class TestJsonAlwaysAvailable:
    """Test that json builtins work without any capabilities."""

    def test_json_parse_no_caps(self):
        source = """
        func main() -> Bool
            let result: Result[JsonValue, String] = json_parse(text: "true")
            match result with
                | Ok(val) ->
                    match val with
                        | JsonBool(b) -> return b
                        | _ -> return false
                    end match
                | Err(_) -> return false
            end match
        end func
        """
        config = RunConfig(capabilities=set())
        result = run(source, config=config)
        assert result.ok is True
        assert result.value is True
