"""
Tests for the Geno Type System (geno/types.py)
===============================================

Unit tests for Type subclasses, TypeEnv, TypeDefInfo,
and the GenoTypeError exception class.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from geno.tokens import SourceLocation
from geno.types import (
    AnyType,
    ArrayType,
    AsyncType,
    BoolType,
    FloatType,
    FuncType,
    GenoTypeError,
    IntType,
    ListType,
    MapType,
    ModuleType,
    MutableMapType,
    OptionType,
    ResultType,
    SetType,
    StringType,
    TupleType,
    Type,
    TypeDefInfo,
    TypeEnv,
    TypeVar,
    UnitType,
    UserType,
    VecType,
    any_child,
    map_type,
    type_children,
)

LOC = SourceLocation(1, 1, "<test>")


# ---------------------------------------------------------------------------
# GenoTypeError
# ---------------------------------------------------------------------------


class TestGenoTypeError:
    def test_message_attribute(self):
        err = GenoTypeError("type mismatch", LOC)
        assert err.message == "type mismatch"

    def test_location_attribute(self):
        err = GenoTypeError("msg", LOC)
        assert err.location is LOC

    def test_str_includes_message(self):
        err = GenoTypeError("something wrong", LOC)
        assert "something wrong" in str(err)

    def test_str_includes_location(self):
        err = GenoTypeError("msg", LOC)
        assert "<test>" in str(err)

    def test_error_code_default_none(self):
        err = GenoTypeError("msg", LOC)
        assert err.error_code is None

    def test_error_code_stored(self):
        err = GenoTypeError("msg", LOC, error_code="E001")
        assert err.error_code == "E001"

    def test_is_exception(self):
        err = GenoTypeError("msg", LOC)
        assert isinstance(err, Exception)

    def test_backward_compat_alias(self):
        from geno.types import TypeError as AliasTypeError

        assert AliasTypeError is GenoTypeError


# ---------------------------------------------------------------------------
# Primitive types: __str__ and equality
# ---------------------------------------------------------------------------


class TestPrimitiveTypes:
    def test_int_str(self):
        assert str(IntType()) == "Int"

    def test_float_str(self):
        assert str(FloatType()) == "Float"

    def test_bool_str(self):
        assert str(BoolType()) == "Bool"

    def test_string_str(self):
        assert str(StringType()) == "String"

    def test_unit_str(self):
        assert str(UnitType()) == "Unit"

    def test_any_str(self):
        assert str(AnyType()) == "Any"

    def test_int_equality(self):
        assert IntType() == IntType()

    def test_different_types_not_equal(self):
        assert IntType() != FloatType()

    def test_types_are_hashable(self):
        s = {IntType(), FloatType(), BoolType()}
        assert len(s) == 3

    def test_same_type_hashes_equal(self):
        assert hash(IntType()) == hash(IntType())


# ---------------------------------------------------------------------------
# Compound types: __str__
# ---------------------------------------------------------------------------


class TestListType:
    def test_list_of_int_str(self):
        assert str(ListType(IntType())) == "List[Int]"

    def test_list_of_string_str(self):
        assert str(ListType(StringType())) == "List[String]"

    def test_nested_list_str(self):
        assert str(ListType(ListType(IntType()))) == "List[List[Int]]"

    def test_equality(self):
        assert ListType(IntType()) == ListType(IntType())

    def test_inequality_element_type(self):
        assert ListType(IntType()) != ListType(FloatType())


class TestArrayType:
    def test_str(self):
        assert str(ArrayType(BoolType())) == "Array[Bool]"

    def test_equality(self):
        assert ArrayType(IntType()) == ArrayType(IntType())


class TestOptionType:
    def test_str(self):
        assert str(OptionType(IntType())) == "Option[Int]"

    def test_nested_str(self):
        assert str(OptionType(OptionType(StringType()))) == "Option[Option[String]]"

    def test_equality(self):
        assert OptionType(IntType()) == OptionType(IntType())


class TestResultType:
    def test_str(self):
        assert str(ResultType(IntType(), StringType())) == "Result[Int, String]"

    def test_equality(self):
        t = ResultType(IntType(), StringType())
        assert t == ResultType(IntType(), StringType())

    def test_inequality(self):
        assert ResultType(IntType(), StringType()) != ResultType(
            FloatType(), StringType()
        )


class TestTupleType:
    def test_single_element_str(self):
        assert str(TupleType((IntType(),))) == "(Int)"

    def test_two_element_str(self):
        assert str(TupleType((IntType(), StringType()))) == "(Int, String)"

    def test_empty_tuple_str(self):
        assert str(TupleType(())) == "()"

    def test_equality(self):
        t = TupleType((IntType(), BoolType()))
        assert t == TupleType((IntType(), BoolType()))


class TestFuncType:
    def test_no_params_str(self):
        assert str(FuncType((), IntType())) == "() -> Int"

    def test_single_param_str(self):
        assert str(FuncType((IntType(),), BoolType())) == "(Int) -> Bool"

    def test_multi_param_str(self):
        result = str(FuncType((IntType(), StringType()), FloatType()))
        assert result == "(Int, String) -> Float"

    def test_equality(self):
        t = FuncType((IntType(),), IntType())
        assert t == FuncType((IntType(),), IntType())

    def test_inequality_return_type(self):
        assert FuncType((IntType(),), IntType()) != FuncType((IntType(),), FloatType())


class TestMapType:
    def test_str(self):
        assert str(MapType(StringType(), IntType())) == "Map[String, Int]"

    def test_equality(self):
        assert MapType(StringType(), IntType()) == MapType(StringType(), IntType())


class TestMutableMapType:
    def test_str(self):
        assert str(MutableMapType(StringType(), IntType())) == "MutableMap[String, Int]"

    def test_distinct_from_map(self):
        assert MutableMapType(StringType(), IntType()) != MapType(
            StringType(), IntType()
        )


class TestVecType:
    def test_str(self):
        assert str(VecType(FloatType())) == "Vec[Float]"

    def test_equality(self):
        assert VecType(IntType()) == VecType(IntType())


# ---------------------------------------------------------------------------
# TypeVar
# ---------------------------------------------------------------------------


class TestTypeVar:
    def test_str(self):
        assert str(TypeVar("T")) == "T"

    def test_equality_same_name(self):
        assert TypeVar("T") == TypeVar("T")

    def test_inequality_different_names(self):
        assert TypeVar("T") != TypeVar("U")

    def test_hashable(self):
        s = {TypeVar("T"), TypeVar("U"), TypeVar("T")}
        assert len(s) == 2


# ---------------------------------------------------------------------------
# UserType
# ---------------------------------------------------------------------------


class TestUserType:
    def test_simple_name_str(self):
        assert str(UserType("MyType")) == "MyType"

    def test_with_type_args_str(self):
        result = str(UserType("Tree", (IntType(),)))
        assert result == "Tree[Int]"

    def test_multiple_type_args_str(self):
        result = str(UserType("Pair", (IntType(), StringType())))
        assert result == "Pair[Int, String]"

    def test_no_args_equality(self):
        assert UserType("Foo") == UserType("Foo")

    def test_with_args_equality(self):
        assert UserType("Box", (IntType(),)) == UserType("Box", (IntType(),))

    def test_different_name_inequality(self):
        assert UserType("Foo") != UserType("Bar")


# ---------------------------------------------------------------------------
# TypeEnv
# ---------------------------------------------------------------------------


class TestTypeEnv:
    def test_empty_lookup_returns_none(self):
        env = TypeEnv()
        assert env.lookup("x") is None

    def test_bind_and_lookup(self):
        env = TypeEnv()
        env.bind("x", IntType())
        assert env.lookup("x") == IntType()

    def test_bind_overwrites(self):
        env = TypeEnv()
        env.bind("x", IntType())
        env.bind("x", FloatType())
        assert env.lookup("x") == FloatType()

    def test_child_inherits_parent_bindings(self):
        parent = TypeEnv()
        parent.bind("x", IntType())
        child = parent.child()
        assert child.lookup("x") == IntType()

    def test_child_binding_does_not_affect_parent(self):
        parent = TypeEnv()
        child = parent.child()
        child.bind("y", StringType())
        assert parent.lookup("y") is None

    def test_child_shadows_parent(self):
        parent = TypeEnv()
        parent.bind("x", IntType())
        child = parent.child()
        child.bind("x", FloatType())
        assert child.lookup("x") == FloatType()
        assert parent.lookup("x") == IntType()

    def test_mutable_variable(self):
        env = TypeEnv()
        env.bind("counter", IntType(), mutable=True)
        assert env.is_mutable("counter") is True

    def test_immutable_variable(self):
        env = TypeEnv()
        env.bind("val", IntType(), mutable=False)
        assert env.is_mutable("val") is False

    def test_mutability_inherited_from_parent(self):
        parent = TypeEnv()
        parent.bind("x", IntType(), mutable=True)
        child = parent.child()
        assert child.is_mutable("x") is True

    def test_immutable_shadow_blocks_parent_mutability(self):
        """Inner `let` shadow must hide outer `var` mutability (#656, F-0001)."""
        parent = TypeEnv()
        parent.bind("x", IntType(), mutable=True)
        child = parent.child()
        child.bind("x", IntType(), mutable=False)
        assert child.is_mutable("x") is False

    def test_mutable_shadow_hides_parent_immutability(self):
        """Inner `var` shadow overrides outer immutability."""
        parent = TypeEnv()
        parent.bind("x", IntType(), mutable=False)
        child = parent.child()
        child.bind("x", IntType(), mutable=True)
        assert child.is_mutable("x") is True

    def test_is_mutable_unknown_returns_false(self):
        env = TypeEnv()
        assert env.is_mutable("nonexistent") is False

    def test_multiple_bindings(self):
        env = TypeEnv()
        env.bind("a", IntType())
        env.bind("b", StringType())
        env.bind("c", BoolType())
        assert env.lookup("a") == IntType()
        assert env.lookup("b") == StringType()
        assert env.lookup("c") == BoolType()

    def test_deep_chain_lookup(self):
        env = TypeEnv()
        env.bind("root", UnitType())
        child = env.child()
        grandchild = child.child()
        assert grandchild.lookup("root") == UnitType()


# ---------------------------------------------------------------------------
# TypeDefInfo
# ---------------------------------------------------------------------------


class TestTypeDefInfo:
    def test_construction(self):
        info = TypeDefInfo(
            name="Option",
            type_params=["T"],
            variants={"Some": [("value", TypeVar("T"))], "None": []},
        )
        assert info.name == "Option"
        assert info.type_params == ["T"]
        assert "Some" in info.variants
        assert "None" in info.variants

    def test_empty_variants(self):
        info = TypeDefInfo(name="Void", type_params=[], variants={})
        assert info.variants == {}

    def test_multiple_type_params(self):
        info = TypeDefInfo(
            name="Either",
            type_params=["L", "R"],
            variants={
                "Left": [("value", TypeVar("L"))],
                "Right": [("value", TypeVar("R"))],
            },
        )
        assert len(info.type_params) == 2


# ---------------------------------------------------------------------------
# Hashability / use in sets and dicts (frozen dataclasses)
# ---------------------------------------------------------------------------


class TestTypeHashability:
    def test_types_usable_as_dict_keys(self):
        d = {IntType(): "int", StringType(): "str"}
        assert d[IntType()] == "int"

    def test_compound_types_hashable(self):
        s = {
            ListType(IntType()),
            OptionType(StringType()),
            FuncType((IntType(),), BoolType()),
        }
        assert len(s) == 3

    def test_nested_compound_types_equal_hash(self):
        t1 = ListType(OptionType(IntType()))
        t2 = ListType(OptionType(IntType()))
        assert hash(t1) == hash(t2)
        assert t1 == t2


class TestTypeChildren:
    """Tests for the structural fold infrastructure (type_children, map_type, any_child)."""

    def test_leaf_types_return_none(self):
        for leaf in [
            IntType(),
            FloatType(),
            BoolType(),
            StringType(),
            UnitType(),
            AnyType(),
            TypeVar("T"),
            ModuleType("foo"),
            UserType("Color"),
        ]:
            assert type_children(leaf) is None

    def test_single_child_types(self):
        cases = [
            (ListType(IntType()), (IntType(),)),
            (ArrayType(IntType()), (IntType(),)),
            (SetType(IntType()), (IntType(),)),
            (VecType(IntType()), (IntType(),)),
            (OptionType(IntType()), (IntType(),)),
            (AsyncType(IntType()), (IntType(),)),
        ]
        for t, expected_children in cases:
            children, rebuild = type_children(t)
            assert children == expected_children
            assert rebuild(children) == t

    def test_dual_child_types(self):
        cases = [
            ResultType(IntType(), StringType()),
            MapType(StringType(), IntType()),
            MutableMapType(StringType(), IntType()),
        ]
        for t in cases:
            children, rebuild = type_children(t)
            assert len(children) == 2
            assert rebuild(children) == t

    def test_tuple_type(self):
        t = TupleType((IntType(), StringType(), BoolType()))
        children, rebuild = type_children(t)
        assert children == (IntType(), StringType(), BoolType())
        assert rebuild(children) == t

    def test_func_type(self):
        t = FuncType((IntType(), StringType()), BoolType(), frozenset({"io"}))
        children, rebuild = type_children(t)
        assert children == (IntType(), StringType(), BoolType())
        rebuilt = rebuild(children)
        assert rebuilt == t
        assert isinstance(rebuilt, FuncType)
        assert rebuilt.effects == frozenset({"io"})

    def test_user_type_with_args(self):
        t = UserType("Pair", (IntType(), StringType()))
        children, rebuild = type_children(t)
        assert children == (IntType(), StringType())
        rebuilt = rebuild(children)
        assert rebuilt == t
        assert isinstance(rebuilt, UserType)
        assert rebuilt.name == "Pair"

    def test_rebuild_with_new_children(self):
        t = ListType(IntType())
        _children, rebuild = type_children(t)
        new_t = rebuild((FloatType(),))
        assert new_t == ListType(FloatType())


class TestMapType:
    def test_identity_on_leaf(self):
        t = IntType()
        assert map_type(t, lambda c: c) is t

    def test_transform_list_element(self):
        t = ListType(IntType())
        result = map_type(t, lambda c: FloatType() if isinstance(c, IntType) else c)
        assert result == ListType(FloatType())

    def test_transform_func_params(self):
        t = FuncType((IntType(), IntType()), IntType())
        result = map_type(t, lambda c: StringType())
        assert result == FuncType((StringType(), StringType()), StringType())

    def test_preserves_non_type_fields(self):
        t = FuncType((IntType(),), BoolType(), frozenset({"io"}))
        result = map_type(t, lambda c: c)
        assert result.effects == frozenset({"io"})

    def test_preserves_user_type_name(self):
        t = UserType("Box", (IntType(),))
        result = map_type(t, lambda c: FloatType())
        assert result == UserType("Box", (FloatType(),))


class TestAnyChild:
    def test_false_on_leaf(self):
        assert not any_child(IntType(), lambda c: True)

    def test_detects_nested_typevar(self):
        t = ListType(TypeVar("T"))
        assert any_child(t, lambda c: isinstance(c, TypeVar))

    def test_false_when_no_match(self):
        t = ListType(IntType())
        assert not any_child(t, lambda c: isinstance(c, TypeVar))

    def test_checks_all_children(self):
        t = ResultType(IntType(), TypeVar("E"))
        assert any_child(t, lambda c: isinstance(c, TypeVar))

    def test_does_not_recurse_deeper(self):
        t = ListType(OptionType(TypeVar("T")))
        assert not any_child(t, lambda c: isinstance(c, TypeVar))
