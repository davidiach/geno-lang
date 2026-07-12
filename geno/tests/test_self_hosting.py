"""
Tests for self-hosting compiler features: string indexing, char codes,
MutableMap, Vec, and file I/O.
"""

import shutil
from pathlib import Path

import pytest

from geno.api import RunConfig, run
from geno.js_compiler import compile_to_js
from geno.lexer import Lexer
from geno.parser import Parser
from geno.typechecker import TypeChecker, TypeError

# =============================================================================
# Helper
# =============================================================================


def typecheck(source: str) -> TypeChecker:
    lexer = Lexer(source, "<test>")
    tokens = lexer.tokenize()
    parser = Parser(tokens)
    program = parser.parse_program()
    checker = TypeChecker()
    checker.check_program(program)
    return checker


def run_geno(source: str):
    result = run(source, config=RunConfig(timeout=5.0))
    assert result.ok, f"Run failed: {result.diagnostics}"
    return result.value


def copy_selfhost(tmp_path: Path) -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    src = repo_root / "selfhost"
    dst = tmp_path / "selfhost"
    shutil.copytree(src, dst)
    return dst


def load_selfhost_modules(tmp_path: Path) -> dict[str, str]:
    selfhost_dir = copy_selfhost(tmp_path)
    return {path.stem: path.read_text() for path in selfhost_dir.glob("*.geno")}


# =============================================================================
# String Indexing
# =============================================================================


class TestStringIndexing:
    def test_index_first_char(self):
        source = """
        func main() -> String
            return "hello"[0]
        end func main
        """
        assert run_geno(source) == "h"

    def test_index_last_char(self):
        source = """
        func main() -> String
            let s: String = "world"
            return s[length(s) - 1]
        end func main
        """
        assert run_geno(source) == "d"

    def test_index_middle(self):
        source = """
        func main() -> String
            return "abcde"[2]
        end func main
        """
        assert run_geno(source) == "c"

    def test_index_out_of_bounds(self):
        source = """
        func main() -> String
            return "hi"[5]
        end func main
        """
        result = run(source, config=RunConfig(timeout=5.0))
        assert not result.ok

    def test_string_index_type_error(self):
        source = """
        func main() -> String
            return "hi"["bad"]
        end func main
        """
        with pytest.raises(TypeError):
            typecheck(source)

    def test_iterate_string(self):
        """Use string indexing to count vowels."""
        source = """
        func count_vowels(s: String) -> Int
            example "hello" -> 2
            var count: Int = 0
            var i: Int = 0
            while i < length(s) do
                let c: String = s[i]
                if c == "a" or c == "e" or c == "i" or c == "o" or c == "u" then
                    count = count + 1
                end if
                i = i + 1
            end while
            return count
        end func count_vowels

        func main() -> Int
            return count_vowels("hello world")
        end func main
        """
        assert run_geno(source) == 3


# =============================================================================
# Char Codes
# =============================================================================


class TestCharCodes:
    def test_char_code_ascii(self):
        source = """
        func main() -> Int
            return char_code("A")
        end func main
        """
        assert run_geno(source) == 65

    def test_from_char_code(self):
        source = """
        func main() -> String
            return from_char_code(65)
        end func main
        """
        assert run_geno(source) == "A"

    def test_round_trip(self):
        source = """
        func main() -> Bool
            let c: Int = char_code("Z")
            return from_char_code(c) == "Z"
        end func main
        """
        assert run_geno(source) is True

    def test_is_digit(self):
        """Use char codes to classify characters."""
        source = """
        func is_digit(c: String) -> Bool
            example "5" -> true
            example "a" -> false
            let code: Int = char_code(c)
            return code >= 48 and code <= 57
        end func is_digit

        func main() -> Bool
            return is_digit("7") and not is_digit("x")
        end func main
        """
        assert run_geno(source) is True

    def test_char_code_empty_string_fails(self):
        source = """
        func main() -> Int
            return char_code("")
        end func main
        """
        result = run(source, config=RunConfig(timeout=5.0))
        assert not result.ok


# =============================================================================
# MutableMap
# =============================================================================


class TestMutableMap:
    def test_create_and_set(self):
        source = """
        func main() -> Int
            let m: MutableMap[String, Int] = mutable_map_new()
            mutable_map_set(map: m, key: "x", value: 42)
            return mutable_map_size(m)
        end func main
        """
        assert run_geno(source) == 1

    def test_get_existing_key(self):
        source = """
        func main() -> Int
            let m: MutableMap[String, Int] = mutable_map_new()
            mutable_map_set(map: m, key: "x", value: 42)
            return unwrap(mutable_map_get(m, "x"))
        end func main
        """
        assert run_geno(source) == 42

    def test_get_missing_key(self):
        source = """
        func main() -> Bool
            let m: MutableMap[String, Int] = mutable_map_new()
            return is_none(mutable_map_get(m, "x"))
        end func main
        """
        assert run_geno(source) is True

    def test_contains(self):
        source = """
        func main() -> Bool
            let m: MutableMap[String, Int] = mutable_map_new()
            mutable_map_set(map: m, key: "x", value: 1)
            return mutable_map_contains(m, "x") and not mutable_map_contains(m, "y")
        end func main
        """
        assert run_geno(source) is True

    def test_delete(self):
        source = """
        func main() -> Int
            let m: MutableMap[String, Int] = mutable_map_new()
            mutable_map_set(map: m, key: "a", value: 1)
            mutable_map_set(map: m, key: "b", value: 2)
            mutable_map_delete(m, "a")
            return mutable_map_size(m)
        end func main
        """
        assert run_geno(source) == 1

    def test_overwrite(self):
        source = """
        func main() -> Int
            let m: MutableMap[String, Int] = mutable_map_new()
            mutable_map_set(map: m, key: "x", value: 1)
            mutable_map_set(map: m, key: "x", value: 99)
            return unwrap(mutable_map_get(m, "x"))
        end func main
        """
        assert run_geno(source) == 99

    def test_keys(self):
        source = """
        func main() -> Int
            let m: MutableMap[String, Int] = mutable_map_new()
            mutable_map_set(map: m, key: "a", value: 1)
            mutable_map_set(map: m, key: "b", value: 2)
            return length(mutable_map_keys(m))
        end func main
        """
        assert run_geno(source) == 2

    def test_reference_semantics(self):
        """Mutations through one binding are visible through another."""
        source = """
        func add_entry(m: MutableMap[String, Int]) -> Unit
            example mutable_map_new() -> ()
            mutable_map_set(map: m, key: "added", value: 1)
            return ()
        end func add_entry

        func main() -> Bool
            let m: MutableMap[String, Int] = mutable_map_new()
            add_entry(m)
            return mutable_map_contains(m, "added")
        end func main
        """
        assert run_geno(source) is True


# =============================================================================
# Vec
# =============================================================================


class TestVec:
    def test_push_and_length(self):
        source = """
        func main() -> Int
            let v: Vec[Int] = vec_new()
            vec_push(v, 10)
            vec_push(v, 20)
            vec_push(v, 30)
            return vec_length(v)
        end func main
        """
        assert run_geno(source) == 3

    def test_get(self):
        source = """
        func main() -> Int
            let v: Vec[Int] = vec_new()
            vec_push(v, 10)
            vec_push(v, 20)
            return vec_get(v, 1)
        end func main
        """
        assert run_geno(source) == 20

    def test_set(self):
        source = """
        func main() -> Int
            let v: Vec[Int] = vec_new()
            vec_push(v, 10)
            vec_push(v, 20)
            vec_set(vec: v, index: 0, value: 99)
            return vec_get(v, 0)
        end func main
        """
        assert run_geno(source) == 99

    def test_pop(self):
        source = """
        func main() -> Int
            let v: Vec[Int] = vec_new()
            vec_push(v, 10)
            vec_push(v, 20)
            return unwrap(vec_pop(v))
        end func main
        """
        assert run_geno(source) == 20

    def test_pop_empty(self):
        source = """
        func main() -> Bool
            let v: Vec[Int] = vec_new()
            return is_none(vec_pop(v))
        end func main
        """
        assert run_geno(source) is True

    def test_to_list(self):
        source = """
        func main() -> List[Int]
            let v: Vec[Int] = vec_new()
            vec_push(v, 1)
            vec_push(v, 2)
            vec_push(v, 3)
            return vec_to_list(v)
        end func main
        """
        assert run_geno(source) == [1, 2, 3]

    def test_from_list(self):
        source = """
        func main() -> Int
            let v: Vec[String] = vec_from_list(["a", "b", "c"])
            return vec_length(v)
        end func main
        """
        assert run_geno(source) == 3

    def test_collect_tokens(self):
        """Simulate building a token list with Vec."""
        source = """
        type Token = Number(val: Int) | Plus | Eof

        func main() -> Int
            let tokens: Vec[Token] = vec_new()
            vec_push(tokens, Number(3))
            vec_push(tokens, Plus)
            vec_push(tokens, Number(4))
            vec_push(tokens, Eof)
            return vec_length(tokens)
        end func main
        """
        assert run_geno(source) == 4


# =============================================================================
# Recursive Types (already working — regression test)
# =============================================================================


class TestRecursiveTypes:
    def test_recursive_adt(self):
        source = """
        type Expr = BinOp(left: Expr, right: Expr, op: String) | Lit(value: Int)

        func eval_expr(e: Expr) -> Int
            example Lit(42) -> 42
            match e with
                | Lit(v) -> return v
                | BinOp(l, r, o) ->
                    let lv: Int = eval_expr(l)
                    let rv: Int = eval_expr(r)
                    if o == "+" then
                        return lv + rv
                    else
                        return lv - rv
                    end if
            end match
        end func eval_expr

        func main() -> Int
            let expr: Expr = BinOp(BinOp(Lit(1), Lit(2), "+"), Lit(3), "+")
            return eval_expr(expr)
        end func main
        """
        assert run_geno(source) == 6


# =============================================================================
# Integration: Mini Lexer
# =============================================================================


class TestMiniLexer:
    def test_tokenize_simple_expression(self):
        """A mini lexer that tokenizes '12 + 34' using string indexing and char codes."""
        source = """
        type Token = TNum(val: Int) | TPlus | TEof

        func is_digit(c: String) -> Bool
            example "5" -> true
            example "a" -> false
            let code: Int = char_code(c)
            return code >= 48 and code <= 57
        end func is_digit

        func tokenize(input: String) -> List[Token]
            example "1" -> [TNum(1), TEof]
            let tokens: Vec[Token] = vec_new()
            var i: Int = 0
            while i < length(input) do
                let c: String = input[i]
                if c == " " then
                    i = i + 1
                else
                    if c == "+" then
                        vec_push(tokens, TPlus)
                        i = i + 1
                    else
                        if is_digit(c) then
                            var num: Int = 0
                            while i < length(input) and is_digit(input[i]) do
                                num = num * 10 + (char_code(input[i]) - 48)
                                i = i + 1
                            end while
                            vec_push(tokens, TNum(num))
                        else
                            i = i + 1
                        end if
                    end if
                end if
            end while
            vec_push(tokens, TEof)
            return vec_to_list(tokens)
        end func tokenize

        func main() -> Int
            let tokens: List[Token] = tokenize("12 + 34")
            return length(tokens)
        end func main
        """
        # Should produce [TNum(12), TPlus, TNum(34), TEof] = 4 tokens
        assert run_geno(source) == 4


class TestSelfhostDiagnostics:
    def test_parser_diagnostic_is_structured_without_print_side_effects(
        self, tmp_path: Path
    ):
        result = run(
            """
            import Ast
            import Lexer
            import Parser
            import Tokens
            import Types

            func main() -> String
                let tokens: List[Token] = tokenize("end")
                let ps: ParserState = make_parser(tokens, "Broken.geno")
                let program: Program = parse_program(ps)
                let diags: List[Diagnostic] = vec_to_list(ps.diagnostics)
                return format_diagnostic(head(diags))
            end func main
            """,
            filename="DiagParser.geno",
            config=RunConfig(
                timeout=10.0,
                check_examples=False,
                modules=load_selfhost_modules(tmp_path),
            ),
        )

        assert result.ok, f"Run failed: {result.diagnostics}"
        assert result.output == ""
        assert result.value.startswith("Broken.geno:1:1-3: error:")

    def test_type_and_runtime_diagnostics_keep_filename_without_core_prints(
        self, tmp_path: Path
    ):
        modules = load_selfhost_modules(tmp_path)
        type_result = run(
            """
            import Ast
            import Parser
            import TypeChecker
            import Types

            func main() -> String
                let source: String = "func main() -> Int\\n    return true\\nend func main"
                let program: Program = parse_with_filename(source, "BrokenType.geno")
                let cs: CheckerState = make_checker_with_filename("BrokenType.geno")
                let errors: Int = check_program(cs: cs, program: program, modules: mutable_map_new())
                let diags: List[Diagnostic] = vec_to_list(cs.diagnostics)
                return format_diagnostic(head(diags))
            end func main
            """,
            filename="DiagType.geno",
            config=RunConfig(
                timeout=10.0,
                check_examples=False,
                modules=modules,
            ),
        )

        assert type_result.ok, f"Run failed: {type_result.diagnostics}"
        assert type_result.output == ""
        assert type_result.value.startswith("BrokenType.geno:")
        assert ":0:0" not in type_result.value

        runtime_result = run(
            """
            import Ast
            import Interpreter
            import Parser
            import TypeChecker
            import Types

            func main() -> String
                let source: String = "func main() -> Int\\n    let xs: List[Int] = []\\n    return head(xs)\\nend func main"
                let program: Program = parse_with_filename(source, "BrokenRun.geno")
                let cs: CheckerState = make_checker_with_filename("BrokenRun.geno")
                let errors: Int = check_program(cs: cs, program: program, modules: mutable_map_new())
                let st: InterpState = make_interp_state_with_filename("BrokenRun.geno")
                let value: Value = run_program_with_state(st: st, program: program, modules: mutable_map_new(), check_ex: false)
                let diags: List[Diagnostic] = vec_to_list(st.diagnostics)
                return format_diagnostic(head(diags))
            end func main
            """,
            filename="DiagRuntime.geno",
            config=RunConfig(
                timeout=10.0,
                check_examples=False,
                modules=modules,
            ),
        )

        assert runtime_result.ok, f"Run failed: {runtime_result.diagnostics}"
        assert runtime_result.output == ""
        assert runtime_result.value.startswith("BrokenRun.geno:")
        assert ":0:0" not in runtime_result.value


class TestSelfhostBuiltinAliases:
    def test_selfhost_supports_alias_builtin_surface(self, tmp_path: Path) -> None:
        result = run(
            """
            import Interpreter
            import TypeChecker
            import Types

            func has_builtin(cs: CheckerState, name: String, params: List[String]) -> Bool
                example (make_checker_with_filename("x.geno"), "to_upper", ["text"]) -> true
                let type_opt: Option[GType] = type_env_lookup(cs.global_env, name)
                let params_opt: Option[List[String]] = mutable_map_get(map: cs.func_params, key: name)
                match type_opt with
                    | Some(_) ->
                    match params_opt with
                        | Some(actual) -> return actual == params
                        | None -> return false
                    end match
                    | None -> return false
                end match
            end func has_builtin

            func as_string(v: Value) -> String
                example StringVal("x") -> "x"
                match v with
                    | StringVal(s) -> return s
                    | _ -> return ""
                end match
            end func as_string

            func as_int_value(v: Value) -> Int
                example IntVal(1) -> 1
                match v with
                    | IntVal(n) -> return n
                    | FloatVal(_) -> return 0
                    | _ -> return -1
                end match
            end func as_int_value

            func as_bool_value(v: Value) -> Bool
                example BoolVal(true) -> true
                match v with
                    | BoolVal(ok) -> return ok
                    | _ -> return false
                end match
            end func as_bool_value

            func main() -> Bool
                let cs: CheckerState = make_checker_with_filename("AliasBuiltins.geno")
                if not has_builtin(cs: cs, name: "to_upper", params: ["text"]) then
                    return false
                end if
                if not has_builtin(cs: cs, name: "replace", params: ["text", "old", "new"]) then
                    return false
                end if
                if not has_builtin(cs: cs, name: "string_substring", params: ["text", "start", "stop"]) then
                    return false
                end if
                if not has_builtin(cs: cs, name: "math_random_int", params: ["lo", "hi"]) then
                    return false
                end if
                if not has_builtin(cs: cs, name: "option_unwrap_or", params: ["option", "default"]) then
                    return false
                end if

                let st: InterpState = make_interp_state_with_filename("AliasBuiltins.geno")
                let upper: Value = call_function(st: st, func_val: BuiltinVal("to_upper"), call_args: [StringVal("geno")], env: st.global_env)
                let replaced: Value = call_function(st: st, func_val: BuiltinVal("replace"), call_args: [upper, StringVal("EN"), StringVal("XX")], env: st.global_env)
                let clipped: Value = call_function(st: st, func_val: BuiltinVal("string_substring"), call_args: [replaced, IntVal(1), IntVal(3)], env: st.global_env)
                let trimmed: Value = call_function(st: st, func_val: BuiltinVal("string_trim"), call_args: [StringVal("  hi  ")], env: st.global_env)
                let trimmed_start: Value = call_function(st: st, func_val: BuiltinVal("string_trim_start"), call_args: [StringVal("  hi")], env: st.global_env)
                let trimmed_end: Value = call_function(st: st, func_val: BuiltinVal("string_trim_end"), call_args: [StringVal("hi  ")], env: st.global_env)
                let maxed: Value = call_function(st: st, func_val: BuiltinVal("math_max"), call_args: [call_function(st: st, func_val: BuiltinVal("math_abs"), call_args: [IntVal(-5)], env: st.global_env), IntVal(7)], env: st.global_env)
                let randomish: Value = call_function(st: st, func_val: BuiltinVal("math_random_int"), call_args: [IntVal(3), IntVal(10)], env: st.global_env)
                let random_floatish: Value = call_function(st: st, func_val: BuiltinVal("math_random_float"), call_args: [], env: st.global_env)
                let present: Value = CtorVal("Some", [ValueField("value", IntVal(1))])
                let absent: Value = CtorVal("None", [])
                let unwrapped: Value = call_function(st: st, func_val: BuiltinVal("option_unwrap_or"), call_args: [absent, IntVal(9)], env: st.global_env)

                return as_string(upper) == "GENO"
                    and as_string(clipped) == "XX"
                    and as_string(trimmed) == "hi"
                    and as_string(trimmed_start) == "hi"
                    and as_string(trimmed_end) == "hi"
                    and as_int_value(maxed) == 7
                    and as_int_value(randomish) == 3
                    and as_int_value(random_floatish) == 0
                    and as_bool_value(call_function(st: st, func_val: BuiltinVal("option_is_some"), call_args: [present], env: st.global_env))
                    and not as_bool_value(call_function(st: st, func_val: BuiltinVal("option_is_none"), call_args: [present], env: st.global_env))
                    and as_int_value(unwrapped) == 9
            end func main
            """,
            filename="AliasBuiltinDriver.geno",
            config=RunConfig(
                timeout=30.0,
                check_examples=False,
                modules=load_selfhost_modules(tmp_path),
            ),
        )

        assert result.ok, f"Run failed: {result.diagnostics}"
        assert result.value is True

    def test_selfhost_supports_pure_string_gap_builtins(self, tmp_path: Path) -> None:
        result = run(
            """
            import Interpreter
            import TypeChecker
            import Types

            func has_builtin(cs: CheckerState, name: String, params: List[String]) -> Bool
                example (make_checker_with_filename("x.geno"), "repeat_string", ["text", "count"]) -> true
                let type_opt: Option[GType] = type_env_lookup(cs.global_env, name)
                let params_opt: Option[List[String]] = mutable_map_get(map: cs.func_params, key: name)
                match type_opt with
                    | Some(_) ->
                    match params_opt with
                        | Some(actual) -> return actual == params
                        | None -> return false
                    end match
                    | None -> return false
                end match
            end func has_builtin

            func as_string(v: Value) -> String
                example StringVal("x") -> "x"
                match v with
                    | StringVal(s) -> return s
                    | _ -> return ""
                end match
            end func as_string

            func as_int_value(v: Value) -> Int
                example IntVal(1) -> 1
                match v with
                    | IntVal(n) -> return n
                    | _ -> return -1
                end match
            end func as_int_value

            func main() -> Bool
                let cs: CheckerState = make_checker_with_filename("PureStringBuiltins.geno")
                if not has_builtin(cs: cs, name: "repeat_string", params: ["text", "count"]) then
                    return false
                end if
                if not has_builtin(cs: cs, name: "string_repeat", params: ["text", "count"]) then
                    return false
                end if
                if not has_builtin(cs: cs, name: "string_char_at", params: ["text", "index"]) then
                    return false
                end if
                if not has_builtin(cs: cs, name: "string_pad_left", params: ["text", "width", "fill_char"]) then
                    return false
                end if

                let st: InterpState = make_interp_state_with_filename("PureStringBuiltins.geno")
                let repeated: Value = call_function(st: st, func_val: BuiltinVal("repeat_string"), call_args: [StringVal("ab"), IntVal(3)], env: st.global_env)
                let namespaced_repeated: Value = call_function(st: st, func_val: BuiltinVal("string_repeat"), call_args: [StringVal("x"), IntVal(3)], env: st.global_env)
                let empty_repeated: Value = call_function(st: st, func_val: BuiltinVal("string_repeat"), call_args: [StringVal(""), IntVal(1000000000)], env: st.global_env)
                let char_at: Value = call_function(st: st, func_val: BuiltinVal("string_char_at"), call_args: [StringVal("hello"), IntVal(1)], env: st.global_env)
                let missing_char: Value = call_function(st: st, func_val: BuiltinVal("string_char_at"), call_args: [StringVal("hello"), IntVal(99)], env: st.global_env)
                let negative_char: Value = call_function(st: st, func_val: BuiltinVal("string_char_at"), call_args: [StringVal("hello"), IntVal(-1)], env: st.global_env)
                let first_index: Value = call_function(st: st, func_val: BuiltinVal("string_index_of"), call_args: [StringVal("hello world"), StringVal("world")], env: st.global_env)
                let missing_index: Value = call_function(st: st, func_val: BuiltinVal("string_index_of"), call_args: [StringVal("hello"), StringVal("z")], env: st.global_env)
                let last_index: Value = call_function(st: st, func_val: BuiltinVal("string_last_index_of"), call_args: [StringVal("banana"), StringVal("na")], env: st.global_env)
                let empty_last_index: Value = call_function(st: st, func_val: BuiltinVal("string_last_index_of"), call_args: [StringVal("abc"), StringVal("")], env: st.global_env)
                let padded_left: Value = call_function(st: st, func_val: BuiltinVal("string_pad_left"), call_args: [StringVal("hi"), IntVal(4), StringVal("0")], env: st.global_env)
                let padded_right: Value = call_function(st: st, func_val: BuiltinVal("string_pad_right"), call_args: [StringVal("hi"), IntVal(4), StringVal(".")], env: st.global_env)

                return as_string(repeated) == "ababab"
                    and as_string(namespaced_repeated) == "xxx"
                    and as_string(empty_repeated) == ""
                    and as_string(char_at) == "e"
                    and as_string(missing_char) == ""
                    and as_string(negative_char) == ""
                    and as_int_value(first_index) == 6
                    and as_int_value(missing_index) == -1
                    and as_int_value(last_index) == 4
                    and as_int_value(empty_last_index) == 3
                    and as_string(padded_left) == "00hi"
                    and as_string(padded_right) == "hi.."
            end func main
            """,
            filename="PureStringBuiltinDriver.geno",
            config=RunConfig(
                timeout=30.0,
                check_examples=False,
                modules=load_selfhost_modules(tmp_path),
            ),
        )

        assert result.ok, f"Run failed: {result.diagnostics}"
        assert result.value is True

    def test_selfhost_math_random_int_dispatch_requires_hi_argument(
        self, tmp_path: Path
    ) -> None:
        result = run(
            """
            import Interpreter
            import Types

            func main() -> Int
                let st: InterpState = make_interp_state_with_filename("RandomProbe.geno")
                let value: Value = call_function(st: st, func_val: BuiltinVal("math_random_int"), call_args: [IntVal(5)], env: st.global_env)
                match value with
                    | IntVal(n) -> return n
                    | _ -> return -1
                end match
            end func main
            """,
            filename="RandomProbeDriver.geno",
            config=RunConfig(
                timeout=10.0,
                check_examples=False,
                modules=load_selfhost_modules(tmp_path),
            ),
        )

        assert not result.ok


class TestSelfhostFrontendParity:
    """Regression tests for #664 — selfhost parser/lexer/CLI parity
    with the Python reference frontend."""

    def test_labeled_end_while_parses_clean(self, tmp_path: Path) -> None:
        """F-0029 — the selfhost parser must accept ``end while "label"``
        the same way the Python frontend does.  ``examples/fibonacci.geno``
        uses this form and previously failed under selfhost."""
        result = run(
            """
            import Ast
            import Lexer
            import Parser
            import Tokens
            import Types

            func main() -> Int
                let source: String = "func main() -> Int\\n    var i: Int = 0\\n    while i < 3 do\\n        i = i + 1\\n    end while \\"count up\\"\\n    return i\\nend func main"
                let tokens: List[Token] = tokenize(source)
                let ps: ParserState = make_parser(tokens, "LabeledWhile.geno")
                let program: Program = parse_program(ps)
                return length(vec_to_list(ps.diagnostics))
            end func main
            """,
            filename="LabeledWhileDriver.geno",
            config=RunConfig(
                timeout=10.0,
                check_examples=False,
                modules=load_selfhost_modules(tmp_path),
            ),
        )

        assert result.ok, f"Run failed: {result.diagnostics}"
        # Zero parser diagnostics — the labeled end-while is accepted.
        assert result.value == 0

    def test_labeled_end_if_parses_clean(self, tmp_path: Path) -> None:
        """F-0029 — labeled ``end if`` should parse without diagnostics."""
        result = run(
            """
            import Ast
            import Lexer
            import Parser
            import Tokens
            import Types

            func main() -> Int
                let source: String = "func main() -> Int\\n    if 1 > 0 then\\n        return 1\\n    else\\n        return 0\\n    end if \\"signum\\"\\nend func main"
                let tokens: List[Token] = tokenize(source)
                let ps: ParserState = make_parser(tokens, "LabeledIf.geno")
                let program: Program = parse_program(ps)
                return length(vec_to_list(ps.diagnostics))
            end func main
            """,
            filename="LabeledIfDriver.geno",
            config=RunConfig(
                timeout=10.0,
                check_examples=False,
                modules=load_selfhost_modules(tmp_path),
            ),
        )

        assert result.ok, f"Run failed: {result.diagnostics}"
        assert result.value == 0

    def test_unterminated_string_emits_error_token(self, tmp_path: Path) -> None:
        """F-0030 — a source that ends without closing a string literal
        must not silently produce a ``TkString`` token.  The lexer now
        emits a ``TkError`` whose ``value`` carries the diagnostic
        message.  We walk the token list looking for the error token
        rather than hard-coding its index, so tweaks to the lexer's
        EOF-emission don't silently shift the test off its target."""
        result = run(
            """
            import Lexer
            import Tokens

            func first_error_value(tokens: List[Token]) -> String
                example [] -> ""
                if length(tokens) == 0 then
                    return ""
                end if
                let h: Token = head(tokens)
                match h.tt with
                    | TkError -> return h.value
                    | _ -> return first_error_value(tail(tokens))
                end match
            end func first_error_value

            func main() -> String
                let source: String = "let s: String = \\"oops"
                let tokens: List[Token] = tokenize(source)
                return first_error_value(tokens)
            end func main
            """,
            filename="UnterminatedString.geno",
            config=RunConfig(
                timeout=10.0,
                check_examples=False,
                modules=load_selfhost_modules(tmp_path),
            ),
        )

        assert result.ok, f"Run failed: {result.diagnostics}"
        assert "Unterminated string literal" in result.value

    def test_unterminated_triple_string_emits_error_token(self, tmp_path: Path) -> None:
        """F-0030 — triple-quoted strings that reach EOF without the
        closing ``\"\"\"`` must also surface as ``TkError``."""
        result = run(
            """
            import Lexer
            import Tokens

            func first_error_value(tokens: List[Token]) -> String
                example [] -> ""
                if length(tokens) == 0 then
                    return ""
                end if
                let h: Token = head(tokens)
                match h.tt with
                    | TkError -> return h.value
                    | _ -> return first_error_value(tail(tokens))
                end match
            end func first_error_value

            func main() -> String
                let source: String = "let s: String = \\"\\"\\"hello"
                let tokens: List[Token] = tokenize(source)
                return first_error_value(tokens)
            end func main
            """,
            filename="UnterminatedTriple.geno",
            config=RunConfig(
                timeout=10.0,
                check_examples=False,
                modules=load_selfhost_modules(tmp_path),
            ),
        )

        assert result.ok, f"Run failed: {result.diagnostics}"
        assert "Unterminated multi-line string literal" in result.value

    def test_load_source_distinguishes_missing_from_empty(self, tmp_path: Path) -> None:
        """F-0034 — ``load_source`` must return ``None`` for a missing
        or unreadable file and ``Some("")`` for an existing-but-empty
        one.  Before the fix it collapsed missing files into an empty
        string, and unreadable files could still escape out of the host
        callback instead of reaching the CLI's error path.

        We wire mock ``fs_exists`` / ``fs_read_text`` callbacks so the
        test is hermetic rather than touching real disk.
        """
        files: dict[str, str] = {
            "/real/tiny.geno": "// hello\n",
            "/real/empty.geno": "",
        }

        def fs_exists(path: str) -> bool:
            return path in files or path == "/real/unreadable.geno"

        def fs_read_text(path: str) -> str:
            if path == "/real/unreadable.geno":
                raise OSError("permission denied")
            return files[path]

        result = run(
            """
            import Main

            func describe(path: String) -> String
                example "/nope" -> "None"
                let got: Option[String] = load_source(path)
                match got with
                    | Some(s) -> return "Some(len=" + to_string(length(s)) + ")"
                    | None -> return "None"
                end match
            end func describe

            func main() -> String
                let missing: String = describe("/does-not-exist-7f3a.geno")
                let unreadable: String = describe("/real/unreadable.geno")
                let tiny: String = describe("/real/tiny.geno")
                let empty: String = describe("/real/empty.geno")
                return missing + "|" + unreadable + "|" + tiny + "|" + empty
            end func main
            """,
            filename="LoadSourceProbe.geno",
            config=RunConfig(
                timeout=10.0,
                check_examples=False,
                modules=load_selfhost_modules(tmp_path),
                capabilities={"fs"},
                host_callbacks={
                    "fs_exists": fs_exists,
                    "fs_read_text": fs_read_text,
                },
            ),
        )

        assert result.ok, f"Run failed: {result.diagnostics}"
        # Missing / unreadable file → None; populated file → Some with
        # length 9; empty file → Some("") with length 0 (critically
        # distinct from the None cases).
        assert result.value == "None|None|Some(len=9)|Some(len=0)"


class TestSelfhostTypecheckerParity:
    """Regression tests for #665 — selfhost typechecker parity with
    the Python reference on list-pattern exhaustiveness (F-0031),
    typed-hole constraints (F-0032), and tuple-name detection
    (F-0033)."""

    def test_list_match_without_catchall_flagged_non_exhaustive(
        self, tmp_path: Path
    ) -> None:
        """F-0031 — ``check_exhaustiveness`` must flag a list-typed
        match that has no wildcard/variable catch-all.  Before the
        fix this slipped through the ``_ -> return ()`` default arm,
        so plainly non-exhaustive list matches typechecked silently."""
        result = run(
            """
            import Ast
            import Parser
            import TypeChecker
            import Types

            func main() -> String
                let source: String = "func main() -> Int\\n    let xs: List[Int] = []\\n    match xs with\\n        | [] -> return 0\\n    end match\\n    return 0\\nend func main"
                let program: Program = parse_with_filename(source, "ListMatch.geno")
                let cs: CheckerState = make_checker_with_filename("ListMatch.geno")
                let errors: Int = check_program(cs: cs, program: program, modules: mutable_map_new())
                let diags: List[Diagnostic] = vec_to_list(cs.diagnostics)
                if length(diags) == 0 then
                    return "no diagnostics"
                end if
                return format_diagnostic(head(diags))
            end func main
            """,
            filename="ListExhaustivenessDriver.geno",
            config=RunConfig(
                timeout=10.0,
                check_examples=False,
                modules=load_selfhost_modules(tmp_path),
            ),
        )
        assert result.ok, f"Run failed: {result.diagnostics}"
        assert "Non-exhaustive" in result.value
        # Error message now mirrors Python's format — ``match on List[Int]``
        # — so a future divergence shows up as a clean parity regression.
        assert "List[Int]" in result.value

    def test_list_match_with_wildcard_arm_is_accepted(self, tmp_path: Path) -> None:
        """Companion to the above: adding a wildcard arm covers the
        tail and makes the match exhaustive, so a correctly-written
        list match must still typecheck cleanly."""
        result = run(
            """
            import Ast
            import Parser
            import TypeChecker
            import Types

            func main() -> Int
                let source: String = "func main() -> Int\\n    let xs: List[Int] = []\\n    match xs with\\n        | [] -> return 0\\n        | _ -> return 1\\n    end match\\n    return 0\\nend func main"
                let program: Program = parse_with_filename(source, "ListMatchOk.geno")
                let cs: CheckerState = make_checker_with_filename("ListMatchOk.geno")
                let errors: Int = check_program(cs: cs, program: program, modules: mutable_map_new())
                return length(vec_to_list(cs.diagnostics))
            end func main
            """,
            filename="ListExhaustivenessOkDriver.geno",
            config=RunConfig(
                timeout=10.0,
                check_examples=False,
                modules=load_selfhost_modules(tmp_path),
            ),
        )
        assert result.ok, f"Run failed: {result.diagnostics}"
        assert result.value == 0

    def test_list_match_nested_tuple_type_uses_python_tuple_syntax(
        self, tmp_path: Path
    ) -> None:
        """Nested tuple element types in the new list-exhaustiveness
        diagnostic must render with Python's parenthesized tuple syntax,
        not ``Tuple[...]``. This pins the parity issue called out in
        review for F-0031."""
        result = run(
            """
            import Ast
            import Parser
            import TypeChecker
            import Types

            func main() -> String
                let source: String = "func main() -> Int\\n    let xs: List[(Int, Bool)] = []\\n    match xs with\\n        | [] -> return 0\\n    end match\\n    return 0\\nend func main"
                let program: Program = parse_with_filename(source, "TupleListMatch.geno")
                let cs: CheckerState = make_checker_with_filename("TupleListMatch.geno")
                let errors: Int = check_program(cs: cs, program: program, modules: mutable_map_new())
                let diags: List[Diagnostic] = vec_to_list(cs.diagnostics)
                if length(diags) == 0 then
                    return "no diagnostics"
                end if
                return format_diagnostic(head(diags))
            end func main
            """,
            filename="TupleListMatchDriver.geno",
            config=RunConfig(
                timeout=10.0,
                check_examples=False,
                modules=load_selfhost_modules(tmp_path),
            ),
        )
        assert result.ok, f"Run failed: {result.diagnostics}"
        assert "List[(Int, Bool)]" in result.value
        assert "Tuple[Int, Bool]" not in result.value

    def test_tuple_prefix_user_type_is_not_misclassified_selfhost(
        self, tmp_path: Path
    ) -> None:
        """F-0033 — a user-defined type whose name starts with
        ``Tuple`` must NOT be silently treated as a builtin tuple.
        Before the fix ``resolve_type`` in selfhost used
        ``starts_with("Tuple")`` which matched anything with that
        prefix.  The check is now exact on the canonical name
        emitted by the parser, so ``TupleFoo[Int]`` resolves back
        to the user type."""
        result = run(
            """
            import Ast
            import Parser
            import TypeChecker
            import Types

            func main() -> Int
                let source: String = "type TupleFoo[T] = MkTupleFoo(value: T)\\n\\nfunc take_foo(x: TupleFoo[Int]) -> Int\\n    example MkTupleFoo(5) -> 5\\n    return 0\\nend func take_foo\\n\\nfunc main() -> Int\\n    return take_foo(MkTupleFoo(42))\\nend func main"
                let program: Program = parse_with_filename(source, "TupleProbe.geno")
                let cs: CheckerState = make_checker_with_filename("TupleProbe.geno")
                let errors: Int = check_program(cs: cs, program: program, modules: mutable_map_new())
                return length(vec_to_list(cs.diagnostics))
            end func main
            """,
            filename="TupleProbeDriver.geno",
            config=RunConfig(
                timeout=10.0,
                check_examples=False,
                modules=load_selfhost_modules(tmp_path),
            ),
        )
        assert result.ok, f"Run failed: {result.diagnostics}"
        # No diagnostics: the user type ``TupleFoo[Int]`` typechecks
        # correctly because it is no longer mangled into a
        # ``TTuple([TInt])`` that would be incompatible with the
        # constructor result.
        assert result.value == 0

    def test_tuple_prefix_user_type_is_not_misclassified_python(
        self,
    ) -> None:
        """F-0033 parity half on the Python side: the reference
        implementation in ``geno/typechecker.py`` used the same
        ``startswith("Tuple")`` check.  After tightening, a
        ``TupleFoo[Int]`` annotation resolves as a ``UserType`` and
        the program typechecks and runs cleanly."""
        source = (
            "type TupleFoo[T] = MkTupleFoo(value: T)\n"
            "\n"
            "func take_foo(x: TupleFoo[Int]) -> Int\n"
            "    example MkTupleFoo(5) -> 5\n"
            "    return x.value\n"
            "end func\n"
            "\n"
            "func main() -> Int\n"
            "    return take_foo(MkTupleFoo(42))\n"
            "end func\n"
        )
        result = run(source, config=RunConfig(timeout=5.0))
        assert result.ok, f"Run failed: {result.diagnostics}"
        assert result.value == 42

    def test_typed_hole_with_constraint_typechecks_like_python(
        self, tmp_path: Path
    ) -> None:
        """F-0032 — the Python reference (``geno/typechecker.py:2262``)
        resolves a typed hole using only ``hole_type`` and discards
        the ``where`` constraint.  Selfhost does the same at
        ``TypeChecker.geno:804``.  No parity gap today; this test
        pins the shared behaviour so a future tightening (to
        actually check the constraint) gets caught before it
        silently breaks parity."""
        result = run(
            """
            import Ast
            import Parser
            import TypeChecker
            import Types

            func main() -> Int
                let source: String = "func main() -> Int\\n    let x: Int = ?todo: Int where true\\n    return x\\nend func main"
                let program: Program = parse_with_filename(source, "TypedHole.geno")
                let cs: CheckerState = make_checker_with_filename("TypedHole.geno")
                let errors: Int = check_program(cs: cs, program: program, modules: mutable_map_new())
                return length(vec_to_list(cs.diagnostics))
            end func main
            """,
            filename="TypedHoleDriver.geno",
            config=RunConfig(
                timeout=10.0,
                check_examples=False,
                modules=load_selfhost_modules(tmp_path),
            ),
        )
        assert result.ok, f"Run failed: {result.diagnostics}"
        assert result.value == 0
