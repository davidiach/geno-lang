"""Proposal 0019: the boundary of local-mutation masking, on every backend.

Masking removes ``mutation`` from a function or lambda whose writes only it can
observe. Each case below is an adversarial program: the checker's inferred
effects for the functions it names are pinned, and the program runs on the
interpreter, compiled Python and compiled JavaScript, which must print the
same output (the only exceptions are the ``KNOWN_GAPS`` cases that pin a
separate ``js_output``; see below).

- A *masked* function (no ``mutation``) is called and the caller then prints
  the state it passed in or holds: it must be unchanged. Where a function is
  called twice, the second call must not see the first one's writes either.
- A *charged* function (``mutation``) mostly writes state the caller can see,
  and the caller prints the change, which is why the effect must stay. A few
  charged shapes are only conservative (the write is private, but the rule
  does not prove it); they say so.

``KNOWN_GAPS`` pins, as current behaviour, the places where a caller can
observe a write the checker does not charge: the mutating-builtin known
limitation, the three ways masking widens it (a builtin writer that 0.6
charged only for its local loop counter among them), and the
argument-passing alias (proposal 0019, section 6 and Unresolved Questions 1
and 7). Closing any of them must change these tests deliberately. The
repository's own functions of the builtin-writer shape declare
``with mutation``, which is pinned too.

Two of the ``KNOWN_GAPS`` cases pin a pre-existing compiled-JavaScript parity
bug instead (Unresolved Question 7): a ``for`` loop over an ``Array`` iterates
it live and binds its elements without a copy, and an element or field read
passed as an argument (``keep(a[0])``, ``keep(b.p)``) hands the callee the
stored object. The interpreter and compiled Python snapshot both. The masked
writes in those cases stay private on every backend (the effect is sound),
but what the writing function itself computes differs on JavaScript, so the
case records that backend's output separately. A JavaScript runtime fix
flips them deliberately.

The local binding named after a builtin (``let array_new = ...``) is
checker-only: compiled JavaScript refuses a local named after a runtime
builtin, while the interpreter and compiled Python accept it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

from geno import check
from geno.parser import parse
from geno.tests.test_backend_parity import (
    HAS_NODE,
    _compiled_js_output,
    _compiled_python_output,
    _interpreter_output,
)
from geno.typechecker import TypeChecker
from geno.types import FuncType

PURE: frozenset[str] = frozenset()
MUTATION = frozenset({"mutation"})
IO = frozenset({"io"})
IO_MUTATION = frozenset({"io", "mutation"})

BACKENDS = [
    pytest.param(_interpreter_output, id="interpreter"),
    pytest.param(_compiled_python_output, id="python"),
    pytest.param(
        _compiled_js_output,
        id="js",
        marks=pytest.mark.skipif(not HAS_NODE, reason="Node.js not available"),
    ),
]

TYPES = """\
type P = P(x: Int)
type Bag = Bag(pts: List[P])
type Box = Box(p: P)
type Holder = Holder(cells: Array[Int])
type Board = Board(cells: Array[Int], pts: Array[P])
type Wrap[T] = Wrap(v: T)

"""


@dataclass(frozen=True)
class Case:
    """One adversarial program, the effects it infers, and what it prints.

    ``output`` is what every backend prints. ``js_output``, when set, is what
    compiled JavaScript prints instead: a pinned pre-existing JS runtime
    parity bug, never a masking decision.
    """

    source: str
    effects: dict[str, frozenset[str]]
    output: str
    js_output: str | None = None


def _lines(*values: object) -> str:
    return "".join(f"{value}\n" for value in values)


CASES: dict[str, Case] = {
    # --- local accumulators: the motivating shape --------------------------
    "local-accumulator-as-pure-callback": Case(
        """
func total(xs: List[Int]) -> Int
    example [1, 2, 3] -> 6
    var sum = 0
    for x in xs do
        sum = sum + x
    end for
    return sum
end func

func apply(f: (List[Int]) -> Int, xs: List[Int]) -> Int
    example fn(ys: List[Int]) -> length(ys), [4, 5] -> 2
    return f(xs)
end func

func main() -> Unit with io
    let xs = [4, 5, 6]
    print(apply(total, xs))
    print(apply(total, xs))
    print([[1, 2], [3]].map(total))
    print(xs)
end func
""",
        {"total": PURE, "apply": PURE, "main": IO},
        _lines(15, 15, "[3, 3]", "[4, 5, 6]"),
    ),
    # --- aliasing through let and var --------------------------------------
    "alias-through-var-is-charged": Case(
        """
func bump(counter: Array[Int]) -> Int
    example array_new(1, 0) -> 1
    var alias = counter
    alias[0] = alias[0] + 1
    return alias[0]
end func

func main() -> Unit with io, mutation
    let shared = array_new(1, 0)
    print(bump(shared))
    print(bump(shared))
    print(shared[0])
end func
""",
        {"bump": MUTATION, "main": IO_MUTATION},
        _lines(1, 2, 2),
    ),
    "alias-through-let-is-charged": Case(
        """
func poke(a: Array[Int]) -> Int
    example array_new(1, 0) -> 9
    let b = a
    var c = b
    c[0] = 9
    return c[0]
end func

func main() -> Unit with io, mutation
    let shared = array_new(1, 0)
    print(poke(shared))
    print(shared[0])
end func
""",
        {"poke": MUTATION, "main": IO_MUTATION},
        _lines(9, 9),
    ),
    "let-alias-of-a-fresh-array-is-charged": Case(
        # Conservative: `let c = b` is an escape, so `b` is not owned, although
        # the write never leaves the function.
        """
func through_let(n: Int) -> Int
    example 1 -> 9
    var b = array_new(1, n)
    let c = b
    b[0] = 9
    return c[0]
end func

func main() -> Unit with io, mutation
    print(through_let(1))
end func
""",
        {"through_let": MUTATION},
        _lines(9),
    ),
    "record-copied-into-a-var-is-masked": Case(
        """
func moved(p: P, dx: Int) -> P
    example P(x: 1), 2 -> P(x: 3)
    var q = p
    q.x = q.x + dx
    return q
end func

func main() -> Unit with io
    let p = P(x: 1)
    print(moved(p, 2))
    print(moved(p, 2))
    print(p)
end func
""",
        {"moved": PURE, "main": IO},
        _lines("P(x: 3)", "P(x: 3)", "P(x: 1)"),
    ),
    "fresh-copy-of-a-let-alias-is-masked": Case(
        """
func copied(a: Array[Int]) -> Int
    example array_new(1, 0) -> 9
    let b = a
    var c = array_copy(b)
    c[0] = 9
    return c[0]
end func

func main() -> Unit with io
    let shared = array_new(1, 0)
    print(copied(shared))
    print(shared[0])
end func
""",
        {"copied": PURE, "main": IO},
        _lines(9, 0),
    ),
    # --- tuple, record and list wrapping -----------------------------------
    "wrapped-and-returned-is-masked": Case(
        """
@untested("returns a fresh array")
func wrap_tuple(n: Int) -> (Array[Int], Int)
    var a = array_new(1, n)
    a[0] = a[0] + 1
    return (a, n)
end func

@untested("returns a fresh array")
func wrap_record(n: Int) -> Holder
    var a = array_new(1, n)
    a[0] = a[0] + 1
    return Holder(cells: a)
end func

@untested("returns a fresh array")
func wrap_list(n: Int) -> List[Array[Int]]
    var a = array_new(1, n)
    a[0] = a[0] + 1
    return [a]
end func

@untested("returns a fresh array")
func wrap_option(n: Int, c: Bool) -> Option[Array[Int]]
    var a = array_new(1, n)
    a[0] = a[0] + 1
    return if c then Some(a) else None end if
end func

func main() -> Unit with io
    let (t1, n1) = wrap_tuple(1)
    let (t2, n2) = wrap_tuple(1)
    array_set(array: t1, index: 0, value: 100)
    print(t2[0])
    let r1 = wrap_record(1)
    let r2 = wrap_record(1)
    array_set(array: r1.cells, index: 0, value: 100)
    print(r2.cells[0])
    let l1 = wrap_list(1)
    let l2 = wrap_list(1)
    array_set(array: l1[0], index: 0, value: 100)
    print(l2[0][0])
    match wrap_option(1, true) with
        | Some(a) -> print(a[0])
        | None -> print("none")
    end match
end func
""",
        {
            "wrap_tuple": PURE,
            "wrap_record": PURE,
            "wrap_list": PURE,
            "wrap_option": PURE,
            "main": IO,
        },
        _lines(2, 2, 2, 2),
    ),
    "wrapped-and-stored-is-charged": Case(
        """
@untested("stores into its argument")
func stash_tuple(store: Vec[(Array[Int], Int)]) -> Int
    var a = array_new(1, 0)
    vec_push(store, (a, 1))
    a[0] = 5
    return 0
end func

@untested("stores into its argument")
func stash_record(store: Vec[Holder]) -> Int
    var a = array_new(1, 0)
    vec_push(store, Holder(cells: a))
    a[0] = 6
    return 0
end func

@untested("stores into its argument")
func stash_list(store: Vec[List[Array[Int]]]) -> Int
    var a = array_new(1, 0)
    vec_push(store, [a])
    a[0] = 7
    return 0
end func

func kept_locally(n: Int) -> Int
    example 1 -> 8
    var a = array_new(1, n)
    let t = (a, 1)
    a[0] = 8
    return t.0[0]
end func

func main() -> Unit with io, mutation
    let tuples: Vec[(Array[Int], Int)] = vec_new()
    let a = stash_tuple(tuples)
    let (arr, k) = vec_get(tuples, 0)
    print(arr[0])
    let records: Vec[Holder] = vec_new()
    let b = stash_record(records)
    print(vec_get(records, 0).cells[0])
    let lists: Vec[List[Array[Int]]] = vec_new()
    let c = stash_list(lists)
    print(vec_get(lists, 0)[0][0])
    print(kept_locally(1))
end func
""",
        {
            "stash_tuple": MUTATION,
            "stash_record": MUTATION,
            "stash_list": MUTATION,
            # Conservative: the tuple never leaves the function.
            "kept_locally": MUTATION,
        },
        _lines(5, 6, 7, 8),
    ),
    # --- closures ----------------------------------------------------------
    "closure-reading-the-collection-is-charged": Case(
        """
@untested("stores a closure into its argument")
func stash_reader(store: Vec[() -> Int]) -> Int
    var a = array_new(1, 0)
    vec_push(store, fn() -> a[0])
    a[0] = 5
    return 0
end func

func main() -> Unit with io, mutation
    let store: Vec[() -> Int] = vec_new()
    let r = stash_reader(store)
    let peek = vec_get(store, 0)
    print(peek())
end func
""",
        {"stash_reader": MUTATION},
        _lines(5),
    ),
    "closure-writing-a-captured-var": Case(
        """
@untested("returns a closure")
func make_counter() -> () -> Int with mutation
    var n = 0
    return fn() do
        n = n + 1
        return n
    end fn
end func

func count_with_closure(xs: List[Int]) -> Int
    example [7, 8] -> 2
    var n = 0
    let inc = fn() do
        n = n + 1
        return n
    end fn
    for x in xs do
        let seen = inc()
    end for
    return n
end func

func main() -> Unit with io, mutation
    let next = make_counter()
    let other = make_counter()
    print(next())
    print(next())
    print(other())
    print(count_with_closure([7, 8]))
end func
""",
        # make_counter only creates the closure, so it is pure; the closure's
        # own type carries `mutation`, and so does every unit that calls it,
        # including count_with_closure (calls of a local closure stay
        # charged, Unresolved Question 2).
        {"make_counter": PURE, "count_with_closure": MUTATION},
        _lines(1, 2, 1, 2),
    ),
    "returned-closure-over-a-finished-var-is-pure": Case(
        """
@untested("returns a closure")
func snapshot_later() -> () -> Int
    var n = 1
    let g = fn() -> n
    n = 5
    return g
end func

func main() -> Unit with io
    let g = snapshot_later()
    print(g())
    print(g())
end func
""",
        {"snapshot_later": PURE, "main": IO},
        _lines(5, 5),
    ),
    "closure-writing-a-captured-array-is-charged": Case(
        """
@untested("returns a closure")
func make_tally() -> () -> Int with mutation
    var a = array_new(1, 0)
    return fn() do
        a[0] = a[0] + 1
        return a[0]
    end fn
end func

func main() -> Unit with io, mutation
    let next = make_tally()
    print(next())
    print(next())
end func
""",
        # The closure is charged (its declared type says so); creating it is
        # not.
        {"make_tally": PURE},
        _lines(1, 2),
    ),
    "lambda-owning-its-own-state-is-pure": Case(
        """
func twice_fresh(n: Int) -> Int
    example 2 -> 4
    let f = fn(k: Int) do
        var a = array_new(1, 0)
        a[0] = a[0] + k
        var s = 0
        s = s + a[0]
        return s
    end fn
    return f(n) + f(n)
end func

func main() -> Unit with io
    print(twice_fresh(2))
    print([1, 2].map(fn(k: Int) do
        var acc = k
        acc = acc * 10
        return acc
    end fn))
end func
""",
        {"twice_fresh": PURE, "main": IO},
        _lines(4, "[10, 20]"),
    ),
    "closure-stored-by-a-charged-write": Case(
        """
@untested("stores a closure into its argument")
func store_reader(slots: Array[() -> Int]) -> Int
    var n = 1
    var view = slots
    view[0] = fn() -> n
    n = 5
    return 0
end func

func main() -> Unit with io, mutation
    let slots: Array[() -> Int] = array_new(1, fn() -> 0)
    let r = store_reader(slots)
    let peek = slots[0]
    print(peek())
end func
""",
        # The write `view[0] = ...` goes through a copy of a parameter, so it
        # is charged; that is what keeps the masked `n = 5` from being seen.
        {"store_reader": MUTATION},
        _lines(5),
    ),
    # --- loops, match and for ----------------------------------------------
    "loop-reassignment-from-a-parameter-is-charged": Case(
        """
func loop_reassign(p: Array[Int], k: Int) -> Int
    example array_new(1, 0), 1 -> 0
    var a = array_new(1, 0)
    var i = 0
    while i < k do
        a[0] = 9
        a = p
        i = i + 1
    end while
    return 0
end func

func loop_fresh(k: Int) -> Int
    example 3 -> 3
    var a = array_new(1, 0)
    var i = 0
    while i < k do
        a[0] = a[0] + 1
        a = array_new(1, a[0])
        i = i + 1
    end while
    return a[0]
end func

func main() -> Unit with io, mutation
    let p = array_new(1, 0)
    print(loop_reassign(p, 2))
    print(p[0])
    print(loop_fresh(3))
end func
""",
        {"loop_reassign": MUTATION, "loop_fresh": PURE},
        _lines(0, 9, 3),
    ),
    "for-and-match-bindings-reusing-the-name": Case(
        """
func for_reuse(rows: List[Array[Int]]) -> Int
    example [] -> 1
    var a = array_new(1, 0)
    a[0] = 1
    for a in rows do
        var a = a
        a[0] = 9
    end for
    return a[0]
end func

func match_reuse(o: Option[Array[Int]]) -> Int
    example None -> 1
    var a = array_new(1, 0)
    a[0] = 1
    match o with
        | Some(a) ->
            var a = a
            a[0] = 9
            return a[0]
        | None -> return a[0]
    end match
end func

func for_reads_only(rows: List[Array[Int]]) -> Int
    example [] -> 1
    var a = array_new(1, 0)
    a[0] = 1
    var t = 0
    for a in rows do
        t = t + a[0]
    end for
    return t + a[0]
end func

func main() -> Unit with io, mutation
    let r1 = array_new(1, 0)
    print(for_reuse([r1]))
    print(r1[0])
    let r2 = array_new(1, 0)
    print(match_reuse(Some(r2)))
    print(r2[0])
    print(for_reads_only([r1]))
end func
""",
        # A name that some binding in the unit takes from elsewhere is owned
        # nowhere in the unit, so even the outer fresh `a` is charged.
        {"for_reuse": MUTATION, "match_reuse": MUTATION, "for_reads_only": MUTATION},
        _lines(1, 9, 9, 9, 10),
    ),
    "redeclared-array-parameter-is-charged": Case(
        """
func redeclare(v: Array[Int]) -> Int
    example array_new(1, 0) -> 9
    var v = v
    v[0] = 9
    return v[0]
end func

func main() -> Unit with io, mutation
    let shared = array_new(1, 0)
    print(redeclare(shared))
    print(shared[0])
end func
""",
        {"redeclare": MUTATION},
        _lines(9, 9),
    ),
    "redeclarations-and-shadows-are-charged": Case(
        """
func same_block(p: Array[Int]) -> Int
    example array_new(1, 0) -> 9
    var a = array_new(1, 0)
    a[0] = 1
    var a = p
    a[0] = 9
    return a[0]
end func

func nested_shadow(p: Array[Int], c: Bool) -> Int
    example array_new(1, 0), false -> 1
    var a = array_new(1, 0)
    if c then
        var a = p
        a[0] = 9
    end if
    a[0] = 1
    return a[0]
end func

func if_initializer(p: Array[Int], c: Bool) -> Int
    example array_new(1, 0), true -> 9
    var a = if c then array_new(1, 0) else p end if
    a[0] = 9
    return a[0]
end func

func destructured(p: Array[Int]) -> Int
    example array_new(1, 0) -> 9
    var (a, b) = (array_new(1, 0), p)
    a[0] = 1
    b[0] = 9
    return b[0]
end func

func main() -> Unit with io, mutation
    let s1 = array_new(1, 0)
    print(same_block(s1))
    print(s1[0])
    let s2 = array_new(1, 0)
    print(nested_shadow(s2, true))
    print(s2[0])
    let s3 = array_new(1, 0)
    print(if_initializer(s3, false))
    print(s3[0])
    let s4 = array_new(1, 0)
    print(destructured(s4))
    print(s4[0])
end func
""",
        {
            "same_block": MUTATION,
            "nested_shadow": MUTATION,
            "if_initializer": MUTATION,
            "destructured": MUTATION,
        },
        _lines(9, 9, 1, 9, 9, 9, 9, 9),
    ),
    # --- two-dimensional tables --------------------------------------------
    "grid-writes-are-charged": Case(
        """
func aliased_rows(n: Int) -> Int
    example 0 -> 5
    var g = array_new(2, array_new(2, n))
    g[0][1] = 5
    return g[1][1]
end func

func param_row(row: Array[Int]) -> Int
    example array_new(2, 0) -> 7
    var g = array_new(2, row)
    g[0][0] = 7
    return g[1][0]
end func

func fresh_rows(n: Int) -> Int
    example 0 -> 0
    var g = array_new(2, array_new(2, n))
    g[0] = array_new(2, n)
    g[1] = array_new(2, n)
    g[0][1] = 5
    return g[1][1]
end func

func main() -> Unit with io, mutation
    print(aliased_rows(0))
    let row = array_new(2, 0)
    print(param_row(row))
    print(row[0])
    print(fresh_rows(0))
end func
""",
        # fresh_rows is private in fact, but proving it needs per-element
        # freshness (Unresolved Question 6).
        {"aliased_rows": MUTATION, "param_row": MUTATION, "fresh_rows": MUTATION},
        _lines(5, 7, 7, 0),
    ),
    # --- record fields over reference and value containers ------------------
    "writes-below-a-reference-field-are-charged": Case(
        """
func poke_cells(b: Board) -> Int
    example Board(cells: array_new(1, 0), pts: array_new(1, P(x: 0))) -> 9
    var c = b
    c.cells[0] = 9
    return c.cells[0]
end func

func poke_pts(b: Board) -> Int
    example Board(cells: array_new(1, 0), pts: array_new(1, P(x: 0))) -> 8
    var c = b
    c.pts[0].x = 8
    return c.pts[0].x
end func

func poke_wrapped(w: Wrap[Array[Int]]) -> Int
    example Wrap(v: array_new(1, 0)) -> 7
    var u = w
    u.v[0] = 7
    return u.v[0]
end func

func main() -> Unit with io, mutation
    let b = Board(cells: array_new(1, 0), pts: array_new(1, P(x: 0)))
    print(poke_cells(b))
    print(b.cells[0])
    print(poke_pts(b))
    print(b.pts[0].x)
    let w = Wrap(v: array_new(1, 0))
    print(poke_wrapped(w))
    print(w.v[0])
end func
""",
        {"poke_cells": MUTATION, "poke_pts": MUTATION, "poke_wrapped": MUTATION},
        _lines(9, 9, 8, 8, 7, 7),
    ),
    "writes-through-value-containers-are-masked": Case(
        """
func list_field(xs: List[P]) -> Int
    example [P(x: 1)] -> 9
    var ys = xs
    ys[0].x = 9
    return ys[0].x
end func

func bag_field(b: Bag) -> Int
    example Bag(pts: [P(x: 1)]) -> 9
    var c = b
    c.pts[0].x = 9
    return c.pts[0].x
end func

func tuple_field(t: (P, Int)) -> Int
    example (P(x: 1), 2) -> 9
    var u = t
    u.0.x = 9
    return u.0.x
end func

func map_field(m: Map[String, P]) -> Int
    example {"a": P(x: 1)} -> 9
    var n = m
    n["a"].x = 9
    return n["a"].x
end func

func nested_field(b: Box) -> Int
    example Box(p: P(x: 1)) -> 9
    var c = b
    c.p.x = 9
    return c.p.x
end func

func wrapped_value(w: Wrap[P]) -> Int
    example Wrap(v: P(x: 1)) -> 9
    var u = w
    u.v.x = 9
    return u.v.x
end func

func replace_reference_field(h: Holder) -> Int
    example Holder(cells: array_new(1, 0)) -> 7
    var g = h
    g.cells = array_new(1, 7)
    return g.cells[0]
end func

func element_of_shared_array(a: Array[P]) -> Int
    example array_from_list([P(x: 1)]) -> 9
    var q = a[0]
    q.x = 9
    return q.x
end func

func main() -> Unit with io
    let xs = [P(x: 1)]
    print(list_field(xs))
    print(xs[0].x)
    let b = Bag(pts: [P(x: 1)])
    print(bag_field(b))
    print(b.pts[0].x)
    let t = (P(x: 1), 2)
    print(tuple_field(t))
    print(t.0.x)
    let m = {"a": P(x: 1)}
    print(map_field(m))
    print(m["a"].x)
    let bx = Box(p: P(x: 1))
    print(nested_field(bx))
    print(bx.p.x)
    let w = Wrap(v: P(x: 1))
    print(wrapped_value(w))
    print(w.v.x)
    let h = Holder(cells: array_new(1, 0))
    print(replace_reference_field(h))
    print(h.cells[0])
    let a = array_from_list([P(x: 1)])
    print(element_of_shared_array(a))
    print(a[0].x)
end func
""",
        {
            "list_field": PURE,
            "bag_field": PURE,
            "tuple_field": PURE,
            "map_field": PURE,
            "nested_field": PURE,
            "wrapped_value": PURE,
            "replace_reference_field": PURE,
            "element_of_shared_array": PURE,
            "main": IO,
        },
        _lines(9, 1, 9, 1, 9, 1, 9, 1, 9, 1, 9, 1, 7, 0, 9, 1),
    ),
    "lists-and-arrays-of-records-and-arrays": Case(
        """
func owned_records(xs: List[P]) -> Int
    example [P(x: 1)] -> 9
    var a = array_from_list(xs)
    a[0].x = 9
    return a[0].x
end func

func owned_record_reference_field(shared: Array[Int]) -> Int
    example array_new(1, 0) -> 7
    var a = array_new(2, Holder(cells: shared))
    a[0].cells = array_new(1, 7)
    return a[0].cells[0]
end func

func shared_record_reference_field(shared: Array[Int]) -> Int
    example array_new(1, 0) -> 9
    var a = array_new(2, Holder(cells: shared))
    a[0].cells[0] = 9
    return a[1].cells[0]
end func

func list_of_arrays(rows: List[Array[Int]]) -> Int
    example [array_new(1, 0)] -> 9
    var ys = rows
    ys[0][0] = 9
    return ys[0][0]
end func

func main() -> Unit with io, mutation
    let xs = [P(x: 1)]
    print(owned_records(xs))
    print(xs[0].x)
    let s1 = array_new(1, 0)
    print(owned_record_reference_field(s1))
    print(s1[0])
    let s2 = array_new(1, 0)
    print(shared_record_reference_field(s2))
    print(s2[0])
    let rows = [array_new(1, 0)]
    print(list_of_arrays(rows))
    print(rows[0][0])
end func
""",
        {
            "owned_records": PURE,
            "owned_record_reference_field": PURE,
            "shared_record_reference_field": MUTATION,
            "list_of_arrays": MUTATION,
        },
        _lines(9, 1, 7, 0, 9, 9, 9, 9),
    ),
    # --- fresh Vec and MutableMap index writes ------------------------------
    "fresh-vec-and-mutable-map-index-writes": Case(
        """
func word_counts(words: List[String]) -> Int
    example ["a", "b", "a"] -> 2
    var m: MutableMap[String, Int] = mutable_map_new()
    for w in words do
        m[w] = 1
    end for
    return mutable_map_size(m)
end func

func first_doubled(xs: List[Int]) -> Int
    example [3] -> 6
    var v = vec_from_list(xs)
    v[0] = vec_get(v, 0) * 2
    return vec_get(v, 0)
end func

func pushed(n: Int) -> Int
    example 4 -> 5
    var v: Vec[Int] = vec_new()
    vec_push(v, n)
    v[0] = n + 1
    return vec_get(v, 0)
end func

func alias_map(m: MutableMap[String, Int]) -> Int
    example mutable_map_new() -> 9
    var n = m
    n["a"] = 9
    return mutable_map_size(n) * 9
end func

func alias_vec(v: Vec[Int]) -> Int
    example vec_from_list([0]) -> 9
    var w = v
    w[0] = 9
    return vec_get(w, 0)
end func

func main() -> Unit with io, mutation
    print(word_counts(["a", "b", "a"]))
    print(word_counts(["a", "b", "a"]))
    let xs = [3]
    print(first_doubled(xs))
    print(xs)
    print(pushed(4))
    let m: MutableMap[String, Int] = mutable_map_new()
    print(alias_map(m))
    print(mutable_map_get(m, "a"))
    let v = vec_from_list([0])
    print(alias_vec(v))
    print(vec_get(v, 0))
end func
""",
        {
            "word_counts": PURE,
            "first_doubled": PURE,
            "pushed": PURE,
            "alias_map": MUTATION,
            "alias_vec": MUTATION,
        },
        _lines(2, 2, 6, "[3]", 5, 9, "Some(value: 9)", 9, 9),
    ),
    "copies-and-set-algebra-are-fresh": Case(
        """
func copied(p: Array[Int]) -> Int
    example array_new(1, 0) -> 9
    var a = array_copy(p)
    a[0] = 9
    return a[0]
end func

func from_list(xs: List[Int]) -> Int
    example [1] -> 9
    var a = array_from_list(xs)
    a[0] = 9
    var v = vec_from_list(xs)
    v[0] = 9
    return a[0] * vec_get(v, 0) / 9
end func

func unioned(a: Set[Int], b: Set[Int]) -> Int
    example set_new(), set_new() -> 1
    var s = set_union(a, b)
    set_add(s, 9)
    var t = set_intersection(a, b)
    set_add(t, 9)
    return set_size(s)
end func

func main() -> Unit with io
    let p = array_from_list([1])
    print(copied(p))
    print(p[0])
    let xs = [1]
    print(from_list(xs))
    print(xs)
    let a: Set[Int] = set_from_list([1])
    let b: Set[Int] = set_new()
    print(unioned(a, b))
    print(unioned(b, a))
    print(set_contains(a, 9))
    print(set_size(b))
end func
""",
        {"copied": PURE, "from_list": PURE, "unioned": PURE, "main": IO},
        _lines(9, 1, 9, "[1]", 2, 2, "false", 0),
    ),
    # --- escapes into other storage -----------------------------------------
    "rolling-buffer-is-charged": Case(
        """
func rolling(n: Int) -> Int
    example 1 -> 3
    var prev = array_new(n, 0)
    var cur = array_new(n, 0)
    var i = 0
    while i < 3 do
        prev = cur
        cur = array_new(n, 0)
        cur[0] = prev[0] + 1
        i = i + 1
    end while
    return cur[0]
end func

func main() -> Unit with io, mutation
    print(rolling(1))
end func
""",
        # Conservative: `prev = cur` copies the reference.
        {"rolling": MUTATION},
        _lines(3),
    ),
    "fresh-collection-passed-to-a-function-is-charged": Case(
        """
func peek_first(a: Array[Int]) -> Int
    example array_new(1, 4) -> 4
    return a[0]
end func

@untested("stores its argument")
func keep_array(store: Vec[Array[Int]], a: Array[Int]) -> Int
    vec_push(store, a)
    return 0
end func

func pass_then_write(n: Int) -> Int
    example 1 -> 10
    var a = array_new(1, n)
    let before = peek_first(a)
    a[0] = 9
    return before + a[0]
end func

@untested("stores into its argument")
func lend_then_write(store: Vec[Array[Int]]) -> Int
    var a = array_new(1, 0)
    let k = keep_array(store, a)
    a[0] = 9
    return k
end func

func main() -> Unit with io, mutation
    print(pass_then_write(1))
    let store: Vec[Array[Int]] = vec_new()
    print(lend_then_write(store))
    print(vec_get(store, 0)[0])
end func
""",
        # pass_then_write is conservative; lend_then_write's write is seen by
        # the caller through the helper's store.
        {"pass_then_write": MUTATION, "lend_then_write": MUTATION},
        _lines(10, 0, 9),
    ),
    "nested-lambda-writing-the-enclosing-lambdas-var": Case(
        """
func nested(xs: List[Int]) -> Int
    example [1, 2] -> 3
    let f = fn(ys: List[Int]) do
        var acc = 0
        let add = fn(y: Int) do
            acc = acc + y
            return acc
        end fn
        for y in ys do
            let seen = add(y)
        end for
        return acc
    end fn
    return f(xs)
end func

func main() -> Unit with io, mutation
    print(nested([1, 2]))
    print(nested([1, 2]))
end func
""",
        {"nested": MUTATION},
        _lines(3, 3),
    ),
    # --- async, generic, impl and recursive units ---------------------------
    "async-functions": Case(
        """
async func async_total(xs: List[Int]) -> Int
    var s = 0
    for x in xs do
        s = s + x
    end for
    return s
end func

async func async_poke(a: Array[Int]) -> Int
    var b = a
    b[0] = 9
    return b[0]
end func

func main() -> Unit with io, mutation
    print(await async_total([1, 2, 3]))
    let shared = array_new(1, 0)
    print(await async_poke(shared))
    print(shared[0])
end func
""",
        {"async_total": PURE, "async_poke": MUTATION},
        _lines(6, 9, 9),
    ),
    "generic-higher-order-calls": Case(
        """
func apply_to[T, U](f: (T) -> U, x: T) -> U
    example fn(n: Int) -> n + 1, 1 -> 2
    return f(x)
end func

func total(xs: List[Int]) -> Int
    example [1, 2] -> 3
    var s = 0
    for x in xs do
        s = s + x
    end for
    return s
end func

func overwrite[T](a: Array[T], value: T) -> Int
    example array_new(1, 0), 9 -> 1
    var b = a
    b[0] = value
    return 1
end func

func main() -> Unit with io, mutation
    let xs = [1, 2, 3]
    print(apply_to(total, xs))
    print(apply_to(fn(ys: List[Int]) do
        var n = 0
        for y in ys do
            n = n + 1
        end for
        return n
    end fn, xs))
    print(xs)
    let shared = array_new(1, 0)
    print(overwrite(shared, 9))
    print(shared[0])
end func
""",
        {"apply_to": PURE, "total": PURE, "overwrite": MUTATION},
        _lines(6, 3, "[1, 2, 3]", 1, 9),
    ),
    "impl-methods-and-recursion": Case(
        """
type Counter = Counter(n: Int)

trait Tally
    func tally(self: Self, xs: List[Int]) -> Int
end trait

impl Tally for Counter
    func tally(self: Counter, xs: List[Int]) -> Int
        example Counter(n: 1), [2, 3] -> 6
        var c = self
        for x in xs do
            c.n = c.n + x
        end for
        return c.n
    end func
end impl

func depth_sum(n: Int) -> Int
    example 3 -> 6
    var a = array_new(1, n)
    if n > 0 then
        a[0] = a[0] + depth_sum(n - 1)
    end if
    return a[0]
end func

func main() -> Unit with io
    let c = Counter(n: 1)
    print(tally(c, [2, 3]))
    print(tally(c, [2, 3]))
    print(c)
    print(depth_sum(3))
end func
""",
        {"depth_sum": PURE, "main": IO},
        _lines(6, 6, "Counter(n: 1)", 6),
    ),
    "user-function-named-array-new-is-not-fresh": Case(
        """
func array_new(size: Int, row: Array[Int]) -> Array[Int]
    example 1, array_from_list([0]) -> array_from_list([0])
    return row
end func

func through_user_array_new(row: Array[Int]) -> Int
    example array_from_list([0]) -> 9
    var a = array_new(1, row)
    a[0] = 9
    return a[0]
end func

func main() -> Unit with io, mutation
    let row = array_from_list([0])
    print(through_user_array_new(row))
    print(row[0])
end func
""",
        {"array_new": PURE, "through_user_array_new": MUTATION},
        _lines(9, 9),
    ),
    "shallow-copying-builtins-then-a-value-write": Case(
        # The builtins copy only the top level, so their elements are the
        # caller's records; the `var` binding still snapshots them.
        """
func via_array_to_list(a: Array[P]) -> Int
    example array_from_list([P(x: 1)]) -> 9
    let l = array_to_list(a)
    var ys = l
    ys[0].x = 9
    return ys[0].x
end func

func via_vec_to_list(v: Vec[P]) -> Int
    example vec_from_list([P(x: 1)]) -> 9
    var ys = vec_to_list(v)
    ys[0].x = 9
    return ys[0].x
end func

func via_map_values(m: Map[String, P]) -> Int
    example {"a": P(x: 1)} -> 9
    var vs = map_values(m)
    vs[0].x = 9
    return vs[0].x
end func

func via_owned_then_list(xs: List[P]) -> List[P]
    example [P(x: 1)] -> [P(x: 1)]
    var a = array_from_list(xs)
    let l = array_to_list(a)
    a[0].x = 9
    return l
end func

func main() -> Unit with io
    let a = array_from_list([P(x: 1)])
    print(via_array_to_list(a))
    print(a[0].x)
    let v = vec_from_list([P(x: 1)])
    print(via_vec_to_list(v))
    print(vec_get(v, 0).x)
    let m = {"a": P(x: 1)}
    print(via_map_values(m))
    print(m)
    print(via_owned_then_list([P(x: 1)]))
end func
""",
        {
            "via_array_to_list": PURE,
            "via_vec_to_list": PURE,
            "via_map_values": PURE,
            "via_owned_then_list": PURE,
            "main": IO,
        },
        _lines(9, 1, 9, 1, 9, '{"a": P(x: 1)}', "[P(x: 1)]"),
    ),
    "array-to-list-snapshots-an-owned-array": Case(
        """
func before_and_after(n: Int) -> (List[Int], List[Int])
    example 2 -> ([0, 0], [9, 0])
    var a = array_new(n, 0)
    let before = array_to_list(a)
    a[0] = 9
    return (before, array_to_list(a))
end func

func main() -> Unit with io
    print(before_and_after(2))
    print(before_and_after(2))
end func
""",
        {"before_and_after": PURE, "main": IO},
        _lines("([0, 0], [9, 0])", "([0, 0], [9, 0])"),
    ),
    # A pipeline passes its value as an argument of the stage call (spec 7.7),
    # so piping an owned array into a builtin that only borrows it is not an
    # escape; piping it into a user function is.
    "pipeline-into-a-borrowing-builtin-is-masked": Case(
        """
func piped(n: Int) -> (List[Int], List[Int])
    example 2 -> ([0, 0], [9, 0])
    var a = array_new(n, 0)
    let before = a |> array_to_list
    let size = a |> array_length
    a[0] = 9 + size - n
    return (before, a |> array_copy |> array_to_list)
end func

func peek_first(a: Array[Int]) -> Int
    example array_new(1, 4) -> 4
    return a[0]
end func

func piped_to_user(n: Int) -> Int
    example 1 -> 10
    var a = array_new(1, n)
    let before = a |> peek_first
    a[0] = 9
    return before + a[0]
end func

func main() -> Unit with io, mutation
    print(piped(2))
    print([1, 2].map(piped))
    print(piped_to_user(1))
end func
""",
        {"piped": PURE, "piped_to_user": MUTATION, "main": IO_MUTATION},
        _lines(
            "([0, 0], [9, 0])",
            "[([0], [9]), ([0, 0], [9, 0])]",
            10,
        ),
    ),
}


# Writes a caller can observe although the checker infers no `mutation` for
# the writing function, and runtime aliases that change what a masked function
# computes. Each is current behaviour; all three backends agree unless the
# case pins a separate `js_output` (a pre-existing JS runtime parity bug).
KNOWN_GAPS: dict[str, Case] = {
    # Spec 3.5 known limitation: mutating builtins are not charged on any
    # receiver (Unresolved Question 1).
    "mutating-builtin-on-a-parameter": Case(
        """
@untested("writes its argument")
func push_param(v: Vec[Int]) -> Int
    vec_push(v, 1)
    return 0
end func

func main() -> Unit with io
    let v: Vec[Int] = vec_new()
    let r = push_param(v)
    print(vec_length(v))
end func
""",
        {"push_param": PURE, "main": IO},
        _lines(1),
    ),
    # The same limitation, in the shape of tetris `lock_piece`/`clear_lines`
    # and notetaker `update`: a function that writes its caller's array only
    # through a mutating builtin. 0.6 charged it, but only because its loop
    # counter is a local `var`; 0.7 masks the counter, so it is inferred pure
    # and is accepted as a pure callback. The repository's own functions of
    # this shape declare `with mutation`. Closing the limitation flips this.
    "builtin-writer-with-a-local-counter": Case(
        """
@untested("writes its argument")
func fill(grid: Array[Int]) -> Unit
    var i = 0
    while i < 2 do
        array_set(array: grid, index: i, value: 9)
        i = i + 1
    end while
end func

@untested("calls back")
func run_pure(f: (Array[Int]) -> Unit, a: Array[Int]) -> Unit
    f(a)
end func

func main() -> Unit with io
    let a: Array[Int] = array_new(2, 0)
    run_pure(fill, a)
    print(a[0])
end func
""",
        {"fill": PURE, "run_pure": PURE, "main": IO},
        _lines(9),
    ),
    # Section 6, widening 1: a builtin stores a closure over one of the
    # writer's cells into caller storage; the later masked write is seen.
    "widening-closure-over-a-cell": Case(
        """
@untested("stores a closure into its argument")
func stash(store: Vec[() -> Int]) -> Int
    var n = 1
    vec_push(store, fn() -> n)
    n = 5
    return 0
end func

@untested("stores a closure into its argument")
func stash_field(store: Vec[() -> Int]) -> Int
    var q = P(x: 1)
    vec_push(store, fn() -> q.x)
    q.x = 6
    return 0
end func

func main() -> Unit with io
    let store: Vec[() -> Int] = vec_new()
    let r = stash(store)
    let s = stash_field(store)
    let first = vec_get(store, 0)
    let second = vec_get(store, 1)
    print(first())
    print(second())
end func
""",
        {"stash": PURE, "stash_field": PURE, "main": IO},
        _lines(5, 6),
    ),
    # Section 6, widening 2: a callee builds a closure over a `var`-held
    # value (argument passing does not snapshot), and a pure callback stores
    # it with a builtin.
    "widening-callee-closure-stored-by-a-pure-callback": Case(
        """
@untested("returns a closure")
func keep(q: P) -> () -> Int
    return fn() -> q.x
end func

@untested("calls back")
func record(cb: (() -> Int) -> Unit, start: Int) -> Int
    var s = P(x: start)
    cb(keep(s))
    s.x = 7
    return s.x
end func

func main() -> Unit with io
    let store: Vec[() -> Int] = vec_new()
    let r = record(fn(g: () -> Int) do
        vec_push(store, g)
    end fn, 1)
    let peek = vec_get(store, 0)
    print(r)
    print(peek())
end func
""",
        {"keep": PURE, "record": PURE, "main": IO},
        _lines(7, 7),
    ),
    # Unresolved Question 7: passing a `var`-held constructor value does not
    # snapshot it, so a closure the callee builds sees later field writes.
    # The writer stays pure; the alias only changes what it computes itself.
    "argument-passing-alias": Case(
        """
@untested("returns a closure")
func keep(q: P) -> () -> Int
    return fn() -> q.x
end func

@untested("returns a closure")
func keep_list(ys: List[P]) -> () -> Int
    return fn() -> ys[0].x
end func

func alias_record(start: Int) -> Int
    example 1 -> 7
    var s = P(x: start)
    let peek = keep(s)
    s.x = 7
    return peek()
end func

func alias_list(start: Int) -> Int
    example 1 -> 5
    var ys = [P(x: start)]
    let g = keep_list(ys)
    ys[0].x = 5
    return g()
end func

func main() -> Unit with io
    var s = P(x: 1)
    let peek = keep(s)
    s.x = 7
    print(peek())
    print(alias_record(1))
    print(alias_list(1))
end func
""",
        {"alias_record": PURE, "alias_list": PURE, "main": IO},
        _lines(7, 7, 5),
    ),
    # Unresolved Question 7, compiled-JavaScript parity bug: an element or
    # field read passed as an argument hands the callee the stored object on
    # JS only, so the callee's closure sees a later in-place write through
    # the slot. The interpreter and compiled Python pass an independent value
    # (binding the same read with `let` snapshots it on every backend). Each
    # writer is pure on every backend; on JS it computes a different result,
    # and its `example` (checked on the interpreter) does not hold there.
    "js-element-and-field-argument-alias": Case(
        """
@untested("returns a closure")
func keep(q: P) -> () -> Int
    return fn() -> q.x
end func

func array_element(n: Int) -> Int
    example 5 -> 0
    var a = array_new(1, P(x: 0))
    let peek = keep(a[0])
    a[0].x = n
    return peek()
end func

func list_element(n: Int) -> Int
    example 5 -> 0
    var ys = [P(x: 0)]
    let peek = keep(ys[0])
    ys[0].x = n
    return peek()
end func

func field_read(n: Int) -> Int
    example 5 -> 0
    var b = Box(p: P(x: 0))
    let peek = keep(b.p)
    b.p.x = n
    return peek()
end func

func let_snapshot(n: Int) -> Int
    example 5 -> 0
    var a = array_new(1, P(x: 0))
    let q = a[0]
    a[0].x = n
    return q.x
end func

func main() -> Unit with io
    print(array_element(5))
    print(list_element(5))
    print(field_read(5))
    print(let_snapshot(5))
    var ys = [P(x: 0)]
    let peek = keep(ys[0])
    ys[0].x = 5
    print(peek())
end func
""",
        {
            "array_element": PURE,
            "list_element": PURE,
            "field_read": PURE,
            "let_snapshot": PURE,
            "main": IO,
        },
        _lines(0, 0, 0, 0, 0),
        js_output=_lines(5, 5, 5, 0, 5),
    ),
    # Unresolved Question 7, compiled-JavaScript parity bug: a `for` loop over
    # an `Array` iterates it live on JS and binds each element without a
    # copy. The interpreter and compiled Python iterate a snapshot of the
    # elements and bind each one independently. Iterating and writing an
    # owned array is masked (the iterable position and the value-hop writes),
    # so `seen` passes as a pure callback on every backend, and its writes
    # stay private, but on JS it returns a different list.
    "js-array-for-loop-iterates-live": Case(
        """
func seen(xs: List[Int]) -> List[Int]
    example [1, 2, 3] -> [1, 2, 3]
    var a = array_from_list(xs)
    var out: List[Int] = []
    for x in a do
        a[2] = 99
        out = out + [x]
    end for
    return out
end func

func seen_fields(n: Int) -> List[P]
    example 5 -> [P(x: 0), P(x: 0)]
    var a = array_new(2, P(x: 0))
    var out: List[P] = []
    for p in a do
        a[1].x = n
        out = out + [p]
    end for
    return out
end func

func apply(f: (List[Int]) -> List[Int], xs: List[Int]) -> List[Int]
    example fn(ys: List[Int]) -> ys, [1] -> [1]
    return f(xs)
end func

func main() -> Unit with io
    let xs = [1, 2, 3]
    print(apply(seen, xs))
    print(seen_fields(5))
    print(xs)
end func
""",
        {"seen": PURE, "seen_fields": PURE, "apply": PURE, "main": IO},
        _lines("[1, 2, 3]", "[P(x: 0), P(x: 0)]", "[1, 2, 3]"),
        js_output=_lines("[1, 2, 99]", "[P(x: 0), P(x: 5)]", "[1, 2, 3]"),
    ),
}


ALL_CASES = {**CASES, **KNOWN_GAPS}


def _checked(source: str) -> TypeChecker:
    checker = TypeChecker()
    checker.check_program(parse(source))
    return checker


def _effects(checker: TypeChecker, name: str) -> frozenset[str]:
    func_type = checker.global_env.lookup(name)
    assert isinstance(func_type, FuncType), name
    effects: frozenset[str] = func_type.effects
    return effects


@pytest.mark.parametrize("case_id", sorted(ALL_CASES))
def test_the_program_checks_and_infers_the_pinned_effects(case_id: str) -> None:
    case = ALL_CASES[case_id]
    source = TYPES + case.source
    result = check(source)
    assert result.ok, [d.message for d in result.diagnostics]
    checker = _checked(source)
    inferred = {name: _effects(checker, name) for name in case.effects}
    assert inferred == case.effects


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("case_id", sorted(ALL_CASES))
def test_every_backend_prints_the_pinned_caller_state(
    case_id: str, backend: Callable[[str], str]
) -> None:
    case = ALL_CASES[case_id]
    expected = case.output
    if backend is _compiled_js_output and case.js_output is not None:
        expected = case.js_output
    assert backend(TYPES + case.source) == expected


def test_only_known_gaps_pin_a_separate_javascript_output() -> None:
    # A masked shape in CASES must print the same on every backend; a
    # JavaScript-only result belongs with the pinned runtime parity bugs.
    assert all(case.js_output is None for case in CASES.values())
    pinned = {case_id for case_id, case in KNOWN_GAPS.items() if case.js_output}
    assert pinned == {
        "js-array-for-loop-iterates-live",
        "js-element-and-field-argument-alias",
    }
    for case_id in pinned:
        assert KNOWN_GAPS[case_id].js_output != KNOWN_GAPS[case_id].output


def test_the_impl_method_with_a_record_copy_is_pure() -> None:
    checker = _checked(TYPES + CASES["impl-methods-and-recursion"].source)
    assert checker.impl_registry[("Tally", "Counter")]["tally"].effects == PURE


# The repository's functions that write their caller's array only through a
# mutating builtin (`builtin-writer-with-a-local-counter` above). 0.6 charged
# each one because it also updates a local `var`; under 0.7 that alone would
# leave them pure, so they declare `with mutation`, which restores their 0.6
# types (and `mutation` on tetris `update`, which calls two of them).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BUILTIN_WRITERS = {
    "examples/apps/tetris.geno": {
        "lock_piece": "func lock_piece(grid: Array[Int], px: Int, py: Int, "
        "piece: Int, rot: Int) -> Unit with mutation\n",
        "clear_lines": "func clear_lines(grid: Array[Int]) -> Int with mutation\n",
    },
    "examples/apps/notetaker.geno": {
        "update": "func update(state: State, dt: Float) -> State with mutation\n",
    },
}


@pytest.mark.parametrize("path", sorted(_BUILTIN_WRITERS))
def test_repository_builtin_writers_declare_mutation(path: str) -> None:
    source = (_REPO_ROOT / path).read_text(encoding="utf-8")
    checker = _checked(source)
    for name, header in _BUILTIN_WRITERS[path].items():
        assert header in source, name
        assert "mutation" in _effects(checker, name), name
    if path.endswith("tetris.geno"):
        assert _effects(checker, "update") == frozenset({"mutation", "random"})


@pytest.mark.parametrize("path", sorted(_BUILTIN_WRITERS))
def test_without_the_declaration_a_builtin_writer_infers_pure(path: str) -> None:
    # The known limitation on real code: drop the declarations and the
    # checker accepts these functions as pure. Closing the limitation flips
    # this test deliberately; until then the declarations carry the effect.
    source = (_REPO_ROOT / path).read_text(encoding="utf-8")
    for header in _BUILTIN_WRITERS[path].values():
        source = source.replace(header, header.replace(" with mutation", ""), 1)
    checker = _checked(source)
    for name in _BUILTIN_WRITERS[path]:
        assert _effects(checker, name) == PURE, name


# ---------------------------------------------------------------------------
# Checker-only cases
# ---------------------------------------------------------------------------

# Every way a local binding can take a builtin's name. The call is then not
# fresh, so the write through its result stays charged. Compiled JavaScript
# refuses a local named after a runtime builtin, so these are checker-only.
LOCAL_ARRAY_NEW_SHADOWS: dict[str, str] = {
    "parameter": """
func f(array_new: (Int, Int) -> Array[Int]) -> Int
    example fn(n: Int, v: Int) -> array_new(n, v) -> 9
    var a = array_new(1, 0)
    a[0] = 9
    return a[0]
end func
""",
    "let in a nested block": """
func f(mk: (Int, Int) -> Array[Int], c: Bool) -> Int
    example fn(n: Int, v: Int) -> array_new(n, v), true -> 9
    if c then
        let array_new = mk
        var a = array_new(1, 0)
        a[0] = 9
        return a[0]
    end if
    return 0
end func
""",
    "destructure": """
func f(mk: (Int, Int) -> Array[Int]) -> Int
    example fn(n: Int, v: Int) -> array_new(n, v) -> 9
    let (array_new, k) = (mk, 1)
    var a = array_new(1, 0)
    a[0] = 9
    return a[0]
end func
""",
    "for variable": """
func f(makers: List[(Int, Int) -> Array[Int]]) -> Int
    example [fn(n: Int, v: Int) -> array_new(n, v)] -> 9
    var t = 0
    for array_new in makers do
        var a = array_new(1, 0)
        a[0] = 9
        t = a[0]
    end for
    return t
end func
""",
    "constructor pattern": """
func f(o: Option[(Int, Int) -> Array[Int]]) -> Int
    example Some(fn(n: Int, v: Int) -> array_new(n, v)) -> 9
    match o with
        | Some(array_new) ->
            var a = array_new(1, 0)
            a[0] = 9
            return a[0]
        | None -> return 0
    end match
end func
""",
    "list pattern": """
func f(makers: List[(Int, Int) -> Array[Int]]) -> Int
    example [fn(n: Int, v: Int) -> array_new(n, v)] -> 9
    match makers with
        | [array_new, ...rest] ->
            var a = array_new(1, 0)
            a[0] = 9
            return a[0]
        | _ -> return 0
    end match
end func
""",
    "tuple pattern": """
func f(p: ((Int, Int) -> Array[Int], Int)) -> Int
    example (fn(n: Int, v: Int) -> array_new(n, v), 1) -> 9
    match p with
        | (array_new, k) ->
            var a = array_new(1, 0)
            a[0] = 9
            return a[0]
    end match
end func
""",
    "lambda parameter": """
func f(mk: (Int, Int) -> Array[Int]) -> Int
    example fn(n: Int, v: Int) -> array_new(n, v) -> 9
    let g = fn(array_new: (Int, Int) -> Array[Int]) do
        var a = array_new(1, 0)
        a[0] = 9
        return a[0]
    end fn
    return g(mk)
end func
""",
    "let inside a lambda": """
func f(mk: (Int, Int) -> Array[Int]) -> Int
    example fn(n: Int, v: Int) -> array_new(n, v) -> 9
    let g = fn() do
        let array_new = mk
        var a = array_new(1, 0)
        a[0] = 9
        return a[0]
    end fn
    return g()
end func
""",
}


@pytest.mark.parametrize("kind", sorted(LOCAL_ARRAY_NEW_SHADOWS))
def test_a_local_named_array_new_is_never_fresh(kind: str) -> None:
    source = TYPES + LOCAL_ARRAY_NEW_SHADOWS[kind]
    assert check(source).ok
    assert _effects(_checked(source), "f") == MUTATION


@pytest.mark.parametrize(
    ("callee", "code"),
    [
        # A function writing through a parameter alias is not a pure callback.
        ("bump", "E300"),
        # Nor is a lambda that writes a variable it captured.
        (
            "fn(xs: List[Int]) do\n        hits = hits + 1\n        return 0\n    end fn",
            "E300",
        ),
    ],
    ids=["parameter-alias", "captured-var"],
)
def test_a_charged_callback_is_still_rejected(callee: str, code: str) -> None:
    source = (
        TYPES
        + """
func bump(xs: List[Int]) -> Int
    example [1] -> 1
    var cells = array_from_list(xs)
    var alias = cells
    alias[0] = 1
    return cells[0]
end func

func apply_to[T, U](f: (T) -> U, x: T) -> U
    example fn(n: Int) -> n + 1, 1 -> 2
    return f(x)
end func

func main() -> Unit with io, mutation
    var hits = 0
    print(apply_to("""
        + callee
        + """, [1]))
end func
"""
    )
    codes = [d.code.value for d in check(source).diagnostics if d.code is not None]
    assert code in codes


def test_masking_does_not_hide_a_charged_call_from_a_declared_effect_set() -> None:
    source = (
        TYPES
        + """
func poke(a: Array[Int]) -> Int
    example array_new(1, 0) -> 9
    var b = a
    b[0] = 9
    return b[0]
end func

func main() -> Unit with io
    var local = array_new(1, 0)
    local[0] = poke(array_new(1, 0))
    print(local[0])
end func
"""
    )
    diagnostics = check(source).diagnostics
    assert [d.code.value for d in diagnostics if d.code is not None] == ["E310"]
    assert "undeclared effects: mutation" in diagnostics[0].message
