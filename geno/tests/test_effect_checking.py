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
    requires fs_read_text(path).unwrap() == "ok"
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
    ensures fs_read_text(path).unwrap() == "ok"
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
        """An unannotated function gets its effects inferred (no annotation =
        no constraint). Since proposal 0019 a counter in its own `var` is not
        an effect, so the inferred set is empty."""
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
        assert ft.effects == frozenset()

    def test_declared_pure_but_calls_effectful_error(self):
        """Function annotated with empty 'with' but calling effectful builtin.
        Note: we can't have 'with' followed by nothing in the grammar, so
        we test with an insufficient effect list."""
        errors = _typecheck_errors("""
func read_and_print(path: String) -> Unit with fs
    example "test.txt" -> ()
    let text: String = fs_read_text(path).unwrap()
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
    requires fs_read_text(path).unwrap() == "ok"
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


# ---------------------------------------------------------------------------
# Proposal 0019: local mutation is not an effect
# ---------------------------------------------------------------------------

_RECORDS = """
type Point = Point(x: Int, y: Int)
type Bag = Bag(items: List[Point], label: String)
type Board = Board(cells: Array[Int], pts: Array[Point])
"""


def _effects(source: str, name: str) -> frozenset[str]:
    """The effects the checker settled on for top-level function *name*."""
    func_type = _typecheck(_RECORDS + source).global_env.lookup(name)
    assert isinstance(func_type, FuncType)
    effects: frozenset[str] = func_type.effects
    return effects


def _check(source: str):
    from geno import check

    return check(_RECORDS + source)


def _codes(source: str) -> list[str]:
    return [d.code.value for d in _check(source).diagnostics if d.code is not None]


def _messages(source: str) -> list[str]:
    return [d.message for d in _check(source).diagnostics]


TOTAL = """
func total(xs: List[Int]) -> Int
    example [1, 2, 3] -> 6
    var sum = 0
    for x in xs do
        sum = sum + x
    end for
    return sum
end func
"""

BUMP = """
func bump(counter: Array[Int]) -> Int
    example array_new(1, 0) -> 1
    var alias = counter
    alias[0] = alias[0] + 1
    return alias[0]
end func
"""

# Section 2 of the proposal, one function per table row: (source, name,
# effects under 0.7). Every one of these was `{mutation}` in 0.6, except the
# mutating builtins, which carried nothing and still carry nothing.
MASKING_TABLE = {
    "var never written": (
        """
func keep(n: Int) -> Int
    example 2 -> 2
    var z = n
    return z
end func
""",
        "keep",
        frozenset(),
    ),
    "unit-local assignment": (TOTAL, "total", frozenset()),
    "mutable destructure": (
        """
func swap_sum(a: Int, b: Int) -> Int
    example 1, 2 -> 5
    var (x, y): (Int, Int) = (a, b)
    x = x + y
    y = x + y
    return y
end func
""",
        "swap_sum",
        frozenset(),
    ),
    "field write on a local record": (
        """
func moved(p: Point, dx: Int) -> Point
    example Point(x: 1, y: 2), 3 -> Point(x: 4, y: 2)
    var q = p
    q.x = q.x + dx
    return q
end func
""",
        "moved",
        frozenset(),
    ),
    "field write through a local list": (
        """
func shift_first(ps: List[Point]) -> List[Point]
    example [Point(x: 1, y: 2)] -> [Point(x: 2, y: 2)]
    var xs = ps
    xs[0].x = xs[0].x + 1
    return xs
end func
""",
        "shift_first",
        frozenset(),
    ),
    "field write through value hops": (
        """
func relabel(b: Bag) -> Bag
    example Bag(items: [Point(x: 1, y: 1)], label: "a") -> Bag(items: [Point(x: 9, y: 1)], label: "a")
    var q = b
    q.items[0].x = 9
    return q
end func
""",
        "relabel",
        frozenset(),
    ),
    "index write into an owned array": (
        """
func histogram(xs: List[Int], buckets: Int) -> List[Int]
    example [0, 1, 1], 3 -> [1, 2, 0]
    var counts = array_new(buckets, 0)
    for x in xs do
        counts[x] = counts[x] + 1
    end for
    return array_to_list(counts)
end func
""",
        "histogram",
        frozenset(),
    ),
    "owned array returned through a borrowing pipeline stage": (
        """
func piped_histogram(xs: List[Int]) -> List[Int]
    example [0, 1, 1] -> [1, 2]
    var counts = array_new(2, 0)
    for x in xs do
        counts[x] = counts[x] + 1
    end for
    let n = counts |> array_length
    let first = counts |> array_get(_, 0)
    return [first, counts[n - 1]]
end func
""",
        "piped_histogram",
        frozenset(),
    ),
    "field write into an owned array's element": (
        """
func bumped(n: Int) -> Int
    example 2 -> 1
    var pts = array_new(n, Point(x: 0, y: 0))
    pts[0].x = 1
    return pts[0].x
end func
""",
        "bumped",
        frozenset(),
    ),
    "index writes into an owned MutableMap and Vec": (
        """
func memo(n: Int) -> Int
    example 2 -> 4
    var m: MutableMap[Int, Int] = mutable_map_new()
    m[n] = n * 2
    var v: Vec[Int] = vec_new()
    vec_push(v, 1)
    v[0] = n
    return mutable_map_get(m, n).unwrap() + vec_get(v, 0) - n
end func
""",
        "memo",
        frozenset(),
    ),
    "owned array returned": (
        """
func squares(n: Int) -> Array[Int]
    example 2 -> array_from_list([0, 1])
    var a = array_new(n, 0)
    for i in range(0, n) do
        a[i] = i * i
    end for
    return a
end func
""",
        "squares",
        frozenset(),
    ),
    "array aliasing a parameter": (BUMP, "bump", frozenset({"mutation"})),
    "array copied into a second binding": (
        """
func rolled(n: Int) -> Int
    example 2 -> 0
    var prev = array_new(n, 0)
    var cur = array_new(n, 0)
    prev = cur
    cur = array_new(n, 0)
    prev[0] = 1
    return cur[0]
end func
""",
        "rolled",
        frozenset({"mutation"}),
    ),
    "array passed to a user function": (
        """
func first(a: Array[Int]) -> Int
    example array_new(1, 7) -> 7
    return a[0]
end func

func lent(n: Int) -> Int
    example 1 -> 0
    var a = array_new(n, 0)
    let seen = first(a)
    a[0] = 1
    return seen
end func
""",
        "lent",
        frozenset({"mutation"}),
    ),
    "array piped to a user function": (
        """
func first_of(a: Array[Int]) -> Int
    example array_new(1, 7) -> 7
    return a[0]
end func

func piped_lent(n: Int) -> Int
    example 1 -> 0
    var a = array_new(n, 0)
    let seen = a |> first_of
    a[0] = 1
    return seen
end func
""",
        "piped_lent",
        frozenset({"mutation"}),
    ),
    "array read by a closure": (
        """
func peeked(n: Int) -> Int
    example 1 -> 1
    var a = array_new(n, 0)
    let peek = fn() -> a[0]
    a[0] = 1
    return peek()
end func
""",
        "peeked",
        frozenset({"mutation"}),
    ),
    "array reassigned from a parameter after the write": (
        """
func swapped(p: Array[Int]) -> Int
    example array_new(1, 0) -> 0
    var a = array_new(1, 0)
    for i in range(0, 2) do
        a[0] = i
        a = p
    end for
    return a[0]
end func
""",
        "swapped",
        frozenset({"mutation"}),
    ),
    "write through a reference field": (
        """
func set_cell(b: Board) -> Int
    example Board(cells: array_new(1, 0), pts: array_new(1, Point(x: 0, y: 0))) -> 1
    var q = b
    q.cells[0] = 1
    return q.cells[0]
end func
""",
        "set_cell",
        frozenset({"mutation"}),
    ),
    "field write below a reference field": (
        """
func set_pt(b: Board) -> Int
    example Board(cells: array_new(1, 0), pts: array_new(1, Point(x: 0, y: 0))) -> 1
    var q = b
    q.pts[0].x = 1
    return q.pts[0].x
end func
""",
        "set_pt",
        frozenset({"mutation"}),
    ),
    "two-dimensional table with aliased rows": (
        """
func grid_set(n: Int) -> Int
    example 2 -> 1
    var grid = array_new(n, array_new(n, 0))
    grid[0][1] = 1
    return grid[1][1]
end func
""",
        "grid_set",
        frozenset({"mutation"}),
    ),
    # Known limitation (0.7 draft): the reference checker does not charge
    # mutating builtins on any receiver. Closing it flips these on purpose.
    "mutating builtins on a parameter": (
        """
func push_param(v: Vec[Int], a: Array[Int]) -> Int
    example vec_new(), array_new(1, 0) -> 1
    vec_push(v, 1)
    array_set(array: a, index: 0, value: 1)
    return vec_length(v)
end func
""",
        "push_param",
        frozenset(),
    ),
    "calling a still-charged function": (
        BUMP
        + """
func calls_bump(n: Int) -> Int
    example 0 -> 1
    return bump(array_new(1, n))
end func
""",
        "calls_bump",
        frozenset({"mutation"}),
    ),
    "calling a masked function": (
        TOTAL
        + """
func calls_total(xs: List[Int]) -> Int
    example [1] -> 1
    return total(xs)
end func
""",
        "calls_total",
        frozenset(),
    ),
    "calling a closure that writes a captured var": (
        """
func ticks(n: Int) -> Int
    example 2 -> 2
    var count = 0
    let tick = fn() do
        count = count + 1
        return count
    end fn
    for i in range(0, n) do
        let ignored = tick()
    end for
    return count
end func
""",
        "ticks",
        frozenset({"mutation"}),
    ),
    "creating a closure that writes a captured var": (
        """
@untested("returns a closure")
func make_counter() -> () -> Int with mutation
    var n = 0
    return fn() do
        n = n + 1
        return n
    end fn
end func

func makes(n: Int) -> Int
    example 1 -> 1
    let next = make_counter
    return n
end func
""",
        "makes",
        frozenset(),
    ),
}


class TestLocalMutationMasking:
    """Proposal 0019: a write only the writing function or lambda can see."""

    @pytest.mark.parametrize("case", sorted(MASKING_TABLE))
    def test_section_two_table(self, case: str) -> None:
        source, name, expected = MASKING_TABLE[case]
        assert _effects(source, name) == expected

    @pytest.mark.parametrize(
        "call",
        [
            "apply(total, xs)",
            "map([xs], total)[0]",
            "[xs].map(total)[0]",
            "xs |> total",
            "[xs] |> map(_, total) |> sum",
        ],
    )
    def test_a_local_accumulator_is_a_pure_callback(self, call: str) -> None:
        source = (
            TOTAL
            + """
func apply(f: (List[Int]) -> Int, xs: List[Int]) -> Int
    example fn(ys: List[Int]) -> length(ys), [4, 5] -> 2
    return f(xs)
end func
"""
            + f"""
func caller(xs: List[Int]) -> Int
    example [1, 2] -> 3
    return {call}
end func
"""
        )
        result = _check(source)
        assert result.ok, [str(d) for d in result.diagnostics]
        assert _effects(source, "caller") == frozenset()

    def test_a_lambda_with_its_own_accumulator_is_pure(self) -> None:
        source = """
func sums(rows: List[List[Int]]) -> List[Int]
    example [[1, 2], [3]] -> [3, 3]
    return rows.map(fn(ys: List[Int]) do
        var s = 0
        for y in ys do
            s = s + y
        end for
        return s
    end fn)
end func

func pure_slot() -> Int
    example () -> 1
    let f: () -> Int = fn() do
        var n = 0
        n = n + 1
        return n
    end fn
    return f()
end func
"""
        result = _check(source)
        assert result.ok, [str(d) for d in result.diagnostics]
        assert _effects(source, "sums") == frozenset()
        assert _effects(source, "pure_slot") == frozenset()

    def test_main_with_io_may_keep_locals(self) -> None:
        source = """
func main() -> Unit with io
    var label = "total: "
    var counts = array_new(2, 0)
    counts[1] = 5
    var p = Point(x: 1, y: 2)
    p.x = counts[1]
    label = label + to_string(p.x)
    print(label)
end func
"""
        result = _check(source)
        assert result.ok, [str(d) for d in result.diagnostics]
        assert _effects(source, "main") == frozenset({"io"})

    def test_impl_methods_mask_their_locals(self) -> None:
        source = """
type Counter = Counter(n: Int)

trait Tally
    func tally(self: Self, xs: List[Int]) -> Int
end trait

impl Tally for Counter
    func tally(self: Counter, xs: List[Int]) -> Int
        example Counter(n: 1), [2, 3] -> 6
        var t = self.n
        for x in xs do
            t = t + x
        end for
        return t
    end func
end impl

func use_tally[T: Tally](c: T) -> Int
    example Counter(n: 1) -> 1
    return tally(c, [])
end func

func apply(f: (Counter) -> Int, c: Counter) -> Int
    example fn(c: Counter) -> 0, Counter(n: 1) -> 0
    return f(c)
end func

func caller(c: Counter) -> Int
    example Counter(n: 2) -> 2
    return apply(use_tally, c)
end func
"""
        checker = _typecheck(_RECORDS + source)
        method = checker.impl_registry[("Tally", "Counter")]["tally"]
        assert method.effects == frozenset()
        bounded = checker.global_env.lookup("use_tally")
        assert isinstance(bounded, FuncType)
        assert bounded.effects == frozenset()

    def test_async_functions_mask_their_locals(self) -> None:
        source = """
async func summed(xs: List[Int]) -> Int
    var s = 0
    for x in xs do
        s = s + x
    end for
    return s
end func

async func caller(xs: List[Int]) -> Int
    var a = array_new(1, 0)
    a[0] = await summed(xs)
    return a[0]
end func
"""
        assert _effects(source, "summed") == frozenset()
        assert _effects(source, "caller") == frozenset()

    def test_generic_functions_mask_their_locals(self) -> None:
        source = """
func count_where[T](xs: List[T], keep: (T) -> Bool) -> Int
    example [1, 2, 3], fn(x: Int) -> x > 1 -> 2
    var n = 0
    for x in xs do
        if keep(x) then
            n = n + 1
        end if
    end for
    return n
end func

func apply(f: (List[Int], (Int) -> Bool) -> Int, xs: List[Int]) -> Int
    example fn(ys: List[Int], p: (Int) -> Bool) -> 0, [1] -> 0
    return f(xs, fn(x: Int) -> x > 0)
end func

func caller(xs: List[Int]) -> Int
    example [1, -1] -> 1
    let f: (List[Int], (Int) -> Bool) -> Int = count_where
    return apply(f, xs) + count_where(xs, fn(x: Int) -> x > 0) - 1
end func
"""
        result = _check(source)
        assert result.ok, [str(d) for d in result.diagnostics]
        assert _effects(source, "count_where") == frozenset()
        assert _effects(source, "caller") == frozenset()

    def test_a_declared_mutation_stays_legal(self) -> None:
        source = TOTAL.replace("-> Int\n", "-> Int with mutation\n", 1)
        assert "with mutation" in source
        result = _check(source)
        assert result.ok, [str(d) for d in result.diagnostics]
        assert _effects(source, "total") == frozenset({"mutation"})

    def test_a_captured_write_still_needs_a_declared_mutation(self) -> None:
        source = """
func main() -> Unit with io
    var n = 0
    let inc = fn() do
        n = n + 1
        return n
    end fn
    print(inc())
end func
"""
        assert _codes(source) == ["E310"]
        assert _messages(source) == [
            "Function 'main' performs undeclared effects: mutation. "
            "Declared effects: io"
        ]

    def test_a_parameter_alias_still_needs_a_declared_mutation(self) -> None:
        source = BUMP.replace("-> Int\n", "-> Int with io\n", 1)
        assert _codes(source) == ["E310"]
        assert "performs undeclared effects: mutation" in _messages(source)[0]

    def test_a_capturing_closure_returned_as_pure_is_rejected(self) -> None:
        source = """
@untested("returns a closure")
func make_counter() -> () -> Int
    var n = 0
    return fn() do
        n = n + 1
        return n
    end fn
end func
"""
        assert _codes(source) == ["E300"]
        assert _messages(source) == [
            "Return type mismatch: expected () -> Int, got () -> Int with mutation"
        ]

    def test_a_nested_lambda_writing_the_enclosing_lambdas_var(self) -> None:
        source = """
func outer(n: Int) -> Int
    example 1 -> 1
    let make = fn() do
        var c = 0
        let inc = fn() do
            c = c + 1
            return c
        end fn
        return inc
    end fn
    let calls = fn() do
        var c = 0
        let inc = fn() do
            c = c + 1
            return c
        end fn
        return inc()
    end fn
    let pure_make: () -> () -> Int with mutation = make
    let charged: () -> Int with mutation = calls
    return n
end func
"""
        # The inner lambda writes `c`, captured from the enclosing lambda, so
        # it carries `mutation`. `make` only creates it and stays pure;
        # `calls` calls it and is charged.
        result = _check(source)
        assert result.ok, [str(d) for d in result.diagnostics]
        rejected = source.replace(
            "let charged: () -> Int with mutation = calls",
            "let charged: () -> Int = calls",
        )
        assert _codes(rejected) == ["E300"]

    def test_unknown_never_masks(self) -> None:
        """A write whose check stops at an error, or whose root does not
        resolve, keeps the function charged."""
        for body in (
            "ghost = 1",
            "ghost[0] = 1",
            "ghost.x = 1",
            "let a = array_new(1, 0)\n    a[0] = 1",
            "var p = Point(x: 0, y: 0)\n    p.z = 1",
        ):
            source = (
                _RECORDS
                + f"""
func f() -> Int
    example () -> 0
    {body}
    return 0
end func
"""
            )
            program = parse(source)
            checker = TypeChecker()
            try:
                checker.check_program(program)
            except Exception:
                pass
            else:  # pragma: no cover - every body is an error
                raise AssertionError(body)
            [defn] = [d for d in program.definitions if getattr(d, "name", "") == "f"]
            inferred = checker._infer_function_effects(
                defn, checker._effect_env_for_function(defn)
            )
            assert "mutation" in inferred, body

    def test_binding_locality(self) -> None:
        from geno.types import IntType, TypeEnv

        checker = TypeChecker()
        local = checker.global_env.child()
        local.bind("x", IntType(), mutable=True)
        assert checker._binding_locality("x", local.child()) == "unit-local"
        assert checker._binding_locality("missing", local) == "unresolved"
        checker.global_env.bind("module_value", IntType())
        assert checker._binding_locality("module_value", local) == "captured"
        detached = TypeEnv()
        detached.bind("y", IntType(), mutable=True)
        assert checker._binding_locality("y", detached) == "captured"
        # Inside a lambda checked in `local`, its own scope is unit-local and
        # anything at or above the capture environment is captured.
        checker._lambda_capture_env = local
        inner = local.child()
        inner.bind("z", IntType(), mutable=True)
        assert checker._binding_locality("z", inner) == "unit-local"
        assert checker._binding_locality("x", inner) == "captured"

    def test_effect_inference_reads_the_classification_not_the_target(
        self, monkeypatch
    ) -> None:
        source = (
            _RECORDS
            + """
func f(n: Int) -> Int
    example 1 -> 1
    var a = array_new(n, 0)
    a[0] = n
    var p = Point(x: 0, y: 0)
    p.x = a[0]
    var t = 0
    t = p.x
    return t
end func
"""
        )
        program = parse(source)
        checker = TypeChecker()
        checker.check_program(program)
        [defn] = [d for d in program.definitions if getattr(d, "name", "") == "f"]
        writes = [s for s in defn.body if type(s).__name__.endswith("AssignStatement")]
        assert len(writes) == 3
        for stmt in writes:
            entry = checker._write_classifications[id(stmt)]
            assert entry[0] is stmt
            assert entry[1].locality == "unit-local"
        index_write, field_write, _plain = writes
        assert checker._write_classifications[id(index_write)][1].root_is_reference
        assert not checker._write_classifications[id(field_write)][1].root_is_reference

        targets = {id(index_write.target), id(field_write.target)}
        checked: list[int] = []
        original = TypeChecker._check_expression

        def counting(self, expr, env):
            checked.append(id(expr))
            return original(self, expr, env)

        monkeypatch.setattr(TypeChecker, "_check_expression", counting)
        env = checker._effect_env_for_function(defn)
        assert checker._infer_function_effects(defn, env) == frozenset()
        assert checked, "effect inference re-checks initializers"
        assert targets.isdisjoint(checked)

        # Without the recorded classification every write is charged.
        checker._write_classifications.clear()
        env = checker._effect_env_for_function(defn)
        assert checker._infer_function_effects(defn, env) == frozenset({"mutation"})

    def test_effect_inference_re_checks_record_no_facts(self) -> None:
        """Only a real check records ownership and write facts; the re-checks
        effect inference makes in its own environment read them."""
        source = (
            _RECORDS
            + """
func f(n: Int) -> Int
    example 1 -> 1
    var a = array_new(n, 0)
    a[0] = n
    return a[0]
end func
"""
        )
        program = parse(source)
        checker = TypeChecker()
        checker.check_program(program)
        [defn] = [d for d in program.definitions if getattr(d, "name", "") == "f"]
        write = defn.body[1]
        recorded = checker._write_classifications[id(write)]
        assert checker._checked_unit_ownership(defn) == frozenset({"a"})
        with checker._effect_inference():
            checker._set_write_classification(write, None)
            # The cache answers; a unit no real check saw owns nothing.
            assert checker._record_unit_ownership(defn) == frozenset({"a"})
            checker._unit_owned_names.clear()
            assert checker._record_unit_ownership(defn) == frozenset()
            assert checker._checked_unit_ownership(defn) == frozenset()
        assert checker._write_classifications[id(write)] == recorded
        # Outside effect inference, the unit's check records again.
        assert checker._record_unit_ownership(defn) == frozenset({"a"})
        assert checker._checked_unit_ownership(defn) == frozenset({"a"})

    def test_a_lambda_checked_mid_walk_restores_the_functions_owned_names(
        self,
    ) -> None:
        """Effect inference re-checks `g` between the two writes into `a`;
        `_function_context` must hand `f` its own owned names back."""
        source = """
func f(n: Int) -> Int
    example 1 -> 4
    var a = array_new(1, 0)
    let g = fn(x: Int) do
        var b = array_new(1, x)
        b[0] = b[0] + 1
        return b[0]
    end fn
    a[0] = g(n)
    let h: (Int) -> Int = g
    a[0] = a[0] + h(1)
    return a[0]
end func

func apply(k: (Int) -> Int, n: Int) -> Int
    example fn(x: Int) -> x, 1 -> 1
    return k(n)
end func

func caller(n: Int) -> Int
    example 1 -> 4
    return apply(f, n)
end func
"""
        result = _check(source)
        assert result.ok, [str(d) for d in result.diagnostics]
        assert _effects(source, "f") == frozenset()

    def test_a_local_binding_named_like_a_builtin_is_not_fresh(self) -> None:
        """Checker only: JavaScript refuses a local named after a builtin."""
        source = """
func f(shared: Array[Int]) -> Int
    example array_new(1, 0) -> 5
    let array_new = fn(n: Int, v: Int) -> shared
    var a = array_new(1, 0)
    a[0] = 5
    return shared[0]
end func
"""
        assert _effects(source, "f") == frozenset({"mutation"})
        unshadowed = source.replace("let array_new = fn", "let make = fn").replace(
            "var a = array_new(1, 0)", "var a = make(1, 0)"
        )
        assert _effects(unshadowed, "f") == frozenset({"mutation"})

    def test_a_top_level_function_named_like_a_builtin_is_not_fresh(self) -> None:
        source = """
func array_new(n: Int, v: Int) -> Array[Int]
    example 1, 0 -> array_from_list([0])
    return array_from_list([v])
end func

func f(n: Int) -> Int
    example 1 -> 5
    var a = array_new(n, 0)
    a[0] = 5
    return a[0]
end func
"""
        assert _effects(source, "f") == frozenset({"mutation"})


BREAKING = """
func local_sum(n: Int) -> Int
    example 2 -> 3
    var y = n
    y = y + 1
    return y
end func

func shared_bump(n: Int) -> Int
    example 1 -> 1
    var cells = array_new(1, n)
    var alias = cells
    alias[0] = n
    return cells[0]
end func

func pick[T](a: T, b: T) -> T
    example 1, 2 -> 1
    return a
end func
"""


class TestLocalMutationBreakingClass:
    """Programs 0.6 accepted because both function types carried `mutation`."""

    def test_reassigning_a_var_function(self) -> None:
        source = (
            BREAKING
            + """
func main() -> Unit with io, mutation
    var f = local_sum
    f = shared_bump
    print(f(1))
end func
"""
        )
        assert _codes(source) == ["E300"]
        assert _messages(source) == [
            "Type mismatch in assignment: expected (Int) -> Int, got (Int) -> Int "
            "with mutation"
        ]

    def test_a_generic_parameter_bound_by_its_first_argument(self) -> None:
        source = (
            BREAKING
            + """
func main() -> Unit with io, mutation
    let g = pick(local_sum, shared_bump)
    print(g(1))
end func
"""
        )
        assert _codes(source) == ["E300"]
        assert "Argument 2 type mismatch" in _messages(source)[0]

    def test_reassigning_an_option_of_a_function(self) -> None:
        source = (
            BREAKING
            + """
func main() -> Unit with io, mutation
    var o = Some(local_sum)
    o = Some(shared_bump)
    print(o.unwrap()(1))
end func
"""
        )
        assert _codes(source) == ["E300"]

    @pytest.mark.parametrize(
        ("old", "new"),
        [
            ("var f = local_sum", "var f: (Int) -> Int with mutation = local_sum"),
            (
                "func local_sum(n: Int) -> Int\n",
                "func local_sum(n: Int) -> Int with mutation\n",
            ),
        ],
    )
    def test_the_migration_restores_the_06_type(self, old: str, new: str) -> None:
        source = (
            BREAKING
            + """
func main() -> Unit with io, mutation
    var f = local_sum
    f = shared_bump
    print(f(1))
end func
"""
        )
        migrated = source.replace(old, new, 1)
        assert migrated != source
        result = _check(migrated)
        assert result.ok, [str(d) for d in result.diagnostics]
