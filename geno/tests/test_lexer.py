"""
Tests for the Geno Lexer
========================
"""

import os
import sys

import pytest

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.lexer import Lexer, LexerError
from geno.tokens import SourceLocation, Token, TokenType


class TestLexerBasics:
    """Basic lexer functionality tests."""

    def test_empty_input(self):
        """Empty input produces only EOF."""
        lexer = Lexer("")
        tokens = lexer.tokenize()
        assert len(tokens) == 1
        assert tokens[0].type == TokenType.EOF

    def test_whitespace_only(self):
        """Whitespace-only input produces only EOF."""
        lexer = Lexer("   \t\n\r\n   ")
        tokens = lexer.tokenize()
        assert len(tokens) == 1
        assert tokens[0].type == TokenType.EOF

    def test_single_integer(self):
        """Single integer token."""
        lexer = Lexer("42")
        tokens = lexer.tokenize()
        assert len(tokens) == 2
        assert tokens[0].type == TokenType.INTEGER
        assert tokens[0].value == 42

    def test_single_float(self):
        """Single float token."""
        lexer = Lexer("3.14")
        tokens = lexer.tokenize()
        assert len(tokens) == 2
        assert tokens[0].type == TokenType.FLOAT
        assert tokens[0].value == 3.14

    def test_single_string(self):
        """Single string token."""
        lexer = Lexer('"hello"')
        tokens = lexer.tokenize()
        assert len(tokens) == 2
        assert tokens[0].type == TokenType.STRING
        assert tokens[0].value == "hello"

    def test_string_escapes(self):
        """String with escape sequences."""
        lexer = Lexer(r'"hello\nworld\t!"')
        tokens = lexer.tokenize()
        assert tokens[0].value == "hello\nworld\t!"

    def test_multiline_string(self):
        """Triple-quoted multiline string."""
        lexer = Lexer('"""line1\nline2\nline3"""')
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.STRING
        assert "line1" in tokens[0].value
        assert "line2" in tokens[0].value


class TestLexerKeywords:
    """Keyword recognition tests."""

    def test_all_keywords(self):
        """All keywords are recognized."""
        keywords = [
            ("func", TokenType.FUNC),
            ("end", TokenType.END),
            ("let", TokenType.LET),
            ("var", TokenType.VAR),
            ("if", TokenType.IF),
            ("then", TokenType.THEN),
            ("else", TokenType.ELSE),
            ("while", TokenType.WHILE),
            ("do", TokenType.DO),
            ("for", TokenType.FOR),
            ("in", TokenType.IN),
            ("match", TokenType.MATCH),
            ("with", TokenType.WITH),
            ("return", TokenType.RETURN),
            ("and", TokenType.AND),
            ("or", TokenType.OR),
            ("not", TokenType.NOT),
            ("true", TokenType.TRUE),
            ("false", TokenType.FALSE),
            ("type", TokenType.TYPE),
            ("requires", TokenType.REQUIRES),
            ("ensures", TokenType.ENSURES),
            ("example", TokenType.EXAMPLE),
            ("ref", TokenType.REF),
            ("fn", TokenType.FN),
        ]

        for keyword, expected_type in keywords:
            lexer = Lexer(keyword)
            tokens = lexer.tokenize()
            assert tokens[0].type == expected_type, f"Failed for keyword: {keyword}"


class TestLexerIdentifiers:
    """Identifier recognition tests."""

    def test_simple_identifier(self):
        """Simple snake_case identifier."""
        lexer = Lexer("foo_bar")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.IDENTIFIER
        assert tokens[0].value == "foo_bar"

    def test_type_identifier(self):
        """PascalCase type identifier."""
        lexer = Lexer("FooBar")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.TYPE_IDENTIFIER
        assert tokens[0].value == "FooBar"

    def test_identifier_with_numbers(self):
        """Identifier with numbers."""
        lexer = Lexer("var123")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.IDENTIFIER
        assert tokens[0].value == "var123"


class TestLexerOperators:
    """Operator recognition tests."""

    def test_two_char_operators(self):
        """Two-character operators."""
        operators = [
            ("->", TokenType.ARROW),
            ("|>", TokenType.PIPE),
            ("==", TokenType.EQ),
            ("!=", TokenType.NEQ),
            ("<=", TokenType.LTE),
            (">=", TokenType.GTE),
        ]

        for op, expected_type in operators:
            lexer = Lexer(op)
            tokens = lexer.tokenize()
            assert tokens[0].type == expected_type, f"Failed for operator: {op}"

    def test_single_char_operators(self):
        """Single-character operators."""
        operators = [
            ("+", TokenType.PLUS),
            ("-", TokenType.MINUS),
            ("*", TokenType.STAR),
            ("/", TokenType.SLASH),
            ("%", TokenType.PERCENT),
            ("<", TokenType.LT),
            (">", TokenType.GT),
            ("=", TokenType.ASSIGN),
            ("|", TokenType.BAR),
        ]

        for op, expected_type in operators:
            lexer = Lexer(op)
            tokens = lexer.tokenize()
            assert tokens[0].type == expected_type, f"Failed for operator: {op}"


class TestLexerDelimiters:
    """Delimiter recognition tests."""

    def test_delimiters(self):
        """All delimiters are recognized."""
        delimiters = [
            ("(", TokenType.LPAREN),
            (")", TokenType.RPAREN),
            ("[", TokenType.LBRACKET),
            ("]", TokenType.RBRACKET),
            ("{", TokenType.LBRACE),
            ("}", TokenType.RBRACE),
            (",", TokenType.COMMA),
            (":", TokenType.COLON),
            (";", TokenType.SEMICOLON),
            (".", TokenType.DOT),
            ("_", TokenType.UNDERSCORE),
            ("?", TokenType.QUESTION),
        ]

        for delim, expected_type in delimiters:
            lexer = Lexer(delim)
            tokens = lexer.tokenize()
            assert tokens[0].type == expected_type, f"Failed for delimiter: {delim}"


class TestLexerComments:
    """Comment handling tests."""

    def test_single_line_comment(self):
        """Single-line comment is skipped."""
        lexer = Lexer("42 // this is a comment\n43")
        tokens = lexer.tokenize()
        assert len(tokens) == 3  # 42, 43, EOF
        assert tokens[0].value == 42
        assert tokens[1].value == 43

    def test_block_comment(self):
        """Block comment is skipped."""
        lexer = Lexer("42 /* this is\na block\ncomment */ 43")
        tokens = lexer.tokenize()
        assert len(tokens) == 3
        assert tokens[0].value == 42
        assert tokens[1].value == 43

    def test_line_comment_preserves_next_token_location(self):
        """Skipping // comments must keep the next token's line/column correct."""
        lexer = Lexer("42 // comment\nbar")
        tokens = lexer.tokenize()
        assert tokens[1].type == TokenType.IDENTIFIER
        assert tokens[1].value == "bar"
        assert tokens[1].location.line == 2
        assert tokens[1].location.column == 1


class TestLexerComplexExamples:
    """Complex tokenization examples."""

    def test_function_definition(self):
        """Function definition tokenizes correctly."""
        source = "func foo(x: Int) -> Int"
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        expected = [
            TokenType.FUNC,
            TokenType.IDENTIFIER,  # foo
            TokenType.LPAREN,
            TokenType.IDENTIFIER,  # x
            TokenType.COLON,
            TokenType.TYPE_IDENTIFIER,  # Int
            TokenType.RPAREN,
            TokenType.ARROW,
            TokenType.TYPE_IDENTIFIER,  # Int
            TokenType.EOF,
        ]

        assert len(tokens) == len(expected)
        for tok, exp_type in zip(tokens, expected):
            assert tok.type == exp_type

    def test_expression(self):
        """Expression tokenizes correctly."""
        source = "x + y * 3"
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        assert tokens[0].type == TokenType.IDENTIFIER
        assert tokens[1].type == TokenType.PLUS
        assert tokens[2].type == TokenType.IDENTIFIER
        assert tokens[3].type == TokenType.STAR
        assert tokens[4].type == TokenType.INTEGER


class TestLexerErrors:
    """Lexer error handling tests."""

    def test_unterminated_string(self):
        """Unterminated string raises error."""
        lexer = Lexer('"hello')
        with pytest.raises(LexerError):
            lexer.tokenize()

    def test_invalid_escape(self):
        """Invalid escape sequence raises error."""
        lexer = Lexer('"hello\\z"')
        with pytest.raises(LexerError):
            lexer.tokenize()

    def test_unterminated_block_comment(self):
        """Unterminated block comment raises error."""
        lexer = Lexer("/* never closed")
        with pytest.raises(LexerError):
            lexer.tokenize()


class TestLexerSourceLocations:
    """Source location tracking tests."""

    def test_line_tracking(self):
        """Line numbers are tracked correctly."""
        source = "foo\nbar\nbaz"
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        assert tokens[0].location.line == 1
        assert tokens[1].location.line == 2
        assert tokens[2].location.line == 3

    def test_column_tracking(self):
        """Column numbers are tracked correctly."""
        source = "foo bar baz"
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        assert tokens[0].location.column == 1
        assert tokens[1].location.column == 5
        assert tokens[2].location.column == 9

    def test_column_tracking_for_mixed_fast_path_tokens(self):
        """Numbers and identifiers keep accurate columns under direct scans."""
        source = "123 45.6 FooBar _"
        lexer = Lexer(source)
        tokens = lexer.tokenize()

        assert tokens[0].type == TokenType.INTEGER
        assert tokens[0].location.column == 1
        assert tokens[1].type == TokenType.FLOAT
        assert tokens[1].location.column == 5
        assert tokens[2].type == TokenType.TYPE_IDENTIFIER
        assert tokens[2].location.column == 10
        assert tokens[3].type == TokenType.UNDERSCORE
        assert tokens[3].location.column == 17


class TestLexerUnicode:
    """Unicode handling tests — ensures non-ASCII chars don't sneak through."""

    def test_superscript_digit_rejected(self):
        """Superscript ² should not be treated as a digit."""
        lexer = Lexer("²")
        with pytest.raises(LexerError):
            lexer.tokenize()

    def test_non_ascii_letter_rejected(self):
        """Non-ASCII letters (e.g. ñ, ü) should not start identifiers."""
        lexer = Lexer("ñ")
        with pytest.raises(LexerError):
            lexer.tokenize()

    def test_fullwidth_digit_rejected(self):
        """Fullwidth digit ３ should not be treated as a digit."""
        lexer = Lexer("３")
        with pytest.raises(LexerError):
            lexer.tokenize()

    def test_ascii_digits_still_work(self):
        """Normal ASCII digits still parse correctly."""
        lexer = Lexer("123")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.INTEGER
        assert tokens[0].value == 123

    def test_ascii_identifiers_still_work(self):
        """Normal ASCII identifiers still parse correctly."""
        lexer = Lexer("hello_world")
        tokens = lexer.tokenize()
        assert tokens[0].type == TokenType.IDENTIFIER
        assert tokens[0].value == "hello_world"


class TestTokenHashing:
    """Token defines value-based __eq__, so it must be hashable to match."""

    def test_token_is_hashable(self):
        loc = SourceLocation(1, 1, "x")
        assert hash(Token(TokenType.INTEGER, 5, loc)) is not None

    def test_equal_tokens_hash_equal(self):
        loc_a = SourceLocation(1, 1, "x")
        loc_b = SourceLocation(1, 1, "x")
        a = Token(TokenType.INTEGER, 5, loc_a)
        b = Token(TokenType.INTEGER, 5, loc_b)
        assert a == b
        assert hash(a) == hash(b)

    def test_tokens_usable_in_set(self):
        loc = SourceLocation(1, 1, "x")
        a = Token(TokenType.INTEGER, 5, loc)
        b = Token(TokenType.INTEGER, 5, SourceLocation(1, 1, "x"))
        c = Token(TokenType.FLOAT, 5.0, loc)
        assert len({a, b, c}) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
