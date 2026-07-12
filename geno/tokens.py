"""
Geno Token Definitions
======================

Defines all token types and the Token class for lexical analysis.
"""

from dataclasses import FrozenInstanceError
from enum import Enum, auto
from typing import Any, Optional


class TokenType(Enum):
    """All token types in the Geno language."""

    # Literals
    INTEGER = auto()
    FLOAT = auto()
    STRING = auto()
    FSTRING = auto()  # f-string: f"text {expr} text"
    TRUE = auto()
    FALSE = auto()

    # Identifiers
    IDENTIFIER = auto()  # snake_case: variables, functions
    TYPE_IDENTIFIER = auto()  # PascalCase: types, constructors

    # Keywords
    FUNC = auto()
    END = auto()
    LET = auto()
    VAR = auto()
    IF = auto()
    THEN = auto()
    ELSE = auto()
    WHILE = auto()
    DO = auto()
    FOR = auto()
    IN = auto()
    MATCH = auto()
    WITH = auto()
    RETURN = auto()
    AND = auto()
    OR = auto()
    NOT = auto()
    TYPE = auto()
    REQUIRES = auto()
    ENSURES = auto()
    EXAMPLE = auto()
    REF = auto()
    FN = auto()
    WHERE = auto()
    IMPORT = auto()
    BREAK = auto()
    CONTINUE = auto()
    TRY = auto()
    CATCH = auto()
    THROW = auto()
    TRAIT = auto()
    IMPL = auto()
    ASYNC = auto()
    AWAIT = auto()
    TEST = auto()
    ASSERT = auto()
    EXPORT = auto()

    # Operators
    PLUS = auto()  # +
    MINUS = auto()  # -
    STAR = auto()  # *
    SLASH = auto()  # /
    PERCENT = auto()  # %
    EQ = auto()  # ==
    NEQ = auto()  # !=
    LT = auto()  # <
    GT = auto()  # >
    LTE = auto()  # <=
    GTE = auto()  # >=
    ASSIGN = auto()  # =
    ARROW = auto()  # ->
    PIPE = auto()  # |>
    BAR = auto()  # |
    DOUBLESTAR = auto()  # **
    AMPERSAND = auto()  # &
    CARET = auto()  # ^
    TILDE = auto()  # ~
    LSHIFT = auto()  # <<
    RSHIFT = auto()  # >>

    # Delimiters
    LPAREN = auto()  # (
    RPAREN = auto()  # )
    LBRACKET = auto()  # [
    RBRACKET = auto()  # ]
    LBRACE = auto()  # {
    RBRACE = auto()  # }
    COMMA = auto()  # ,
    COLON = auto()  # :
    SEMICOLON = auto()  # ;
    DOT = auto()  # .
    UNDERSCORE = auto()  # _
    QUESTION = auto()  # ?
    AT = auto()  # @

    # Special
    NEWLINE = auto()
    EOF = auto()
    COMMENT = auto()


# Keyword mapping
KEYWORDS: dict[str, TokenType] = {
    "func": TokenType.FUNC,
    "end": TokenType.END,
    "let": TokenType.LET,
    "var": TokenType.VAR,
    "if": TokenType.IF,
    "then": TokenType.THEN,
    "else": TokenType.ELSE,
    "while": TokenType.WHILE,
    "do": TokenType.DO,
    "for": TokenType.FOR,
    "in": TokenType.IN,
    "match": TokenType.MATCH,
    "with": TokenType.WITH,
    "return": TokenType.RETURN,
    "and": TokenType.AND,
    "or": TokenType.OR,
    "not": TokenType.NOT,
    "true": TokenType.TRUE,
    "false": TokenType.FALSE,
    "type": TokenType.TYPE,
    "requires": TokenType.REQUIRES,
    "ensures": TokenType.ENSURES,
    "example": TokenType.EXAMPLE,
    "ref": TokenType.REF,
    "fn": TokenType.FN,
    "where": TokenType.WHERE,
    "import": TokenType.IMPORT,
    "break": TokenType.BREAK,
    "continue": TokenType.CONTINUE,
    "try": TokenType.TRY,
    "catch": TokenType.CATCH,
    "throw": TokenType.THROW,
    "trait": TokenType.TRAIT,
    "impl": TokenType.IMPL,
    "async": TokenType.ASYNC,
    "await": TokenType.AWAIT,
    "test": TokenType.TEST,
    "assert": TokenType.ASSERT,
    "export": TokenType.EXPORT,
}


class SourceLocation:
    """Location in source code for error reporting."""

    __slots__ = ("column", "filename", "line")

    def __init__(
        self,
        *args: Any,
        line: int | None = None,
        column: int | None = None,
        filename: str = "<unknown>",
    ):
        if args:
            if len(args) == 1:
                if line is not None:
                    raise TypeError(
                        "__init__() got multiple values for argument 'line'"
                    )
                line = args[0]
            elif len(args) == 2:
                if line is not None:
                    raise TypeError(
                        "__init__() got multiple values for argument 'line'"
                    )
                if column is not None:
                    raise TypeError(
                        "__init__() got multiple values for argument 'column'"
                    )
                line, column = args
            elif len(args) == 3:
                first, second, third = args
                if isinstance(first, str) and not isinstance(second, str):
                    filename, line, column = first, second, third
                else:
                    line, column, filename = first, second, third
            else:
                raise TypeError(
                    "SourceLocation expects (line, column[, filename]) "
                    "or legacy (filename, line, column)"
                )

        if line is None or column is None:
            raise TypeError("SourceLocation requires line and column")

        self.line = line
        self.column = column
        self.filename = filename

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self.__slots__ and hasattr(self, name):
            raise FrozenInstanceError(f"cannot assign to field '{name}'")
        object.__setattr__(self, name, value)

    def __str__(self) -> str:
        return f"{self.filename}:{self.line}:{self.column}"

    def __repr__(self) -> str:
        return (
            "SourceLocation("
            f"line={self.line!r}, column={self.column!r}, filename={self.filename!r}"
            ")"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SourceLocation):
            return NotImplemented
        return (
            self.line == other.line
            and self.column == other.column
            and self.filename == other.filename
        )

    def __hash__(self) -> int:
        return hash((self.line, self.column, self.filename))


class Token:
    """
    A lexical token from Geno source code.

    Attributes:
        type: The type of token
        value: The literal value (for literals) or lexeme
        location: Source location for error reporting
    """

    __slots__ = ("location", "type", "value")

    def __init__(self, type: TokenType, value: Any, location: SourceLocation):
        self.type = type
        self.value = value
        self.location = location

    def __repr__(self) -> str:
        if self.value is not None:
            return f"Token({self.type.name}, {self.value!r}, {self.location})"
        return f"Token({self.type.name}, {self.location})"

    def __str__(self) -> str:
        if self.type in (
            TokenType.INTEGER,
            TokenType.FLOAT,
            TokenType.STRING,
            TokenType.FSTRING,
            TokenType.IDENTIFIER,
            TokenType.TYPE_IDENTIFIER,
        ):
            return f"{self.type.name}({self.value})"
        return self.type.name

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Token):
            return NotImplemented
        return (
            self.type == other.type
            and self.value == other.value
            and self.location == other.location
        )

    def __hash__(self) -> int:
        return hash((self.type, self.value, self.location))


def token_type_to_str(tt: TokenType) -> str:
    """Convert token type to human-readable string for error messages."""
    names = {
        TokenType.INTEGER: "integer",
        TokenType.FLOAT: "float",
        TokenType.STRING: "string",
        TokenType.FSTRING: "f-string",
        TokenType.TRUE: "'true'",
        TokenType.FALSE: "'false'",
        TokenType.IDENTIFIER: "identifier",
        TokenType.TYPE_IDENTIFIER: "type name",
        TokenType.FUNC: "'func'",
        TokenType.END: "'end'",
        TokenType.LET: "'let'",
        TokenType.VAR: "'var'",
        TokenType.IF: "'if'",
        TokenType.THEN: "'then'",
        TokenType.ELSE: "'else'",
        TokenType.WHILE: "'while'",
        TokenType.DO: "'do'",
        TokenType.FOR: "'for'",
        TokenType.IN: "'in'",
        TokenType.MATCH: "'match'",
        TokenType.WITH: "'with'",
        TokenType.RETURN: "'return'",
        TokenType.AND: "'and'",
        TokenType.OR: "'or'",
        TokenType.NOT: "'not'",
        TokenType.TYPE: "'type'",
        TokenType.REQUIRES: "'requires'",
        TokenType.ENSURES: "'ensures'",
        TokenType.EXAMPLE: "'example'",
        TokenType.REF: "'ref'",
        TokenType.FN: "'fn'",
        TokenType.WHERE: "'where'",
        TokenType.IMPORT: "'import'",
        TokenType.BREAK: "'break'",
        TokenType.CONTINUE: "'continue'",
        TokenType.TRY: "'try'",
        TokenType.CATCH: "'catch'",
        TokenType.THROW: "'throw'",
        TokenType.TRAIT: "'trait'",
        TokenType.IMPL: "'impl'",
        TokenType.ASYNC: "'async'",
        TokenType.AWAIT: "'await'",
        TokenType.TEST: "'test'",
        TokenType.ASSERT: "'assert'",
        TokenType.EXPORT: "'export'",
        TokenType.PLUS: "'+'",
        TokenType.MINUS: "'-'",
        TokenType.STAR: "'*'",
        TokenType.SLASH: "'/'",
        TokenType.PERCENT: "'%'",
        TokenType.EQ: "'=='",
        TokenType.NEQ: "'!='",
        TokenType.LT: "'<'",
        TokenType.GT: "'>'",
        TokenType.LTE: "'<='",
        TokenType.GTE: "'>='",
        TokenType.ASSIGN: "'='",
        TokenType.ARROW: "'->'",
        TokenType.PIPE: "'|>'",
        TokenType.BAR: "'|'",
        TokenType.DOUBLESTAR: "'**'",
        TokenType.AMPERSAND: "'&'",
        TokenType.CARET: "'^'",
        TokenType.TILDE: "'~'",
        TokenType.LSHIFT: "'<<'",
        TokenType.RSHIFT: "'>>'",
        TokenType.LPAREN: "'('",
        TokenType.RPAREN: "')'",
        TokenType.LBRACKET: "'['",
        TokenType.RBRACKET: "']'",
        TokenType.LBRACE: "'{'",
        TokenType.RBRACE: "'}'",
        TokenType.COMMA: "','",
        TokenType.COLON: "':'",
        TokenType.SEMICOLON: "';'",
        TokenType.DOT: "'.'",
        TokenType.UNDERSCORE: "'_'",
        TokenType.QUESTION: "'?'",
        TokenType.AT: "'@'",
        TokenType.NEWLINE: "newline",
        TokenType.EOF: "end of file",
        TokenType.COMMENT: "comment",
    }
    return names.get(tt, tt.name)
