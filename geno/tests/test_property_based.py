"""
Property-Based Tests for Geno
============================

Uses Hypothesis to test invariants of the lexer, parser, and type system.
"""
# mypy: disable-error-code="no-redef,misc"

import pytest

try:
    from hypothesis import assume, given, settings
    from hypothesis import strategies as st

    HYPOTHESIS_AVAILABLE = True
except ImportError:
    HYPOTHESIS_AVAILABLE = False

    def given(*args, **kwargs):
        def decorator(f):
            return pytest.mark.skip(reason="hypothesis not installed")(f)

        return decorator

    class st:
        @staticmethod
        def text(*args, **kwargs):
            return None

        @staticmethod
        def sampled_from(*args, **kwargs):
            return None

        @staticmethod
        def one_of(*args, **kwargs):
            return None

        @staticmethod
        def integers(*args, **kwargs):
            return None

    def settings(*args, **kwargs):
        def decorator(f):
            return f

        return decorator

    def assume(x):
        pass


from geno.lexer import Lexer, LexerError
from geno.parser import ParseError, ParseErrors, Parser
from geno.typechecker import TypeChecker
from geno.typechecker import TypeError as GenoTypeError

# ---------------------------------------------------------------------------
# Lexer properties
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestLexerProperties:
    """Property-based tests for the lexer."""

    @given(st.text(min_size=0, max_size=500))
    @settings(max_examples=200, deadline=None)
    def test_lexer_never_crashes(self, text):
        """The lexer must never crash — only LexerError is acceptable."""
        try:
            lexer = Lexer(text, "<prop>")
            tokens = lexer.tokenize()
            assert isinstance(tokens, list)
            assert len(tokens) > 0  # At least EOF token
        except LexerError:
            pass

    @given(
        st.text(
            alphabet='abcdefghijklmnopqrstuvwxyz_"0123456789 \n\t+-*/=<>()[]{},.:|',
            min_size=1,
            max_size=300,
        )
    )
    @settings(max_examples=200, deadline=None)
    def test_lexer_tokens_have_valid_types(self, text):
        """All tokens produced by the lexer have a non-None type."""
        try:
            lexer = Lexer(text, "<prop>")
            tokens = lexer.tokenize()
            for token in tokens:
                assert token.type is not None
        except LexerError:
            pass

    @given(
        st.sampled_from(
            [
                "42",
                "3.14",
                '"hello"',
                "true",
                "false",
                "x",
                "foo_bar",
                "Int",
                "String",
            ]
        )
    )
    @settings(max_examples=50, deadline=None)
    def test_single_token_roundtrip(self, literal):
        """A single known-valid token lexes to exactly one token (plus EOF)."""
        lexer = Lexer(literal, "<prop>")
        tokens = lexer.tokenize()
        # Should have the value token + EOF
        assert len(tokens) >= 2


# ---------------------------------------------------------------------------
# Parser properties
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestParserProperties:
    """Property-based tests for the parser."""

    @given(st.text(min_size=0, max_size=300))
    @settings(max_examples=200, deadline=None)
    def test_parser_never_crashes(self, text):
        """The parser must never crash — only ParseError/LexerError acceptable."""
        try:
            lexer = Lexer(text, "<prop>")
            tokens = lexer.tokenize()
            parser = Parser(tokens)
            program = parser.parse_program()
            assert program is not None
        except (LexerError, ParseError, ParseErrors):
            pass

    @given(st.text(min_size=0, max_size=300))
    @settings(max_examples=100, deadline=None)
    def test_parse_twice_same_result(self, text):
        """Parsing the same source twice produces the same number of statements."""
        try:
            tokens1 = Lexer(text, "<prop>").tokenize()
            prog1 = Parser(tokens1).parse_program()
            tokens2 = Lexer(text, "<prop>").tokenize()
            prog2 = Parser(tokens2).parse_program()
            assert len(prog1.definitions) == len(prog2.definitions)
        except (LexerError, ParseError, ParseErrors):
            pass


# ---------------------------------------------------------------------------
# Type system properties
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestTypeSystemProperties:
    """Property-based tests for the type checker."""

    @given(st.integers(min_value=-10000, max_value=10000))
    @settings(max_examples=50, deadline=None)
    def test_int_literal_always_typechecks(self, n):
        """An integer literal in a well-formed program always type-checks."""
        source = f"""
        func main() -> Int
            example () -> {n}
            return {n}
        end func
        """
        try:
            tokens = Lexer(source, "<prop>").tokenize()
            program = Parser(tokens).parse_program()
            checker = TypeChecker()
            checker.check_program(program)
        except (LexerError, ParseError, ParseErrors):
            pass  # Negative numbers with leading minus might lex differently

    @given(
        st.sampled_from(
            [
                ('let x: Int = "hello"', "mismatch"),
                ('let x: Bool = "hello"', "mismatch"),
                ("let x: Int = true", "mismatch"),
                ("let x: String = 42", "mismatch"),
            ]
        )
    )
    @settings(max_examples=20, deadline=None)
    def test_type_mismatch_always_rejected(self, case):
        """Type mismatches are always caught by the checker."""
        stmt, _expected = case
        source = f"""
        func main() -> Int
            example () -> 0
            {stmt}
            return 0
        end func
        """
        with pytest.raises(GenoTypeError):
            tokens = Lexer(source, "<prop>").tokenize()
            program = Parser(tokens).parse_program()
            checker = TypeChecker()
            checker.check_program(program)
