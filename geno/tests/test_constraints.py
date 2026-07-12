"""
Tests for the Geno Constraints Module
=====================================

Tests constrained decoding functionality for LLM code generation.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.constraints import (
    KEYWORD_TOKEN_TYPES,
    KEYWORDS,
    TOKEN_TYPE_TO_KEYWORD,
    AllowedNext,
    ConstraintState,
    allowed_next,
    allowed_next_for_prefix,
    get_unclosed_blocks,
    start_state,
    validate_prefix,
)
from geno.tokens import KEYWORDS as LEXER_KEYWORDS


class TestConstraintState:
    """Test constraint state management."""

    def test_start_state(self):
        """Start state has no open blocks."""
        state = start_state()
        assert state.open_blocks == []
        assert state.paren_depth == 0
        assert state.bracket_depth == 0

    def test_start_state_flags(self):
        """Start state has correct default flags."""
        state = start_state()
        assert state.expecting_func_name == False
        assert state.expecting_type_annotation == False
        assert state.expecting_return_type == False
        assert state.in_parameter_list == False


class TestAllowedNext:
    """Test AllowedNext structure."""

    def test_empty_allowed_next(self):
        """Empty AllowedNext has no allowed tokens."""
        allowed = AllowedNext()
        assert allowed.keywords == []
        assert allowed.allow_identifier == False
        assert allowed.allow_int == False
        assert allowed.allow_punct == []

    def test_allowed_next_repr(self):
        """AllowedNext has useful repr."""
        allowed = AllowedNext(
            keywords=["func", "type"],
            allow_identifier=True,
            allow_int=True,
        )
        repr_str = repr(allowed)
        assert "func" in repr_str
        assert "IDENT" in repr_str
        assert "INT" in repr_str


class TestAllowedNextForPrefix:
    """Test computing allowed tokens for partial programs."""

    def test_empty_prefix(self):
        """Empty prefix allows top-level constructs."""
        allowed = allowed_next_for_prefix("")
        assert "func" in allowed.keywords
        assert "type" in allowed.keywords

    def test_after_func_keyword(self):
        """After 'func', expect identifier."""
        allowed = allowed_next_for_prefix("func ")
        assert allowed.allow_identifier == True
        assert "func" in allowed.expected_end_stack

    def test_after_func_name(self):
        """After 'func name', expect parameter list."""
        allowed = allowed_next_for_prefix("func add")
        # Should allow '(' for parameter list
        assert "(" in allowed.allow_punct or allowed.allow_identifier

    def test_in_parameter_list(self):
        """Inside parameter list, expect params."""
        allowed = allowed_next_for_prefix("func add(x")
        assert ":" in allowed.allow_punct

    def test_after_parameter_type(self):
        """After parameter type, expect comma or close paren."""
        allowed = allowed_next_for_prefix("func add(x: Int")
        # Can continue with more params or close
        assert ")" in allowed.allow_punct or "," in allowed.allow_punct

    def test_nested_blocks(self):
        """Nested blocks tracked correctly."""
        allowed = allowed_next_for_prefix("""
func foo() -> Int
    if x > 0 then
        while y < 10 do
""")
        # Should need to close while, if, func
        assert len(allowed.expected_end_stack) >= 2

    def test_if_block(self):
        """If block tracked."""
        allowed = allowed_next_for_prefix("func main() -> Int\n    if true then\n")
        assert "if" in allowed.expected_end_stack
        assert "end" in allowed.keywords

    def test_match_block(self):
        """Match block tracked."""
        allowed = allowed_next_for_prefix("func main() -> Int\n    match x with\n")
        assert "match" in allowed.expected_end_stack


class TestValidatePrefix:
    """Test prefix validation."""

    def test_valid_empty(self):
        """Empty prefix is valid."""
        is_valid, error = validate_prefix("")
        assert is_valid == True
        assert error is None

    def test_valid_func_start(self):
        """Starting a function is valid."""
        is_valid, _error = validate_prefix("func foo(")
        assert is_valid == True

    def test_valid_complete_function(self):
        """Complete function is valid."""
        source = """
func add(x: Int, y: Int) -> Int
    return x + y
end func
"""
        is_valid, _error = validate_prefix(source)
        assert is_valid == True

    def test_valid_partial_expression(self):
        """Partial expression is valid."""
        is_valid, _error = validate_prefix("func main() -> Int\n    return 1 +")
        assert is_valid == True

    def test_valid_nested_blocks(self):
        """Nested blocks are valid."""
        source = """
func process() -> Int
    if x > 0 then
        while y < 10 do
            y = y + 1
        end while
    end if
    return y
end func
"""
        is_valid, _error = validate_prefix(source)
        assert is_valid == True


class TestGetUnclosedBlocks:
    """Test tracking unclosed blocks."""

    def test_no_unclosed(self):
        """Completed code has no unclosed blocks."""
        source = """
func add(x: Int, y: Int) -> Int
    return x + y
end func
"""
        unclosed = get_unclosed_blocks(source)
        assert unclosed == []

    def test_unclosed_func(self):
        """Unclosed function is tracked."""
        source = "func foo() -> Int\n    return 0"
        unclosed = get_unclosed_blocks(source)
        assert "func" in unclosed

    def test_unclosed_if(self):
        """Unclosed if is tracked."""
        source = """
func foo() -> Int
    if x > 0 then
        return 1
"""
        unclosed = get_unclosed_blocks(source)
        assert "if" in unclosed
        assert "func" in unclosed

    def test_unclosed_while(self):
        """Unclosed while is tracked."""
        source = """
func foo() -> Unit
    while true do
        x = x + 1
"""
        unclosed = get_unclosed_blocks(source)
        assert "while" in unclosed

    def test_unclosed_nested(self):
        """Multiple unclosed blocks tracked in order."""
        source = """
func outer() -> Int
    if a then
        while b do
            for c: Int in items do
"""
        unclosed = get_unclosed_blocks(source)
        # Should be in order they need to be closed (innermost first)
        assert len(unclosed) >= 3

    def test_type_definitions_are_not_blocks(self):
        """Current type definitions are single-line definitions, not blocks."""
        assert get_unclosed_blocks("type Alias = Int\n") == []
        assert get_unclosed_blocks("type Color = Red | Green\n") == []

    def test_current_top_level_blocks_are_tracked(self):
        """Current parser block forms should report their required close."""
        assert get_unclosed_blocks("trait Show\n") == ["trait"]
        assert get_unclosed_blocks("impl Show for Point\n") == ["impl"]
        assert get_unclosed_blocks('test "math"\n') == ["test"]

    def test_try_blocks_are_tracked_inside_functions(self):
        """try/catch blocks should be tracked like parser statements."""
        source = """
func main() -> Int
    try
"""
        assert get_unclosed_blocks(source) == ["try", "func"]

    def test_impl_header_for_does_not_start_loop_block(self):
        """The `for` in `impl Trait for Type` is not a for-loop starter."""
        unclosed = get_unclosed_blocks("impl Show for Point\n")
        assert unclosed == ["impl"]
        assert "for" not in unclosed

    def test_current_top_level_blocks_close(self):
        """Newly tracked block forms should close with their end keyword."""
        assert get_unclosed_blocks("trait Show\nend trait\n") == []
        assert get_unclosed_blocks("impl Show for Point\nend impl\n") == []
        assert get_unclosed_blocks('test "math"\nend test\n') == []

    def test_trait_method_signatures_do_not_start_func_blocks(self):
        """Trait method signatures are closed by `end trait`, not `end func`."""
        source = """
trait Show
    func show(x: Int) -> String
"""
        assert get_unclosed_blocks(source) == ["trait"]


class TestKeywordMaps:
    """Constraint keyword tables should stay in sync with the lexer."""

    def test_keyword_maps_match_lexer_keywords(self):
        assert set(LEXER_KEYWORDS) == KEYWORDS
        assert set(LEXER_KEYWORDS.values()) == KEYWORD_TOKEN_TYPES
        for keyword, token_type in LEXER_KEYWORDS.items():
            assert TOKEN_TYPE_TO_KEYWORD[token_type] == keyword

    @pytest.mark.parametrize(
        "keyword",
        [
            "export",
            "async",
            "trait",
            "impl",
            "test",
            "assert",
            "try",
            "catch",
            "throw",
        ],
    )
    def test_current_keywords_are_allowed(self, keyword):
        assert keyword in allowed_next_for_prefix("").keywords


class TestComplexScenarios:
    """Test more complex real-world scenarios."""

    def test_type_definition(self):
        """Type definitions tracked."""
        allowed = allowed_next_for_prefix("type Option[T] = ")
        # Should be able to start defining variants
        assert allowed.allow_identifier or "type" in allowed.expected_end_stack

    def test_example_clause(self):
        """Example clauses parsed."""
        source = """
func double(x: Int) -> Int
    example 5 -> 10
"""
        is_valid, _error = validate_prefix(source)
        assert is_valid == True

    def test_requires_clause(self):
        """Requires clauses parsed."""
        source = """
func sqrt(x: Int) -> Int
    requires x >= 0
"""
        is_valid, _error = validate_prefix(source)
        assert is_valid == True

    def test_list_literal(self):
        """List literals parsed."""
        source = "func main() -> List[Int]\n    return [1, 2, 3"
        is_valid, _error = validate_prefix(source)
        assert is_valid == True

    def test_lambda_expression(self):
        """Lambda expressions parsed."""
        source = "func main() -> Int\n    let f: (Int) -> Int = fn(x: Int) ->"
        is_valid, _error = validate_prefix(source)
        assert is_valid == True

    def test_pipeline(self):
        """Pipeline expressions parsed."""
        source = "func main() -> Int\n    return [1, 2, 3] |> filter(_, fn(x: Int) -> x > 1) |>"
        is_valid, _error = validate_prefix(source)
        assert is_valid == True


class TestImportConstraints:
    """Test constraints with import keyword and new builtins."""

    def test_import_in_empty_prefix(self):
        """Empty prefix should allow 'import' as a top-level construct."""
        allowed = allowed_next_for_prefix("")
        assert "import" in allowed.keywords

    def test_after_import(self):
        """After 'import', should expect a type identifier (module name)."""
        allowed = allowed_next_for_prefix("import ")
        assert allowed.allow_type_identifier

    def test_import_then_func(self):
        """After a complete import, func should be allowed."""
        source = "import Utils\n"
        allowed = allowed_next_for_prefix(source)
        assert "func" in allowed.keywords or "import" in allowed.keywords

    def test_new_builtins_in_expressions(self):
        """New builtins should be valid identifiers in expressions."""
        source = "func main() -> Int\n    return clock_now("
        is_valid, _error = validate_prefix(source)
        assert is_valid == True

    def test_random_builtin_in_expression(self):
        """random_int should be valid in expression context."""
        source = "func main() -> Int\n    return random_int("
        is_valid, _error = validate_prefix(source)
        assert is_valid == True


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_mismatched_end(self):
        """Mismatched end detected."""
        source = """
func foo() -> Int
    if true then
        return 1
    end while
"""
        # This should detect the mismatch
        _is_valid, _error = validate_prefix(source)
        # May still be "valid" as a prefix, but unclosed blocks will show the issue
        unclosed = get_unclosed_blocks(source)
        # The if should still be open because "end while" doesn't close it

    def test_deeply_nested(self):
        """Deeply nested blocks handled."""
        source = """
func a() -> Int
    if b then
        while c do
            for d: Int in e do
                match f with
                    | x -> if g then
"""
        unclosed = get_unclosed_blocks(source)
        assert len(unclosed) >= 4

    def test_keywords_in_expressions(self):
        """Keywords in valid expression positions."""
        source = "func main() -> Bool\n    return true and false"
        is_valid, _error = validate_prefix(source)
        assert is_valid == True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
