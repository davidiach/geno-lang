"""
Tests for Geno Runtime Values
================================

Direct unit tests for geno/values.py.
"""

from geno.tokens import SourceLocation
from geno.values import (
    BuiltinFunction,
    Closure,
    ConstructorValue,
    Environment,
    ReturnException,
    RuntimeError,
)


class TestConstructorValue:
    """Tests for ConstructorValue."""

    def test_repr_no_fields(self):
        val = ConstructorValue("None", {})
        assert repr(val) == "None"

    def test_repr_with_fields(self):
        val = ConstructorValue("Some", {"value": 42})
        result = repr(val)
        assert "Some" in result
        assert "value" in result
        assert "42" in result

    def test_equality(self):
        a = ConstructorValue("Some", {"value": 1})
        b = ConstructorValue("Some", {"value": 1})
        assert a == b

    def test_inequality_different_constructor(self):
        a = ConstructorValue("Some", {"value": 1})
        b = ConstructorValue("None", {})
        assert a != b

    def test_inequality_different_fields(self):
        a = ConstructorValue("Some", {"value": 1})
        b = ConstructorValue("Some", {"value": 2})
        assert a != b

    def test_inequality_non_constructor(self):
        val = ConstructorValue("Some", {"value": 1})
        assert val != 42
        assert val != "Some"

    def test_hash(self):
        a = ConstructorValue("Some", {"value": 1})
        b = ConstructorValue("Some", {"value": 1})
        assert hash(a) == hash(b)
        # Can be used as dict key
        d = {a: "found"}
        assert d[b] == "found"


class TestEnvironment:
    """Tests for Environment."""

    def test_bind_and_lookup(self):
        env = Environment()
        env.bind("x", 42)
        assert env.lookup("x") == 42

    def test_lookup_missing(self):
        from geno.values import _UNBOUND

        env = Environment()
        assert env.lookup("x") is _UNBOUND

    def test_child_scoping(self):
        parent = Environment()
        parent.bind("x", 1)
        child = parent.child()
        child.bind("y", 2)
        assert child.lookup("x") == 1
        assert child.lookup("y") == 2
        from geno.values import _UNBOUND

        assert parent.lookup("y") is _UNBOUND

    def test_mutable_assign(self):
        env = Environment()
        env.bind("x", 1, mutable=True)
        result = env.assign("x", 2)
        assert result is True
        assert env.lookup("x") == 2

    def test_immutable_reject(self):
        env = Environment()
        env.bind("x", 1, mutable=False)
        result = env.assign("x", 2)
        assert result is False
        assert env.lookup("x") == 1

    def test_assign_not_found(self):
        env = Environment()
        result = env.assign("x", 1)
        assert result is False

    def test_assign_in_parent(self):
        parent = Environment()
        parent.bind("x", 1, mutable=True)
        child = parent.child()
        result = child.assign("x", 2)
        assert result is True
        assert parent.lookup("x") == 2


class TestReturnException:
    """Tests for ReturnException."""

    def test_stores_value(self):
        exc = ReturnException(42)
        assert exc.value == 42

    def test_stores_none(self):
        exc = ReturnException(None)
        assert exc.value is None


class TestRuntimeError:
    """Tests for RuntimeError."""

    def test_without_location(self):
        err = RuntimeError("something failed")
        assert err.message == "something failed"
        assert err.location is None
        assert "something failed" in str(err)

    def test_with_location(self):
        loc = SourceLocation(line=5, column=10, filename="test.geno")
        err = RuntimeError("bad value", loc)
        assert err.message == "bad value"
        assert err.location == loc
        assert "bad value" in str(err)


class TestClosure:
    """Tests for Closure."""

    def test_repr_named(self):
        env = Environment()
        closure = Closure(params=[], body=[], env=env, name="my_func")
        assert repr(closure) == "<function my_func>"

    def test_repr_anonymous(self):
        env = Environment()
        closure = Closure(params=[], body=[], env=env)
        assert repr(closure) == "<function anonymous>"


class TestBuiltinFunction:
    """Tests for BuiltinFunction."""

    def test_repr(self):
        bf = BuiltinFunction("length", lambda lst: len(lst), 1, ["list"])
        assert repr(bf) == "<builtin length>"
