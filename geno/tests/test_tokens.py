import pickle
from dataclasses import FrozenInstanceError

from geno.tokens import SourceLocation, Token, TokenType


def test_source_location_supports_standard_positional_order():
    loc = SourceLocation(3, 5, "test.geno")

    assert loc.line == 3
    assert loc.column == 5
    assert loc.filename == "test.geno"
    assert str(loc) == "test.geno:3:5"


def test_source_location_supports_legacy_filename_first_order():
    loc = SourceLocation("<test>", 1, 2)

    assert loc.line == 1
    assert loc.column == 2
    assert loc.filename == "<test>"
    assert str(loc) == "<test>:1:2"


def test_source_location_supports_mixed_positional_and_keyword_args():
    loc = SourceLocation(3, column=5, filename="test.geno")

    assert loc.line == 3
    assert loc.column == 5
    assert loc.filename == "test.geno"


def test_source_location_is_frozen():
    loc = SourceLocation(3, 5, "test.geno")

    try:
        loc.line = 4
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("SourceLocation should be immutable")


def test_token_pickle_round_trip_preserves_fields():
    token = Token(TokenType.INTEGER, 42, SourceLocation(1, 1, "test.geno"))

    roundtrip = pickle.loads(pickle.dumps(token))

    assert roundtrip == token
    assert roundtrip.location == token.location
