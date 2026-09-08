"""Imported namespace aliases belong to the module defining each closure."""

import pytest

from geno.api import RunConfig, run


def _modules():
    modules = {}
    for name, value in (("Alpha", 1), ("Beta", 2)):
        prefix = name.lower()
        modules[f"{name}Utils"] = f"""
func value() -> Int
    example () -> {value}
    return {value}
end func
"""
        modules[name] = f"""
import {name}Utils as Local

func {prefix}() -> Int
    example () -> Local.value()
    return Local.value()
end func

@untested("callback fixture")
func {prefix}_callback() -> () -> Int
    return Local.value
end func

@untested("closure fixture")
func {prefix}_closure() -> () -> Int
    return fn() -> Local.value()
end func

@untested("nested call fixture")
func {prefix}_nested(callback: () -> Int) -> Int
    return Local.value() + callback() + Local.value()
end func

func {prefix}_default(x: Int = Local.value()) -> Int
    example () -> {value}
    return x
end func
"""
    return modules


@pytest.mark.parametrize(
    "expression, expected",
    [
        ("alpha() * 10 + beta()", 12),
        ("alpha_callback()() * 10 + beta_callback()()", 12),
        ("alpha_closure()() * 10 + beta_closure()()", 12),
        ("alpha_nested(beta)", 4),
        ("alpha_default() * 10 + beta_default()", 12),
    ],
)
def test_module_local_aliases_survive_other_imports(expression, expected):
    source = f"""
import Alpha
import Beta
func main() -> Int
    return {expression}
end func
"""
    result = run(source, RunConfig(modules=_modules()))
    assert result.ok, result.diagnostics
    assert result.value == expected
