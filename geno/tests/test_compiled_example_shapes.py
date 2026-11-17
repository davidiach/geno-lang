"""Compiled JavaScript compares Float examples using the concrete result type."""

import shutil

import pytest

from geno.test_runner import run_test_suite


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not available")
def test_generic_and_recursive_results_keep_float_tolerance(tmp_path):
    path = tmp_path / "values.geno"
    path.write_text(
        """\
func identity[T](v: T) -> T
    example 0.1 + 0.2 -> 0.3
    example 4 -> 4
    return v
end func

type Chain = Link(x: Float, tail: Chain) | Nil
func chain(n: Int) -> Chain
    example 0 -> Link(0.0, Link(0.3, Nil))
    return Link(0.0, Link(0.1 + 0.2, Nil))
end func
""",
        encoding="utf-8",
    )
    suite = run_test_suite([path], backend="js")
    assert (suite.total, suite.passed, suite.errors) == (3, 3, 0), suite.to_dict()
