"""A literal must face the integer-bits limit in a Float position too.

`_compile_expected_float_literal` promotes an Int literal that sits in a Float
position.  When the conversion happened to be exact -- a power of two, say --
it emitted a bare Python float constant, dropping the `_check_collection_size`
guard that `_compile_int_literal` applies to large literals.  The same literal
was still rejected in an Int position, so whether the configured
`max_integer_bits` limit applied at all depended on the surrounding type.  The
interpreter checks the bit length before promoting either way.
"""

from __future__ import annotations

import pytest

from geno.compiler import compile_to_python
from geno.interpreter import Interpreter
from geno.lexer import Lexer
from geno.parser import Parser
from geno.sandbox import SandboxConfig

# 2**200: large, and exactly representable as a float, so the conversion is
# lossless and the old code took the unguarded path.
BIG = 2**200


def _program(return_type: str) -> str:
    return f"func main() -> {return_type}\n    return {BIG}\nend func\n"


@pytest.mark.parametrize("return_type", ["Int", "Float"])
def test_large_literal_keeps_the_runtime_guard(return_type: str) -> None:
    namespace: dict[str, object] = {}
    exec(compile_to_python(_program(return_type)), namespace)
    namespace["_MAX_INTEGER_BITS"] = 64
    main = namespace["main"]
    with pytest.raises(RuntimeError, match="Integer exceeds maximum size"):
        main()  # type: ignore[operator]


@pytest.mark.parametrize("return_type", ["Int", "Float"])
def test_interpreter_rejects_it_too(return_type: str) -> None:
    """The lane the compiled backends have to agree with."""
    program = Parser(Lexer(_program(return_type)).tokenize()).parse_program()
    interpreter = Interpreter(sandbox_config=SandboxConfig(max_integer_bits=64))
    with pytest.raises(Exception, match="Integer exceeds maximum size"):
        interpreter.run(program)


def test_a_small_literal_in_a_float_position_stays_a_plain_constant() -> None:
    """The guard is for large literals; small ones must not pay for it."""
    emitted = compile_to_python("func main() -> Float\n    return 2\nend func\n")
    body = emitted[emitted.index("def main") :]
    body = body[: body.index("\n\n")]
    assert "2.0" in body, body
    assert "_check_collection_size" not in body, body
