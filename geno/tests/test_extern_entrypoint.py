"""Target checking includes the host's implicit call of main()."""

import pytest

from geno import api
from geno.types import FloatType, FuncType, IntType, TypeVar, type_children


@pytest.mark.parametrize(
    "target", ["python-cli", "python-hosted", "browser", "node-cli"]
)
def test_target_checks_implicit_extern_main(target):
    source = 'extern func main() -> Int with ffi\n    js "Date.now"\nend func\n'
    result = api.check(source, target=target)
    if target == "node-cli":
        assert result.ok
    else:
        assert any(
            "Extern function 'main' is not available" in diagnostic.message
            for diagnostic in result.diagnostics
        )


def test_browser_rejects_implicit_calls_to_extern_lifecycle_functions():
    source = """
extern func init() -> Int with ffi
    js "Date.now"
end func
extern func update(model: Int, dt: Float) -> Int with ffi
    js "Math.max"
end func
extern func render(model: Int) -> Unit with ffi
    js "console.log"
end func
"""
    result = api.check(source, target="browser")
    messages = [diagnostic.message for diagnostic in result.diagnostics]
    for name in ("init", "update", "render"):
        assert any(
            f"Extern function '{name}' is not available" in msg for msg in messages
        )


def test_rebuild_preserves_target_availability():
    unavailable = "no binding for this target"
    fn = FuncType((TypeVar("T"),), IntType(), unavailable=unavailable)
    parts = type_children(fn)
    assert parts is not None
    _, rebuild = parts
    rebuilt = rebuild((FloatType(), IntType()))
    assert isinstance(rebuilt, FuncType)
    assert rebuilt.unavailable == unavailable
