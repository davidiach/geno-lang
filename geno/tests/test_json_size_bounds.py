"""Property tests for the size-bounded JSON encoders.

Both Python runtimes bound a value's JSON size before encoding it, use plain
``json.dumps`` when the bound proves the output fits, and otherwise stream the
encoding against the limit. Whichever path runs, the output must equal
``json.dumps`` and the limit must be enforced to the exact character.
"""

import json

import pytest

hypothesis = pytest.importorskip("hypothesis")
st = pytest.importorskip("hypothesis.strategies")

from geno import _runtime_support as rs  # noqa: E402
from geno import builtins as _builtins  # noqa: E402

# Escapes, an astral character and a line separator stress the string bounds.
_TEXT = st.text(alphabet='a"\\\n\x01é\U0001f600 ', max_size=6)
_JSON = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(min_value=-(10**40), max_value=10**40)
    | st.floats(allow_nan=False, allow_infinity=False)
    | _TEXT,
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(_TEXT, children, max_size=4)
    ),
    max_leaves=20,
)
_FORMATS = st.sampled_from([None, 1, 4, 10])


def _expected(obj, indent, ensure_ascii):
    separators = (",", ":") if indent is None else (",", ": ")
    return json.dumps(
        obj, indent=indent, separators=separators, ensure_ascii=ensure_ascii
    )


def _interpreter_dumps(obj, limit, indent, ensure_ascii):
    return _builtins._json_dumps_under_limit(
        "f",
        obj,
        _builtins._InterpreterCap(limit),
        indent=indent,
        ensure_ascii=ensure_ascii,
    )


def _compiled_dumps(obj, limit, indent, ensure_ascii):
    saved = rs._MAX_COLLECTION_SIZE
    rs._MAX_COLLECTION_SIZE = limit
    try:
        return rs._json_dumps_under_limit(
            "f", obj, indent=indent, ensure_ascii=ensure_ascii
        )
    finally:
        rs._MAX_COLLECTION_SIZE = saved


@pytest.mark.parametrize("dumps", [_interpreter_dumps, _compiled_dumps])
@hypothesis.given(obj=_JSON, indent=_FORMATS, ensure_ascii=st.booleans())
@hypothesis.settings(max_examples=200, deadline=None)
def test_bounded_json_matches_json_dumps_at_the_exact_limit(
    dumps, obj, indent, ensure_ascii
):
    # Repeating the value also covers subtrees shared by reference.
    for value in (obj, [obj, obj, [obj]]):
        expected = _expected(value, indent, ensure_ascii)
        assert dumps(value, len(expected), indent, ensure_ascii) == expected
        with pytest.raises(Exception, match="String size exceeds limit"):
            dumps(value, len(expected) - 1, indent, ensure_ascii)
