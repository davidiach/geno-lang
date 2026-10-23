"""Tests for the effect typing system — Phase 3: inference and checking."""

import pytest

from geno.parser import parse
from geno.typechecker import TypeChecker
from geno.types import FuncType


def _typecheck(source: str):
    program = parse(source)
    checker = TypeChecker()
    checker.check_program(program)
    return checker


def _typecheck_errors(source: str) -> list:
    """Run typecheck and return errors (empty list = success)."""
    program = parse(source)
    checker = TypeChecker()
    try:
        checker.check_program(program)
        return []
    except Exception as e:
        return [str(e)]


# ---------------------------------------------------------------------------
# Effect inference from builtin calls
# ---------------------------------------------------------------------------


class TestEffectInference:
    def test_pure_function_inferred(self):
        checker = _typecheck("""
func add(a: Int, b: Int) -> Int
    example (1, 2) -> 3
    return a + b
end func add
""")
        ft = checker.global_env.lookup("add")
        assert isinstance(ft, FuncType)
        assert ft.effects == frozenset()

    def test_io_effect_inferred_from_print(self):
        checker = _typecheck("""
func greet(name: String) -> Unit with io
    example "world" -> ()
    print(name)
    return ()
end func greet
""")
        ft = checker.global_env.lookup("greet")
        assert isinstance(ft, FuncType)
        assert ft.effects == frozenset({"io"})

    def test_mutation_inferred_from_var(self):
        checker = _typecheck("""
func counter(start: Int) -> Int with mutation
    example 0 -> 5
    var x: Int = start
    x = x + 5
    return x
end func counter
""")
        ft = checker.global_env.lookup("counter")
        assert isinstance(ft, FuncType)
        assert ft.effects == frozenset({"mutation"})

    def test_throw_inferred(self):
        checker = _typecheck("""
func fail_fast(x: Int) -> Int with throw
    example 0 -> 0
    if x == 0 then
        throw "error"
    end if
    return x
end func fail_fast
""")
        ft = checker.global_env.lookup("fail_fast")
        assert isinstance(ft, FuncType)
        assert ft.effects == frozenset({"throw"})

    def test_try_catch_masks_throw(self):
        checker = _typecheck("""
func safe_op(x: Int) -> Int
    example 0 -> 0
    try
        if x == 0 then
            throw "error"
        end if
    catch e: String
        return 0
    end try
    return x
end func safe_op
""")
        ft = checker.global_env.lookup("safe_op")
        assert isinstance(ft, FuncType)
        # throw is masked by try/catch
        assert "throw" not in ft.effects

    def test_multiple_effects_inferred(self):
        checker = _typecheck("""
func do_stuff(x: Int) -> Unit with io, mutation
    example 0 -> ()
    var y: Int = x
    y = y + 1
    print(y)
    return ()
end func do_stuff
""")
        ft = checker.global_env.lookup("do_stuff")
        assert isinstance(ft, FuncType)
        assert ft.effects == frozenset({"io", "mutation"})

    def test_effect_propagation_through_calls(self):
        """Calling an effectful function propagates its effects."""
        checker = _typecheck("""
func greet(name: String) -> Unit with io
    example "world" -> ()
    print(name)
    return ()
end func greet

func greet_twice(name: String) -> Unit with io
    example "world" -> ()
    greet(name)
    greet(name)
    return ()
end func greet_twice
""")
        ft = checker.global_env.lookup("greet_twice")
        assert isinstance(ft, FuncType)
        assert ft.effects == frozenset({"io"})

    def test_pipeline_stage_effects_propagate(self):
        """Pipeline calls should contribute the invoked stage function effects."""
        checker = _typecheck("""
func log_int(x: Int) -> Int with io
    example 1 -> 1
    print(x)
    return x
end func log_int

func use_pipeline() -> Int
    example () -> 1
    return 1 |> log_int
end func use_pipeline
""")
        ft = checker.global_env.lookup("use_pipeline")
        assert isinstance(ft, FuncType)
        assert ft.effects == frozenset({"io"})

    def test_declared_effects_reject_missing_pipeline_stage_effect(self):
        """Declared effects must include effects from pipeline stage calls."""
        errors = _typecheck_errors("""
func log_int(x: Int) -> Int with io
    example 1 -> 1
    print(x)
    return x
end func log_int

func use_pipeline() -> Int with fs
    example () -> 1
    return 1 |> log_int
end func use_pipeline
""")
        assert len(errors) > 0
        assert "undeclared effects" in errors[0].lower()
        assert "io" in errors[0]

    def test_inline_lambda_call_effects_propagate(self):
        """Calling an inline lambda contributes its inferred effects."""
        checker = _typecheck("""
func main() -> Unit
    (fn() do
        print("hi")
        return ()
    end fn)()
    return ()
end func
""")
        ft = checker.global_env.lookup("main")
        assert isinstance(ft, FuncType)
        assert ft.effects == frozenset({"io"})

    def test_unannotated_function_gets_inferred_effects(self):
        """Without explicit annotation, effects are inferred and stored."""
        checker = _typecheck("""
@untested("uses print")
func log_value(x: Int) -> Unit
    print(x)
    return ()
end func log_value
""")
        ft = checker.global_env.lookup("log_value")
        assert isinstance(ft, FuncType)
        assert ft.effects == frozenset({"io"})

    def test_requires_effect_inferred(self):
        checker = _typecheck("""
@untested("requires reads")
func guarded(path: String) -> Int
    requires fs_read_text(path) == "ok"
    return 1
end func guarded
""")
        ft = checker.global_env.lookup("guarded")
        assert isinstance(ft, FuncType)
        assert ft.effects == frozenset({"fs"})

    def test_ensures_effect_inferred(self):
        checker = _typecheck("""
@untested("ensures reads")
func checked(path: String) -> Int
    ensures fs_read_text(path) == "ok"
    return 1
end func checked
""")
        ft = checker.global_env.lookup("checked")
        assert isinstance(ft, FuncType)
        assert ft.effects == frozenset({"fs"})


# ---------------------------------------------------------------------------
# Effect checking — declared vs inferred
# ---------------------------------------------------------------------------


class TestEffectChecking:
    def test_declared_effects_match_inferred(self):
        """No error when declared effects are a superset of inferred."""
        errors = _typecheck_errors("""
func greet(name: String) -> Unit with io
    example "world" -> ()
    print(name)
    return ()
end func greet
""")
        assert errors == []

    def test_declared_effects_superset_ok(self):
        """Extra declared effects are fine (function promises it *may* do them)."""
        errors = _typecheck_errors("""
func greet(name: String) -> Unit with io, fs
    example "world" -> ()
    print(name)
    return ()
end func greet
""")
        assert errors == []

    def test_undeclared_effect_error(self):
        """Error when function performs an effect not in its declaration."""
        errors = _typecheck_errors("""
func bad(x: Int) -> Unit
    example 0 -> ()
    var y: Int = x
    y = y + 1
    print(y)
    return ()
end func bad
""")
        # No explicit annotation → no error (effects just get inferred)
        assert errors == []

    def test_explicitly_pure_but_effectful_error(self):
        """A function declared with no effects but performing mutation should
        get effects inferred (since no annotation = no constraint)."""
        # When there's NO annotation at all, we just infer — no error
        checker = _typecheck("""
func counter(start: Int) -> Int
    example 0 -> 5
    var x: Int = start
    x = x + 5
    return x
end func counter
""")
        ft = checker.global_env.lookup("counter")
        assert isinstance(ft, FuncType)
        assert ft.effects == frozenset({"mutation"})

    def test_declared_pure_but_calls_effectful_error(self):
        """Function annotated with empty 'with' but calling effectful builtin.
        Note: we can't have 'with' followed by nothing in the grammar, so
        we test with an insufficient effect list."""
        errors = _typecheck_errors("""
func read_and_print(path: String) -> Unit with fs
    example "test.txt" -> ()
    let text: String = fs_read_text(path)
    print(text)
    return ()
end func read_and_print
""")
        # Should error: performs io but only declares fs
        assert len(errors) > 0
        assert "undeclared effects" in errors[0].lower() or "io" in errors[0].lower()

    def test_contract_effect_must_be_declared(self):
        errors = _typecheck_errors("""
@untested("requires reads")
func guarded(path: String) -> Int with io
    requires fs_read_text(path) == "ok"
    return 1
end func guarded
""")
        assert len(errors) > 0
        assert "undeclared effects" in errors[0].lower()
        assert "fs" in errors[0]

    def test_inline_lambda_call_effect_must_be_declared(self):
        errors = _typecheck_errors("""
func main() -> Unit with mutation
    (fn() do
        print("hi")
        return ()
    end fn)()
    return ()
end func
""")
        assert len(errors) > 0
        assert "undeclared effects" in errors[0].lower()
        assert "io" in errors[0]

    def test_local_function_value_call_effect_still_propagates(self):
        errors = _typecheck_errors("""
func main() -> Unit with mutation
    let f: () -> Unit with io = fn() do
        print("hi")
        return ()
    end fn
    f()
    return ()
end func
""")
        assert len(errors) > 0
        assert "undeclared effects" in errors[0].lower()
        assert "io" in errors[0]


# ---------------------------------------------------------------------------
# Effect subtyping
# ---------------------------------------------------------------------------


class TestEffectSubtyping:
    def test_pure_function_assignable_to_effectful_slot(self):
        """A pure function can be passed where an effectful one is expected."""
        errors = _typecheck_errors("""
func identity(x: Int) -> Int
    example 1 -> 1
    return x
end func identity

func apply(f: (Int) -> Int, x: Int) -> Int
    example (identity, 5) -> 5
    return f(x)
end func apply
""")
        assert errors == []

    def test_lambda_in_higher_order(self):
        """Lambda used in map is fine."""
        errors = _typecheck_errors("""
func double_all(xs: List[Int]) -> List[Int]
    example [1, 2, 3] -> [2, 4, 6]
    return map(xs, fn (x: Int) -> x * 2)
end func double_all
""")
        assert errors == []

    def test_effectful_callback_rejected_for_pure_higher_order(self):
        """Pure higher-order APIs must reject effectful callbacks."""
        errors = _typecheck_errors("""
func noisy(x: Int) -> Int with io
    example 1 -> 1
    print(x)
    return x
end func noisy

func use_map(xs: List[Int]) -> List[Int]
    example [1] -> [1]
    return map(xs, noisy)
end func use_map
""")
        assert len(errors) > 0

    def test_effectful_lambda_rejected_for_pure_higher_order(self):
        """Lambda callback effects must be preserved for higher-order checks."""
        errors = _typecheck_errors("""
func noisy(x: Int) -> Int with io
    example 1 -> 1
    print(x)
    return x
end func noisy

func use_map(xs: List[Int]) -> List[Int]
    example [1] -> [1]
    return map(xs, fn (x: Int) -> noisy(x))
end func use_map
""")
        assert len(errors) > 0


class TestEffectStabilization:
    def test_forward_call_effects_stabilize(self):
        """Inference should not depend on source order."""
        checker = _typecheck("""
func caller(x: Int) -> Unit
    example 1 -> ()
    callee(x)
    return ()
end func caller

func callee(x: Int) -> Unit
    example 1 -> ()
    print(x)
    return ()
end func callee
""")
        ft = checker.global_env.lookup("caller")
        assert isinstance(ft, FuncType)
        assert ft.effects == frozenset({"io"})

    def test_trait_dispatch_effects_propagate(self):
        """Trait-dispatch calls should contribute the selected impl effects."""
        checker = _typecheck("""
type Box = Box(value: Int)

trait Touch
    func touch(self: Self) -> Int
end trait

impl Touch for Box
    func touch(self: Box) -> Int with io
        example Box(1) -> 1
        print(1)
        return self.value
    end func
end impl

func use_touch(b: Box) -> Int
    example Box(1) -> 1
    return touch(b)
end func use_touch
""")
        ft = checker.global_env.lookup("use_touch")
        assert isinstance(ft, FuncType)
        assert ft.effects == frozenset({"io"})

    def test_trait_dispatch_effects_propagate_from_match_pattern_binding(self):
        """Effect inference should bind match pattern variables before arm bodies."""
        checker = _typecheck("""
type Box = Box(value: Int)

trait Touch
    func touch(self: Self) -> Int
end trait

impl Touch for Box
    func touch(self: Box) -> Int with io
        example Box(1) -> 1
        print(1)
        return self.value
    end func
end impl

func use_touch(xs: List[Box]) -> Int
    example [Box(1)] -> 1
    match xs with
        | [b, ...rest] -> return touch(b)
        | [] -> return 0
    end match
end func use_touch
""")
        ft = checker.global_env.lookup("use_touch")
        assert isinstance(ft, FuncType)
        assert ft.effects == frozenset({"io"})

    def test_trait_dispatch_effects_propagate_from_comprehension_binding(self):
        """Effect inference should bind comprehension variables before elements."""
        checker = _typecheck("""
type Box = Box(value: Int)

trait Touch
    func touch(self: Self) -> Int
end trait

impl Touch for Box
    func touch(self: Box) -> Int with io
        example Box(1) -> 1
        print(1)
        return self.value
    end func
end impl

func use_touch(xs: List[Box]) -> List[Int]
    example [Box(1)] -> [1]
    return [touch(b) for b: Box in xs]
end func use_touch
""")
        ft = checker.global_env.lookup("use_touch")
        assert isinstance(ft, FuncType)
        assert ft.effects == frozenset({"io"})


class TestTryCatchThrowRetention:
    """A catch masks the body's throw effect only when the handler is known to
    take over every value the body can throw (#197)."""

    @pytest.mark.parametrize("binding", ["local", "parameter"])
    @pytest.mark.parametrize("call", ["risky(1)", "1 |> risky"])
    def test_shadowed_callee_retains_unknown_throws(self, binding: str, call: str):
        params = "risky: (Int) -> Int with throw" if binding == "parameter" else ""
        local = (
            'let risky: (Int) -> Int with throw = fn(x: Int) -> throw "escaped"'
            if binding == "local"
            else ""
        )
        source = f"""
type ExpectedError = Expected(message: String)

@untested("throwing helper")
func risky(x: Int) -> Int with throw
    throw Expected("known")
end func

@untested("shadowing regression")
func wrapper({params}) -> Int
    {local}
    try
        return {call}
    catch err: ExpectedError
        return 0
    end try
end func
"""
        checker = _typecheck(source)
        inferred = checker.global_env.lookup("wrapper")
        assert isinstance(inferred, FuncType)
        assert "throw" in inferred.effects
        annotated = source.replace(
            f"func wrapper({params}) -> Int", f"func wrapper({params}) -> Int with io"
        )
        errors = _typecheck_errors(annotated)
        assert any(
            "undeclared effects" in error and "throw" in error for error in errors
        )

    @pytest.mark.parametrize("call", ["risky(1)", "1 |> risky"])
    def test_global_callee_known_throws_are_masked(self, call: str):
        checker = _typecheck(f"""
type ExpectedError = Expected(message: String)

@untested("throwing helper")
func risky(x: Int) -> Int with throw
    throw Expected("known")
end func

func main() -> Int with io
    try
        return {call}
    catch err: ExpectedError
        return 0
    end try
end func
""")
        inferred = checker.global_env.lookup("main")
        assert isinstance(inferred, FuncType)
        assert "throw" not in inferred.effects

    def test_unmatched_catch_retains_throw_effect(self):
        """The issue's reproduction: a typed catch that cannot match the thrown
        string must leave the throw effect on the function."""
        checker = _typecheck("""
type ExpectedError = ExpectedError(message: String)

func main() -> Int
    try
        throw "unexpected"
    catch e: ExpectedError
        return 0
    end try
end func main
""")
        ft = checker.global_env.lookup("main")
        assert isinstance(ft, FuncType)
        assert "throw" in ft.effects

    def test_unmatched_catch_rejected_without_declared_throw(self):
        errors = _typecheck_errors("""
type ExpectedError = ExpectedError(message: String)

func main() -> Int with io
    example () -> 0
    try
        throw "unexpected"
    catch e: ExpectedError
        return 0
    end try
end func main
""")
        assert any("undeclared effects" in e and "throw" in e for e in errors), errors

    def test_unmatched_catch_accepted_with_declared_throw(self):
        """Declaring the retained effect is the documented way out."""
        checker = _typecheck("""
type ExpectedError = ExpectedError(message: String)

func main() -> Int with io, throw
    try
        throw "unexpected"
    catch e: ExpectedError
        return 0
    end try
end func main
""")
        ft = checker.global_env.lookup("main")
        assert isinstance(ft, FuncType)
        assert "throw" in ft.effects

    def test_matching_adt_catch_masks_throw(self):
        checker = _typecheck("""
type ExpectedError = ExpectedError(message: String)

func main() -> Int
    try
        throw ExpectedError("boom")
    catch e: ExpectedError
        return 0
    end try
end func main
""")
        ft = checker.global_env.lookup("main")
        assert isinstance(ft, FuncType)
        assert "throw" not in ft.effects

    @pytest.mark.parametrize(
        ("definitions", "value", "catch_type", "escapes"),
        [
            (
                "type BaseError = Base(message: String)\ntype AliasError = BaseError",
                'Base("caught")',
                "AliasError",
                False,
            ),
            (
                "type BaseError = Base(message: String)\ntype AliasError = BaseError"
                "\ntype OtherError = Other(message: String)",
                'Other("escaped")',
                "AliasError",
                True,
            ),
            ("", 'JsonString("caught")', "JsonValue", False),
            (
                "type WrappedError[T] = Wrapped(value: T)",
                "Wrapped(1)",
                "WrappedError[Int]",
                False,
            ),
            ("type Message = String", '"escaped"', "Message", True),
            (
                "type Outer = Inner(value: Int)\ntype Inner = Leaf(value: Int)",
                "Inner(1)",
                "Inner",
                True,
            ),
        ],
    )
    def test_masking_uses_resolved_catch_variants(
        self, definitions: str, value: str, catch_type: str, escapes: bool
    ):
        source = f"""
{definitions}

func main() -> Int
    try
        throw {value}
    catch err: {catch_type}
        return 0
    end try
end func
"""
        checker = _typecheck(source)
        inferred = checker.global_env.lookup("main")
        assert isinstance(inferred, FuncType)
        assert ("throw" in inferred.effects) is escapes

        annotated = source.replace("main() -> Int", "main() -> Int with io")
        errors = _typecheck_errors(annotated)
        if escapes:
            assert any(
                "undeclared effects" in error and "throw" in error for error in errors
            )
        else:
            assert errors == []

    @pytest.mark.parametrize("value", ["error", "make_error()"])
    @pytest.mark.parametrize(
        ("catch_type", "escapes"), [("Outer", True), ("Inner", False)]
    )
    def test_resolved_thrown_type_is_not_a_constructor_name(
        self, value: str, catch_type: str, escapes: bool
    ):
        source = f"""
type Outer = Inner(value: Int)
type Inner = Leaf(value: Int)

@untested("type identity regression")
func make_error() -> Inner
    return Leaf(1)
end func

func main() -> Int
    let error: Inner = Leaf(1)
    try
        throw {value}
    catch err: {catch_type}
        return 0
    end try
end func
"""
        checker = _typecheck(source)
        inferred = checker.global_env.lookup("main")
        assert isinstance(inferred, FuncType)
        assert ("throw" in inferred.effects) is escapes
        errors = _typecheck_errors(
            source.replace("main() -> Int", "main() -> Int with io")
        )
        if escapes:
            assert any(
                "undeclared effects" in error and "throw" in error for error in errors
            )
        else:
            assert errors == []

    def test_call_to_throwing_function_retains_throw(self):
        """A call whose callee declares throw may throw any value, so the
        handler cannot be proven to cover it (conservative retention)."""
        checker = _typecheck("""
type ExpectedError = ExpectedError(message: String)

func risky() -> Int with throw
    example () -> 0
    throw "boom"
end func risky

func main() -> Int
    example () -> 0
    try
        risky()
    catch e: ExpectedError
        return 0
    end try
    return 0
end func main
""")
        ft = checker.global_env.lookup("main")
        assert isinstance(ft, FuncType)
        assert "throw" in ft.effects

    def test_outer_catch_covering_inner_unmatched_throw_masks(self):
        """An inner catch that cannot take the value still lets it reach an
        outer handler that can, so no throw escapes the function."""
        checker = _typecheck("""
type ExpectedError = ExpectedError(message: String)

func main() -> Int
    example () -> 0
    try
        try
            throw "unexpected"
        catch e: ExpectedError
            return 1
        end try
    catch e: String
        return 0
    end try
    return 0
end func main
""")
        ft = checker.global_env.lookup("main")
        assert isinstance(ft, FuncType)
        assert "throw" not in ft.effects

    def test_inner_unmatched_throw_without_outer_handler_retains(self):
        checker = _typecheck("""
type ExpectedError = ExpectedError(message: String)

func main() -> Int
    example () -> 0
    try
        try
            throw "unexpected"
        catch e: ExpectedError
            return 1
        end try
    catch e: ExpectedError
        return 0
    end try
    return 0
end func main
""")
        ft = checker.global_env.lookup("main")
        assert isinstance(ft, FuncType)
        assert "throw" in ft.effects

    def test_string_catch_masks_adt_throw(self):
        """At runtime, 'catch e: String' stringifies/catches any error, so it
        covers ADT throws as well."""
        checker = _typecheck("""
type ExpectedError = ExpectedError(message: String)

func main() -> Int
    example () -> 0
    try
        throw ExpectedError("boom")
    catch e: String
        return 0
    end try
    return 0
end func main
""")
        ft = checker.global_env.lookup("main")
        assert isinstance(ft, FuncType)
        assert "throw" not in ft.effects

    def test_string_catch_masks_throwing_function_call(self):
        """A string catch covers calls to functions declared with throw."""
        checker = _typecheck("""
func risky() -> Int with throw
    example () -> 0
    throw "boom"
end func risky

func main() -> Int
    example () -> 0
    try
        risky()
    catch e: String
        return 0
    end try
    return 0
end func main
""")
        ft = checker.global_env.lookup("main")
        assert isinstance(ft, FuncType)
        assert "throw" not in ft.effects

    def test_tuple_destructure_throw_in_try(self):
        """Tuple destructuring inside try binds names correctly without leaking effects."""
        checker = _typecheck("""
type ExpectedError = ExpectedError(message: String)

func main() -> Int
    example () -> 0
    try
        let (a, b): (ExpectedError, Int) = (ExpectedError("err"), 1)
        throw a
    catch e: ExpectedError
        return 0
    end try
    return 0
end func main
""")
        ft = checker.global_env.lookup("main")
        assert isinstance(ft, FuncType)
        assert "throw" not in ft.effects

    def test_index_assign_throwing_index_retains_throw(self):
        """A throwing expression in the index of an IndexAssignStatement retains throw."""
        checker = _typecheck("""
type ExpectedError = ExpectedError(message: String)

func pick(x: Int) -> Int with throw
    example (1) -> 0
    throw "fail"
end func pick

func main() -> Int
    example () -> 0
    var arr: Array[Int] = array_new(3, 0)
    try
        arr[pick(1)] = 9
    catch e: ExpectedError
        return 0
    end try
    return 0
end func main
""")
        ft = checker.global_env.lookup("main")
        assert isinstance(ft, FuncType)
        assert "throw" in ft.effects

    def test_throw_expression_nested_throw_retains_throw(self):
        """Evaluating the thrown value itself may throw an unmatched error."""
        checker = _typecheck("""
type ExpectedError = ExpectedError(message: String)

func describe() -> String with throw
    example () -> "test"
    throw "fail"
end func describe

func main() -> Int
    example () -> 0
    try
        throw ExpectedError(describe())
    catch e: ExpectedError
        return 0
    end try
    return 0
end func main
""")
        ft = checker.global_env.lookup("main")
        assert isinstance(ft, FuncType)
        assert "throw" in ft.effects

    def test_nested_try_inner_handler_masks_its_own_adt(self):
        """An inner try handles its own ADT, so only the outer throw remains for outer catch."""
        checker = _typecheck("""
type InnerError = InnerError(message: String)
type OuterError = OuterError(message: String)

func main() -> Int
    example () -> 0
    try
        try
            throw InnerError("inner")
        catch e: InnerError
            return 1
        end try
        throw OuterError("outer")
    catch e: OuterError
        return 0
    end try
    return 0
end func main
""")
        ft = checker.global_env.lookup("main")
        assert isinstance(ft, FuncType)
        assert "throw" not in ft.effects

    def test_string_alias_catch_acts_as_adt_catch_and_retains_string_throw(self):
        """Catching a type alias for String dispatches on the alias name at runtime,
        so it acts as an ADT catch and does not catch a raw string."""
        checker = _typecheck("""
type Message = String

func main() -> Int
    example () -> 0
    try
        throw "boom"
    catch e: Message
        return 0
    end try
    return 0
end func main
""")
        ft = checker.global_env.lookup("main")
        assert isinstance(ft, FuncType)
        assert "throw" in ft.effects

    def test_matching_inferred_callee_thrown_adt_masks_throw(self):
        """Calling a function that throws a known ADT is masked by catching that ADT."""
        checker = _typecheck("""
type ValidationError = ValidationError(message: String)

func validate_age(age: Int) -> Int
    example (5) -> 5
    if age < 0 then
        throw ValidationError("negative age")
    end if
    return age
end func validate_age

func main() -> Int
    example () -> 0
    try
        validate_age(-1)
    catch e: ValidationError
        return 0
    end try
    return 0
end func main
""")
        ft = checker.global_env.lookup("main")
        assert isinstance(ft, FuncType)
        assert "throw" not in ft.effects
