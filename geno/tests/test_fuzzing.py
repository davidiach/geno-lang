"""
Property-Based Fuzzing Tests for Geno
======================================

Uses Hypothesis to generate random inputs and test invariants.
"""
# mypy: disable-error-code="no-redef,misc"

import pytest

try:
    from hypothesis import assume, given, settings
    from hypothesis import strategies as st

    # Trigger AttributeError for partial/shadowed installs (e.g. namespace
    # packages without the real implementation).
    _ = st.integers
    HYPOTHESIS_AVAILABLE = True
except (ImportError, AttributeError):
    HYPOTHESIS_AVAILABLE = False

    # Create dummy decorators for when hypothesis isn't installed
    def given(*args, **kwargs):
        def decorator(f):
            return pytest.mark.skip(reason="hypothesis not installed")(f)

        return decorator

    class _StubStrategy:
        """Stub for any hypothesis strategy or strategy attribute lookup."""

        def __call__(self, *args, **kwargs):
            return self

        def __getattr__(self, name):
            return self

    class _StubSt:
        """Stub for hypothesis.strategies — every attribute returns a stub."""

        def __getattr__(self, name):
            return _StubStrategy()

    st = _StubSt()  # type: ignore[assignment]

    def settings(*args, **kwargs):
        def decorator(f):
            return f

        return decorator

    def assume(x):
        pass


import re

from geno.api import RunConfig, run
from geno.capabilities import KNOWN_CAPABILITIES
from geno.diagnostics import ErrorCode
from geno.formatter import format_source
from geno.lexer import Lexer, LexerError
from geno.parser import ParseError, ParseErrors, Parser
from geno.tokens import KEYWORDS
from geno.typechecker import TypeChecker, TypeError


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestLexerFuzzing:
    """Fuzz test the lexer with random inputs."""

    @given(st.text(min_size=0, max_size=1000))
    @settings(max_examples=100, deadline=None)
    def test_lexer_does_not_crash(self, text):
        """Lexer should not crash on arbitrary input."""
        try:
            lexer = Lexer(text, "<fuzz>")
            tokens = lexer.tokenize()
            # If we got here, the lexer didn't crash
            assert isinstance(tokens, list)
        except LexerError:
            # LexerError is expected for invalid input
            pass

    @given(
        st.text(
            alphabet="abcdefghijklmnopqrstuvwxyz_0123456789 \n\t",
            min_size=0,
            max_size=500,
        )
    )
    @settings(max_examples=100, deadline=None)
    def test_lexer_with_identifier_chars(self, text):
        """Lexer should handle identifier-like characters."""
        try:
            lexer = Lexer(text, "<fuzz>")
            tokens = lexer.tokenize()
            assert isinstance(tokens, list)
        except LexerError:
            pass

    @given(st.text(alphabet="0123456789.+-", min_size=1, max_size=50))
    @settings(max_examples=100, deadline=None)
    def test_lexer_with_numeric_chars(self, text):
        """Lexer should handle numeric-like strings."""
        try:
            lexer = Lexer(text, "<fuzz>")
            tokens = lexer.tokenize()
            assert isinstance(tokens, list)
        except LexerError:
            pass


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestParserFuzzing:
    """Fuzz test the parser with generated tokens."""

    @given(st.lists(st.integers(min_value=0, max_value=100), min_size=0, max_size=20))
    @settings(max_examples=50, deadline=None)
    def test_parser_with_random_integers(self, nums):
        """Parser should handle lists of integers in expressions."""
        # Build a simple expression from integers
        if not nums:
            return

        # Create a valid expression: let x: Int = <nums[0]> + <nums[1]> + ...
        expr = " + ".join(str(n) for n in nums)
        source = f"""
func check_val() -> Int
    example 0 -> 0
    let x: Int = {expr}
    return x
end func check_val
"""
        try:
            lexer = Lexer(source, "<fuzz>")
            tokens = lexer.tokenize()
            parser = Parser(tokens)
            program = parser.parse_program()
            assert program is not None
        except (LexerError, ParseError):
            pass


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestTypecheckerFuzzing:
    """Fuzz test the typechecker."""

    @given(
        st.lists(st.integers(min_value=-1000, max_value=1000), min_size=1, max_size=10)
    )
    @settings(max_examples=50, deadline=None)
    def test_typechecker_with_list_literals(self, nums):
        """Typechecker should handle list literals."""
        list_str = "[" + ", ".join(str(n) for n in nums) + "]"
        source = f"""
func check_val() -> Int
    example [1] -> 1
    let xs: List[Int] = {list_str}
    return length(xs)
end func check_val
"""
        try:
            lexer = Lexer(source, "<fuzz>")
            tokens = lexer.tokenize()
            parser = Parser(tokens)
            program = parser.parse_program()
            checker = TypeChecker()
            checker.check_program(program)
        except (LexerError, ParseError, TypeError):
            pass


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestValidGenoPrograms:
    """Test with valid Geno program fragments."""

    @given(st.integers(min_value=0, max_value=100))
    @settings(max_examples=50, deadline=None)
    def test_factorial_like_functions(self, n):
        """Factorial-style recursion should typecheck."""
        source = """
func factorial(n: Int) -> Int
    requires n >= 0
    example 0 -> 1
    example 5 -> 120

    if n <= 1 then
        return 1
    else
        return n * factorial(n - 1)
    end if
end func factorial
"""
        lexer = Lexer(source, "<fuzz>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        checker = TypeChecker()
        checker.check_program(program)

    @given(st.text(alphabet="abcdefghij", min_size=1, max_size=10))
    @settings(max_examples=50, deadline=None)
    def test_variable_names(self, name):
        """Valid variable names should be accepted."""
        assume(not name[0].isdigit())  # Variables can't start with digit
        assume(
            name
            not in {
                "if",
                "then",
                "else",
                "while",
                "do",
                "for",
                "in",
                "func",
                "end",
                "let",
                "var",
                "return",
                "match",
                "with",
                "type",
                "requires",
                "ensures",
                "example",
                "and",
                "or",
                "not",
                "true",
                "false",
                "fn",
                "where",
                "test",
                "assert",
            }
        )

        source = f"""
func check_val() -> Int
    example 1 -> 1
    let {name}: Int = 42
    return {name}
end func check_val
"""
        try:
            lexer = Lexer(source, "<fuzz>")
            tokens = lexer.tokenize()
            parser = Parser(tokens)
            program = parser.parse_program()
            checker = TypeChecker()
            checker.check_program(program)
        except (LexerError, ParseError, TypeError):
            # Some names might still be invalid
            pass


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestSandboxFuzzing:
    """Fuzz test the sandbox with various code patterns."""

    @given(st.text(min_size=0, max_size=200))
    @settings(max_examples=50, deadline=None)
    def test_sandbox_does_not_hang_on_arbitrary_input(self, code):
        """Sandbox should not hang on arbitrary input (with timeout)."""
        from geno.sandbox import SandboxConfig, SandboxError, run_sandboxed

        # Short timeout to avoid hanging
        config = SandboxConfig(timeout=0.5, strict=True)

        try:
            _result, _output = run_sandboxed(code, config)
            # If we got here, execution completed
        except SandboxError:
            # Expected for invalid/dangerous code
            pass
        except SyntaxError:
            # Python syntax error is fine
            pass
        except Exception:
            # Other exceptions are acceptable too
            pass


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestImportFuzzing:
    """Fuzz test import statements with random module names."""

    @given(
        st.text(
            alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
            min_size=1,
            max_size=20,
        )
    )
    @settings(max_examples=50, deadline=None)
    def test_import_with_random_names(self, name):
        """Import with random names should not crash the parser."""
        source = f"import {name}\nfunc main() -> Int\n    return 0\nend func\n"
        try:
            lexer = Lexer(source, "<fuzz>")
            tokens = lexer.tokenize()
            parser = Parser(tokens)
            program = parser.parse_program()
            assert program is not None
        except (LexerError, ParseError, ParseErrors):
            pass


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestCapabilityComboFuzzing:
    """Fuzz test different capability combinations."""

    @given(
        st.lists(
            st.sampled_from(sorted(KNOWN_CAPABILITIES)),
            min_size=0,
            max_size=5,
        )
    )
    @settings(max_examples=50, deadline=None)
    def test_random_capability_sets(self, caps):
        """Random capability sets should not crash the API."""
        source = """
func main() -> Int
    return 42
end func
"""
        config = RunConfig(capabilities=set(caps), timeout=2.0)
        result = run(source, config=config)
        # Should always succeed since main() doesn't use any gated builtins
        assert result.ok is True
        assert result.value == 42


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestHostCallbackFuzzing:
    """Fuzz test host callback error paths."""

    @given(st.text(min_size=0, max_size=100))
    @settings(max_examples=50, deadline=None)
    def test_fs_callback_return_values(self, return_val):
        """Host callbacks returning arbitrary strings should not crash."""
        source = """
func main() -> String
    return fs_read_text(path: "/test")
end func
"""

        def callback(path):
            return return_val

        config = RunConfig(
            capabilities={"fs"},
            host_callbacks={"fs_read_text": callback},
            timeout=2.0,
        )
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == return_val


# ---------------------------------------------------------------------------
# Strategies for generating valid Geno program fragments
# ---------------------------------------------------------------------------

if HYPOTHESIS_AVAILABLE:
    # Simple types
    _simple_types = st.sampled_from(["Int", "Float", "String", "Bool"])

    # Integer expressions (recursive)
    _int_expr = st.recursive(
        st.integers(min_value=-1000, max_value=1000).map(str),
        lambda children: st.one_of(
            st.tuples(children, st.sampled_from(["+", "-", "*"]), children).map(
                lambda t: f"({t[0]} {t[1]} {t[2]})"
            ),
        ),
        max_leaves=10,
    )

    # Bool expressions
    _bool_expr = st.one_of(
        st.just("true"),
        st.just("false"),
        st.tuples(
            st.integers(min_value=-100, max_value=100).map(str),
            st.sampled_from(["<", ">", "<=", ">=", "==", "!="]),
            st.integers(min_value=-100, max_value=100).map(str),
        ).map(lambda t: f"{t[0]} {t[1]} {t[2]}"),
    )

    # Variable names that avoid Geno keywords.
    _KEYWORDS = set(KEYWORDS)
    _var_name = st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz_",
        min_size=2,
        max_size=8,
    ).filter(lambda n: n not in _KEYWORDS and not n.startswith("_") and n[0].isalpha())
else:
    # Stubs so module-level @given(_int_expr) decorators don't NameError when
    # hypothesis isn't installed. The associated classes are skipped at runtime
    # via @pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, ...).
    _simple_types = None  # type: ignore[assignment]
    _int_expr = None  # type: ignore[assignment]
    _bool_expr = None  # type: ignore[assignment]
    _var_name = None  # type: ignore[assignment]


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestParserAllConstructs:
    """Fuzz the parser with all major grammar constructs."""

    @given(
        st.integers(min_value=-1000, max_value=1000),
        st.integers(min_value=-1000, max_value=1000),
    )
    @settings(max_examples=50, deadline=None)
    def test_if_then_else(self, a, b):
        """Parser handles if/then/else with random comparisons."""
        source = f"""
func check_val() -> Int
    example 0 -> 0
    if {a} > {b} then
        return {a}
    else
        return {b}
    end if
end func
"""
        lexer = Lexer(source, "<fuzz>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        assert len(program.definitions) == 1

    @given(st.lists(st.integers(min_value=0, max_value=10), min_size=1, max_size=5))
    @settings(max_examples=50, deadline=None)
    def test_match_expression(self, values):
        """Parser handles match/with blocks."""
        arms = "\n".join(f"    | {v} -> return {v * 10}" for v in values)
        source = f"""
func check_val(x: Int) -> Int
    example 0 -> 0
    match x with
{arms}
    | _ -> return -1
    end match
end func
"""
        lexer = Lexer(source, "<fuzz>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        assert program is not None

    @given(st.lists(st.integers(min_value=0, max_value=50), min_size=1, max_size=5))
    @settings(max_examples=30, deadline=None)
    def test_for_loop(self, nums):
        """Parser handles for loops."""
        list_str = "[" + ", ".join(str(n) for n in nums) + "]"
        source = f"""
func check_val() -> Int
    example () -> 0
    var total: Int = 0
    for item: Int in {list_str} do
        var total: Int = total + item
    end for
    return total
end func
"""
        lexer = Lexer(source, "<fuzz>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        assert program is not None

    @given(st.integers(min_value=1, max_value=10))
    @settings(max_examples=30, deadline=None)
    def test_while_loop(self, n):
        """Parser handles while loops."""
        source = f"""
func check_val() -> Int
    example () -> 0
    var count: Int = {n}
    while count > 0 do
        var count: Int = count - 1
    end while
    return count
end func
"""
        lexer = Lexer(source, "<fuzz>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        assert program is not None

    @given(
        st.integers(min_value=-100, max_value=100),
        st.integers(min_value=-100, max_value=100),
    )
    @settings(max_examples=30, deadline=None)
    def test_try_catch(self, a, b):
        """Parser handles try/catch blocks."""
        source = f"""
func check_val() -> Int
    example 0 -> 0
    try
        return {a} + {b}
    catch err: String
        return 0
    end try
end func
"""
        lexer = Lexer(source, "<fuzz>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        assert program is not None

    @given(
        st.lists(
            st.text(alphabet="abcdefghij", min_size=2, max_size=6),
            min_size=1,
            max_size=4,
            unique=True,
        )
    )
    @settings(max_examples=30, deadline=None)
    def test_type_definitions(self, variant_names):
        """Parser handles type definitions with variants."""
        # Make variant names PascalCase
        variants = [n.capitalize() for n in variant_names]
        # Ensure unique after capitalization
        assume(len(set(variants)) == len(variants))
        # Ensure no Geno keywords
        assume(all(v.lower() not in _KEYWORDS for v in variants))

        first = variants[0]
        rest = "\n".join(f"    | {v}" for v in variants[1:])
        type_body = f"type MyType = {first}"
        if rest:
            type_body += f"\n{rest}"

        source = f"""{type_body}

func check_val() -> MyType
    example () -> {first}
    return {first}
end func
"""
        try:
            lexer = Lexer(source, "<fuzz>")
            tokens = lexer.tokenize()
            parser = Parser(tokens)
            program = parser.parse_program()
            assert program is not None
        except (LexerError, ParseError, ParseErrors):
            pass  # Some generated names may collide with builtins


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestTypecheckerExpanded:
    """Expanded typechecker fuzzing with diverse constructs."""

    @given(_int_expr)
    @settings(max_examples=50, deadline=None)
    def test_integer_expressions_typecheck(self, expr):
        """Random integer expression trees should typecheck."""
        source = f"""
func check_val() -> Int
    example () -> 0
    return {expr}
end func
"""
        lexer = Lexer(source, "<fuzz>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        checker = TypeChecker()
        checker.check_program(program)

    @given(_bool_expr)
    @settings(max_examples=50, deadline=None)
    def test_boolean_expressions_typecheck(self, expr):
        """Random boolean expressions should typecheck."""
        source = f"""
func check_val() -> Bool
    example () -> true
    return {expr}
end func
"""
        lexer = Lexer(source, "<fuzz>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        checker = TypeChecker()
        checker.check_program(program)

    @given(
        st.lists(st.integers(min_value=-100, max_value=100), min_size=0, max_size=10),
        st.sampled_from(["map", "filter"]),
    )
    @settings(max_examples=50, deadline=None)
    def test_list_higher_order_functions(self, nums, fn):
        """Typechecker handles map/filter on list literals."""
        list_str = "[" + ", ".join(str(n) for n in nums) + "]"
        if fn == "map":
            source = f"""
func check_val() -> List[Int]
    example () -> []
    return map({list_str}, fn(x: Int) -> x * 2)
end func
"""
        else:
            source = f"""
func check_val() -> List[Int]
    example () -> []
    return filter({list_str}, fn(x: Int) -> x > 0)
end func
"""
        lexer = Lexer(source, "<fuzz>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        checker = TypeChecker()
        checker.check_program(program)

    @given(
        st.lists(
            st.tuples(
                _var_name,
                st.sampled_from(["Int", "String", "Bool"]),
            ),
            min_size=1,
            max_size=3,
            unique_by=lambda t: t[0],
        )
    )
    @settings(max_examples=50, deadline=None)
    def test_let_bindings_typecheck(self, bindings):
        """Random let bindings with explicit types should typecheck."""
        defaults = {"Int": "0", "String": '"hello"', "Bool": "true"}
        lets = "\n".join(
            f"    let {name}: {typ} = {defaults[typ]}" for name, typ in bindings
        )
        first_name = bindings[0][0]
        first_type = bindings[0][1]
        source = f"""
func check_val() -> {first_type}
    example () -> {defaults[first_type]}
{lets}
    return {first_name}
end func
"""
        lexer = Lexer(source, "<fuzz>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        checker = TypeChecker()
        checker.check_program(program)

    @given(
        st.integers(min_value=0, max_value=5),
        st.integers(min_value=0, max_value=5),
    )
    @settings(max_examples=30, deadline=None)
    def test_nested_if_typecheck(self, depth_a, depth_b):
        """Nested if/else should typecheck correctly."""
        assume(depth_a + depth_b <= 6)
        body = "return 0"
        for i in range(depth_a):
            body = (
                f"if {i} < {i + 1} then\n"
                f"        {body}\n"
                f"    else\n"
                f"        return {i}\n"
                f"    end if"
            )
        source = f"""
func check_val() -> Int
    example () -> 0
    {body}
end func
"""
        lexer = Lexer(source, "<fuzz>")
        tokens = lexer.tokenize()
        parser = Parser(tokens)
        program = parser.parse_program()
        checker = TypeChecker()
        checker.check_program(program)


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestFormatterRoundtrip:
    """Property: parse(format(source)) is structurally equivalent to parse(source)."""

    def _strip_locations(self, s: str) -> str:
        """Remove SourceLocation details for structural comparison."""
        return re.sub(r"(location|filename)=SourceLocation\([^)]*\)", "LOC", s)

    def _assert_roundtrip(self, source: str):
        """Verify that formatting preserves parse structure."""
        try:
            lexer1 = Lexer(source, "<test>")
            tokens1 = lexer1.tokenize()
            parser1 = Parser(tokens1)
            ast1 = parser1.parse_program()
        except (LexerError, ParseError, ParseErrors):
            assume(False)
            return

        formatted = format_source(source)

        try:
            lexer2 = Lexer(formatted, "<test>")
            tokens2 = lexer2.tokenize()
            parser2 = Parser(tokens2)
            ast2 = parser2.parse_program()
        except (LexerError, ParseError, ParseErrors):
            raise AssertionError(f"Formatted source failed to parse:\n{formatted}")

        s1 = self._strip_locations(str(ast1))
        s2 = self._strip_locations(str(ast2))
        assert s1 == s2, "AST mismatch after formatting"

    @given(
        st.integers(min_value=-100, max_value=100),
        st.integers(min_value=-100, max_value=100),
    )
    @settings(max_examples=50, deadline=None)
    def test_arithmetic_roundtrip(self, a, b):
        """Formatting arithmetic functions preserves structure."""
        source = f"""func add() -> Int
example () -> {a + b}
return {a} + {b}
end func
"""
        self._assert_roundtrip(source)

    @given(
        st.integers(min_value=0, max_value=50),
        st.integers(min_value=0, max_value=50),
    )
    @settings(max_examples=30, deadline=None)
    def test_if_else_roundtrip(self, a, b):
        """Formatting if/else blocks preserves structure."""
        source = f"""func bigger() -> Int
example () -> {max(a, b)}
if {a} > {b} then
return {a}
else
return {b}
end if
end func
"""
        self._assert_roundtrip(source)

    @given(st.lists(st.integers(min_value=0, max_value=5), min_size=1, max_size=4))
    @settings(max_examples=30, deadline=None)
    def test_match_roundtrip(self, values):
        """Formatting match blocks preserves structure."""
        arms = "\n".join(f"| {v} -> return {v}" for v in values)
        source = f"""func classify(x: Int) -> Int
example 0 -> 0
match x with
{arms}
| _ -> return -1
end match
end func
"""
        self._assert_roundtrip(source)

    @given(st.lists(st.integers(min_value=0, max_value=20), min_size=1, max_size=5))
    @settings(max_examples=20, deadline=None)
    def test_for_loop_roundtrip(self, nums):
        """Formatting for loops preserves structure."""
        list_str = "[" + ", ".join(str(n) for n in nums) + "]"
        source = f"""func sum_to() -> Int
example () -> 0
var total: Int = 0
for item: Int in {list_str} do
var total: Int = total + item
end for
return total
end func
"""
        self._assert_roundtrip(source)


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestStringBuiltinParity:
    """Test string builtins produce consistent results across backends."""

    @given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz ", min_size=0, max_size=30))
    @settings(max_examples=50, deadline=None)
    def test_to_upper_to_lower_roundtrip(self, text):
        """to_lower(to_upper(s)) is idempotent on ASCII."""
        # Escape quotes in the string
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        source = f'''
func main() -> String
    return to_lower(to_upper("{escaped}"))
end func
'''
        config = RunConfig(timeout=5.0)
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == text.upper().lower()

    @given(
        st.text(alphabet="abcdefghij.", min_size=1, max_size=20),
        st.text(alphabet=".,;:", min_size=1, max_size=3),
    )
    @settings(max_examples=50, deadline=None)
    def test_split_join_roundtrip(self, text, sep):
        """join(split(s, sep), sep) == s when s doesn't start/end with sep."""
        assume(sep not in text)  # Simplify: avoid split edge cases
        escaped_text = text.replace("\\", "\\\\").replace('"', '\\"')
        escaped_sep = sep.replace("\\", "\\\\").replace('"', '\\"')
        source = f'''
func main() -> String
    let parts: List[String] = split("{escaped_text}", "{escaped_sep}")
    return join(parts, "{escaped_sep}")
end func
'''
        config = RunConfig(timeout=5.0)
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == text

    @given(st.text(alphabet="abcdefghij ", min_size=0, max_size=30))
    @settings(max_examples=50, deadline=None)
    def test_length_matches_python(self, text):
        """length(s) matches Python's len(s)."""
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        source = f'''
func main() -> Int
    return length("{escaped}")
end func
'''
        config = RunConfig(timeout=5.0)
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == len(text)

    @given(
        st.text(alphabet="abcdefghij ", min_size=1, max_size=20),
        st.text(alphabet="abc", min_size=1, max_size=3),
        st.text(alphabet="xyz", min_size=1, max_size=3),
    )
    @settings(max_examples=50, deadline=None)
    def test_replace_matches_python(self, text, old, new):
        """replace(s, old, new) matches Python's str.replace."""
        esc_text = text.replace("\\", "\\\\").replace('"', '\\"')
        esc_old = old.replace("\\", "\\\\").replace('"', '\\"')
        esc_new = new.replace("\\", "\\\\").replace('"', '\\"')
        source = f'''
func main() -> String
    return replace(text: "{esc_text}", old: "{esc_old}", new: "{esc_new}")
end func
'''
        config = RunConfig(timeout=5.0)
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == text.replace(old, new)

    @given(
        st.text(alphabet="abcdefghij", min_size=0, max_size=20),
        st.text(alphabet="abc", min_size=1, max_size=5),
    )
    @settings(max_examples=50, deadline=None)
    def test_starts_with_matches_python(self, text, prefix):
        """starts_with(s, p) matches Python's str.startswith."""
        esc_text = text.replace("\\", "\\\\").replace('"', '\\"')
        esc_prefix = prefix.replace("\\", "\\\\").replace('"', '\\"')
        source = f'''
func main() -> Bool
    return starts_with("{esc_text}", "{esc_prefix}")
end func
'''
        config = RunConfig(timeout=5.0)
        result = run(source, config=config)
        assert result.ok is True
        assert result.value == text.startswith(prefix)
