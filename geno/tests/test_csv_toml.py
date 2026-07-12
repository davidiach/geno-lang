"""
Tests for CSV and TOML parsing builtins
========================================
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import geno
from geno.compiler import compile_to_python
from geno.js_compiler import compile_to_js
from geno.tests._script_runner import run_node_code


def run(source: str):
    result = geno.run(source, config=geno.RunConfig(timeout=10.0))
    if not result.ok:
        msgs = "; ".join(d.message for d in result.diagnostics)
        raise AssertionError(f"Program failed: {msgs}")
    return result.value_raw


def check(source: str):
    return geno.check(source)


class TestCsvParse:
    def test_basic_rows(self):
        source = """
func main() -> List[List[String]]
  return csv_parse("a,b,c\\n1,2,3")
end func
"""
        result = run(source)
        assert result == [["a", "b", "c"], ["1", "2", "3"]]

    def test_empty_input(self):
        source = """
func main() -> List[List[String]]
  return csv_parse("")
end func
"""
        result = run(source)
        assert result == []

    def test_single_row(self):
        source = """
func main() -> List[List[String]]
  return csv_parse("hello,world")
end func
"""
        result = run(source)
        assert result == [["hello", "world"]]

    def test_quoted_fields(self):
        source = """
func main() -> List[List[String]]
  return csv_parse("name,desc\\n\\"Alice\\",\\"has, commas\\"")
end func
"""
        result = run(source)
        assert result == [["name", "desc"], ["Alice", "has, commas"]]

    def test_bare_carriage_return_rows(self):
        source = """
func main() -> List[List[String]]
  let cr: String = from_char_code(13)
  return csv_parse("a" + cr + "b" + cr + cr)
end func
"""
        result = run(source)
        assert result == [["a"], ["b"], []]

    def test_type_check(self):
        source = """
func main() -> List[List[String]]
  return csv_parse("a,b")
end func
"""
        result = check(source)
        assert result.ok


class TestCsvParseWithHeaders:
    def test_basic_headers(self):
        source = """
func main() -> List[Map[String, String]]
  return csv_parse_with_headers("name,age\\nAlice,30\\nBob,25")
end func
"""
        result = run(source)
        assert len(result) == 2
        # Maps are dicts in interpreter
        assert result[0]["name"] == "Alice"
        assert result[0]["age"] == "30"
        assert result[1]["name"] == "Bob"

    def test_empty_table(self):
        source = """
func main() -> List[Map[String, String]]
  return csv_parse_with_headers("name,age")
end func
"""
        result = run(source)
        assert result == []

    def test_ragged_rows_keep_string_map_contract(self):
        source = """
func main() -> List[Map[String, String]]
  return csv_parse_with_headers("name,age\\nAlice\\nBob,31,ignored")
end func
"""
        result = run(source)
        assert result == [
            {"name": "Alice", "age": ""},
            {"name": "Bob", "age": "31"},
        ]

    def test_bare_carriage_return_header_rows(self):
        source = """
func main() -> List[Map[String, String]]
  let cr: String = from_char_code(13)
  return csv_parse_with_headers("name" + cr + "Alice" + cr)
end func
"""
        result = run(source)
        assert result == [{"name": "Alice"}]


class TestTomlParse:
    def test_basic_key_value(self):
        source = """
func main() -> String
  let result: Result[JsonValue, String] = toml_parse("name = \\"Alice\\"\\nage = 30")
  match result with
  | Ok(val) ->
    match val with
    | JsonObject(entries) -> return "ok"
    | _ -> return "wrong type"
    end match
  | Err(msg) -> return msg
  end match
end func
"""
        assert run(source) == "ok"

    def test_invalid_toml(self):
        source = """
func main() -> String
  let result: Result[JsonValue, String] = toml_parse("= invalid")
  match result with
  | Ok(_) -> return "unexpected ok"
  | Err(_) -> return "error"
  end match
end func
"""
        assert run(source) == "error"

    def test_type_check(self):
        source = """
func main() -> Result[JsonValue, String]
  return toml_parse("x = 1")
end func
"""
        result = check(source)
        assert result.ok


class TestCsvCompiledPython:
    def test_compiled_output_contains_csv_parse(self):
        source = """
func main() -> List[List[String]]
  return csv_parse("a,b")
end func
"""
        py_code = compile_to_python(source)
        assert "csv_parse" in py_code


class TestCsvCompiledJS:
    def test_compiled_output_contains_csv_parse(self):
        source = """
func main() -> List[List[String]]
  return csv_parse("a,b")
end func
"""
        js_code = compile_to_js(source)
        assert "csv_parse" in js_code

    def test_js_csv_runs(self):
        source = """
        func main() -> List[List[String]]
  return csv_parse("x,y\\n1,2")
end func
"""
        js_code = compile_to_js(source)
        result = run_node_code(js_code, timeout=10)
        assert result.returncode == 0
        assert "1" in result.stdout
