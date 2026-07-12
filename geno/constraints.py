"""
Geno Constraints Module
=======================

Provides constrained decoding support for LLM code generation.
Given a partial program prefix, returns the set of valid next tokens.

This enables:
- Streaming LLM generation with grammar constraints
- Early rejection of invalid token sequences
- Guided decoding for higher quality code generation

Ported from Stitch's constraint system, adapted for Geno's syntax.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import List, Optional, Set, Tuple, Union

from .lexer import Lexer, LexerError
from .tokens import KEYWORDS as LEXER_KEYWORDS
from .tokens import Token, TokenType

logger = logging.getLogger(__name__)

# =============================================================================
# Block Types - Keywords that start blocks requiring 'end X'
# =============================================================================

BLOCK_STARTERS = {
    "func": "func",
    "if": "if",
    "while": "while",
    "for": "for",
    "match": "match",
    "trait": "trait",
    "impl": "impl",
    "test": "test",
    "try": "try",
}

# All keyword token types (Geno uses individual token types for each keyword).
KEYWORD_TOKEN_TYPES = set(LEXER_KEYWORDS.values())

# Mapping from token type to keyword string
TOKEN_TYPE_TO_KEYWORD = {
    token_type: keyword for keyword, token_type in LEXER_KEYWORDS.items()
}


def is_keyword_token(token: Token) -> bool:
    """Check if a token is a keyword token."""
    return token.type in KEYWORD_TOKEN_TYPES


def get_keyword_value(token: Token) -> str | None:
    """Get the keyword string value for a keyword token."""
    return TOKEN_TYPE_TO_KEYWORD.get(token.type)


# Keywords that can appear in various contexts
KEYWORDS = set(LEXER_KEYWORDS)

# Operators and punctuation
OPERATORS = {
    "+",
    "-",
    "*",
    "/",
    "%",
    "==",
    "!=",
    "<",
    ">",
    "<=",
    ">=",
    "->",
    "|>",
    "|",
    "=",
    ":",
    ",",
    "(",
    ")",
    "[",
    "]",
    "_",
}


# =============================================================================
# Constraint State
# =============================================================================


@dataclass
class ConstraintState:
    """
    Tracks the parser state for constraint computation.

    This is a simplified model of the parser state that tracks:
    - Open blocks that need closing
    - What the parser is currently expecting
    """

    # Stack of open blocks (e.g., ["func", "if"])
    open_blocks: List[str] = field(default_factory=list)

    # Current parsing context
    expecting_func_name: bool = False
    expecting_type_annotation: bool = False
    expecting_return_type: bool = False
    expecting_block_body: bool = False
    in_parameter_list: bool = False
    paren_depth: int = 0
    bracket_depth: int = 0

    # For match expressions
    in_match: bool = False
    expecting_match_arm: bool = False

    # For import statements
    expecting_module_name: bool = False

    # For impl headers: `impl Trait for Type` uses `for` as header syntax, not a
    # loop starter.
    in_impl_header: bool = False


@dataclass(frozen=True)
class ConstraintViolation:
    """Represents a constraint violation during parsing."""

    message: str
    token_text: str


@dataclass
class AllowedNext:
    """
    Describes what tokens are allowed next in the grammar.

    This is used for constrained decoding - the LLM can only generate
    tokens that are in this set.
    """

    # Specific keywords that are allowed
    keywords: List[str] = field(default_factory=list)

    # Whether identifiers (variable/function names) are allowed
    allow_identifier: bool = False

    # Whether type identifiers (PascalCase names) are allowed
    allow_type_identifier: bool = False

    # Whether literals are allowed
    allow_int: bool = False
    allow_float: bool = False
    allow_string: bool = False
    allow_bool: bool = False

    # Allowed operators/punctuation
    allow_punct: List[str] = field(default_factory=list)

    # Stack of blocks that need to be closed (for reference)
    expected_end_stack: List[str] = field(default_factory=list)

    def __repr__(self) -> str:
        parts = []
        if self.keywords:
            parts.append(f"keywords={self.keywords}")
        if self.allow_identifier:
            parts.append("IDENT")
        if self.allow_type_identifier:
            parts.append("TYPE_IDENT")
        if self.allow_int:
            parts.append("INT")
        if self.allow_float:
            parts.append("FLOAT")
        if self.allow_string:
            parts.append("STRING")
        if self.allow_bool:
            parts.append("BOOL")
        if self.allow_punct:
            parts.append(f"punct={self.allow_punct}")
        if self.expected_end_stack:
            parts.append(f"need_to_close={self.expected_end_stack}")
        return f"AllowedNext({', '.join(parts)})"


# =============================================================================
# Constraint State Machine
# =============================================================================


def start_state() -> ConstraintState:
    """Create initial constraint state."""
    return ConstraintState()


def step(
    state: ConstraintState, token: Token
) -> Union[ConstraintState, ConstraintViolation]:
    """
    Advance the constraint state by one token.

    Returns either an updated state or a constraint violation.
    """
    keyword_value = get_keyword_value(token)

    # Handle end keyword specially
    if token.type == TokenType.END:
        if not state.open_blocks:
            return ConstraintViolation("unexpected 'end' with no open block", "end")
        # Next token should be the block type being closed
        return state  # Will check in next step

    # Track parentheses depth
    if token.type == TokenType.LPAREN:
        state.paren_depth += 1
        if state.expecting_func_name:
            state.expecting_func_name = False
            state.in_parameter_list = True
    elif token.type == TokenType.RPAREN:
        state.paren_depth = max(0, state.paren_depth - 1)
        if state.in_parameter_list and state.paren_depth == 0:
            state.in_parameter_list = False
            state.expecting_return_type = True

    # Handle return type: after -> and type, we're in function body
    if token.type == TokenType.ARROW and state.expecting_return_type:
        state.expecting_type_annotation = True
        return state

    # If we see a type identifier while expecting type annotation, we're done with return type
    if token.type == TokenType.TYPE_IDENTIFIER and state.expecting_type_annotation:
        state.expecting_type_annotation = False
        state.expecting_return_type = False
        state.expecting_block_body = True
        return state

    # Track bracket depth
    if token.type == TokenType.LBRACKET:
        state.bracket_depth += 1
    elif token.type == TokenType.RBRACKET:
        state.bracket_depth = max(0, state.bracket_depth - 1)

    if state.in_impl_header and token.type == TokenType.NEWLINE:
        state.in_impl_header = False
        return state

    # Handle import: after 'import', expect module name then done
    if state.expecting_module_name:
        if token.type == TokenType.TYPE_IDENTIFIER:
            state.expecting_module_name = False
            return state

    # Handle block-starting keywords
    if is_keyword_token(token):
        if token.type == TokenType.IMPORT:
            state.expecting_module_name = True
            return state

        if token.type == TokenType.FUNC:
            if state.open_blocks and state.open_blocks[-1] == "trait":
                state.expecting_func_name = True
                return state
            state.open_blocks.append("func")
            state.expecting_func_name = True
            return state

        if state.in_impl_header:
            if token.type == TokenType.FOR:
                return state

        if token.type == TokenType.TYPE:
            return state

        if token.type == TokenType.IF:
            state.open_blocks.append("if")
            return state

        if token.type == TokenType.WHILE:
            state.open_blocks.append("while")
            return state

        if token.type == TokenType.FOR:
            state.open_blocks.append("for")
            return state

        if token.type == TokenType.MATCH:
            state.open_blocks.append("match")
            state.in_match = True
            return state

        if token.type == TokenType.TRAIT:
            state.open_blocks.append("trait")
            return state

        if token.type == TokenType.IMPL:
            state.open_blocks.append("impl")
            state.in_impl_header = True
            return state

        if token.type == TokenType.TEST:
            state.open_blocks.append("test")
            return state

        if token.type == TokenType.TRY:
            state.open_blocks.append("try")
            return state

        # Check if this closes a block (e.g., "end func")
        if keyword_value in BLOCK_STARTERS.values():
            # This might be closing a block
            if state.open_blocks and state.open_blocks[-1] == keyword_value:
                # Check if previous token was "end"
                pass  # This is handled by looking at token sequence

    return state


def step_with_end_check(
    state: ConstraintState, prev_token: Token | None, token: Token
) -> Union[ConstraintState, ConstraintViolation]:
    """
    Step with special handling for 'end X' sequences.
    """
    # If previous token was 'end', this should be the block type
    if prev_token and prev_token.type == TokenType.END:
        keyword_value = get_keyword_value(token)
        if is_keyword_token(token) and keyword_value in BLOCK_STARTERS.values():
            if state.open_blocks and state.open_blocks[-1] == keyword_value:
                if keyword_value == "match":
                    state.in_match = False
                    state.expecting_match_arm = False
                state.open_blocks.pop()
                return state
            else:
                expected = state.open_blocks[-1] if state.open_blocks else "nothing"
                return ConstraintViolation(
                    f"mismatched end: expected 'end {expected}', got 'end {keyword_value}'",
                    keyword_value or "",
                )

    return step(state, token)


def allowed_next(state: ConstraintState) -> AllowedNext:
    """
    Compute what tokens are allowed next given the current state.
    """
    expected_end_stack = list(reversed(state.open_blocks))

    # If expecting module name after 'import' keyword
    if state.expecting_module_name:
        return AllowedNext(
            allow_type_identifier=True,
            expected_end_stack=expected_end_stack,
        )

    # If expecting function name after 'func' keyword
    if state.expecting_func_name:
        return AllowedNext(
            allow_identifier=True,
            expected_end_stack=expected_end_stack,
        )

    # If in parameter list
    if state.in_parameter_list:
        return AllowedNext(
            keywords=["fn"],  # For function type parameters
            allow_identifier=True,
            allow_punct=[":", ",", ")"],
            expected_end_stack=expected_end_stack,
        )

    # If expecting return type (after parameter list)
    if state.expecting_return_type:
        return AllowedNext(
            allow_punct=["->"],
            expected_end_stack=expected_end_stack,
        )

    # If in a match expression, expecting arm
    if state.in_match and state.expecting_match_arm:
        return AllowedNext(
            allow_punct=["|"],
            allow_identifier=True,  # Pattern
            expected_end_stack=expected_end_stack,
        )

    # Default: most things are allowed
    keywords = list(KEYWORDS)

    # Add 'end' if there are open blocks
    if state.open_blocks:
        if "end" not in keywords:
            keywords.append("end")

    return AllowedNext(
        keywords=sorted(keywords),
        allow_identifier=True,
        allow_int=True,
        allow_float=True,
        allow_string=True,
        allow_bool=True,
        allow_punct=sorted(OPERATORS),
        expected_end_stack=expected_end_stack,
    )


# =============================================================================
# Main API
# =============================================================================


def allowed_next_for_prefix(prefix: str) -> AllowedNext:
    """
    Given a partial program prefix, compute what tokens are allowed next.

    This is the main API for constrained decoding.

    Example:
        >>> allowed = allowed_next_for_prefix("func add(x: Int, y: Int) ->")
        >>> print(allowed)
        AllowedNext(IDENT, keywords=['Int', 'Bool', 'String', ...])

    Args:
        prefix: Partial Geno source code

    Returns:
        AllowedNext describing valid next tokens
    """
    state = start_state()

    try:
        lexer = Lexer(prefix)
        tokens = lexer.tokenize()
    except LexerError:
        logger.debug("Constraint lexing failed for prefix", exc_info=True)
        return AllowedNext(expected_end_stack=[])

    prev_token = None
    for token in tokens:
        if token.type == TokenType.EOF:
            break

        result = step_with_end_check(state, prev_token, token)

        if isinstance(result, ConstraintViolation):
            # Return empty constraints on violation
            return AllowedNext(expected_end_stack=list(reversed(state.open_blocks)))

        state = result
        prev_token = token

    return allowed_next(state)


def validate_prefix(prefix: str) -> Tuple[bool, str | None]:
    """
    Check if a prefix is valid (could be extended to a valid program).

    Returns:
        (is_valid, error_message)
    """
    state = start_state()

    try:
        lexer = Lexer(prefix)
        tokens = lexer.tokenize()
    except LexerError as e:
        return False, f"Lexer error: {e}"

    prev_token = None
    for token in tokens:
        if token.type == TokenType.EOF:
            break

        result = step_with_end_check(state, prev_token, token)

        if isinstance(result, ConstraintViolation):
            return False, result.message

        state = result
        prev_token = token

    return True, None


def get_unclosed_blocks(prefix: str) -> List[str]:
    """
    Get the list of blocks that are still open and need closing.

    Useful for auto-completion and error messages.

    Example:
        >>> get_unclosed_blocks("func foo() -> Int\\n    if x > 0 then")
        ['func', 'if']
    """
    allowed = allowed_next_for_prefix(prefix)
    return allowed.expected_end_stack
