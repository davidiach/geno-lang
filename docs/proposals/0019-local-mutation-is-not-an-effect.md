# Proposal 0019: Local Mutation Is Not an Effect

- Status: Draft
- Authors: Claude Code (drafted and implemented at the project lead's request)
- Created: 2026-11-05
- Discussion: https://github.com/davidiach/geno-lang/pull/233
- Supersedes: None
- Review: None yet; awaiting project lead acceptance under `GOVERNANCE.md`.
- Target: the 0.7 development line (`docs/spec/v0.7.md`, Draft)

## Summary

A function that changes only state its own call created is pure. Its callers
cannot see that state, so its type no longer says `with mutation`:

```geno
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
    print(apply(total, [4, 5, 6]))
    print([[1, 2], [3]].map(total))
end func
```

0.6 types `total` as `(List[Int]) -> Int with mutation`. It therefore rejects
`apply(total, ...)` and `.map(total)` with E300, and rejects `main` with E310
as soon as `main` declares a local `var`. Under this proposal all three are
accepted.

The rule is applied at every function and lambda boundary. The following do not
contribute `mutation` to the function or lambda doing them:

- declaring a `var`;
- assigning one of the unit's own bindings;
- writing a field or element of a value that the unit's own binding holds;
- writing an element of an `Array`, `Vec` or `MutableMap` that the unit
  allocated and never let escape.

Everything a caller can observe still contributes `mutation`. That covers
writes to a binding captured from an enclosing scope (charged to the lambda
that writes it), writes through a parameter's reference collection, writes
below a reference-typed field or element, and writes to a collection that has
been stored, passed or captured elsewhere. The reference checker still does
not charge mutating builtins such as `vec_push` on any receiver, as in 0.6.
The spec keeps that as a non-normative known limitation, and closing it
needs the project lead's decision (Unresolved Question 1).

Effects stay a static property. No backend changes, no syntax changes, no
diagnostic code changes, and declared `with mutation` annotations stay legal.

## Motivation

Proposal [0003](0003-geno-0.6-language-upgrade.md) recorded this as a finding
during 0.6 ("Local mutation is an effect") and deferred it:

> A function that only mutates its own `var` locals is typed `with mutation`,
> so it cannot be passed where a pure function type is expected, although
> callers cannot observe the mutation. Masking effects on state that does not
> escape (as Koka does) belongs with declared effects on trait methods in a
> later effect-system milestone.

The probes behind this proposal reproduce it on 0.6.0:

- **Higher-order calls fail.** `apply(total, xs)`, `map(xs, total)`,
  `xs.map(total)` and `xs |> map(_, total)` each fail with one E300, for example
  "Argument 1 type mismatch: expected (List[Int]) -> Int, got (List[Int]) ->
  Int with mutation". A lambda passed to `map` that sums into its own `var`
  fails the same way.
- **`main` needs a second effect.** `func main() -> Unit with io` with a single
  local `var` fails with E310 "performs undeclared effects: mutation".
- **Declaring is enough.** A function whose only `var` is never assigned
  (`var z = n; return z`) is typed `with mutation`.

Local accumulators are the most common shape in model-written loops. The extra
effect leaks into every signature that contains one, and from there into every
higher-order use of it. Callers gain nothing from that, because none of them
can observe the state.

The rule covers scalar and record accumulators, value-typed working copies
(`var q = p; q.x = ...`), and one-dimensional collections the function
allocates (histograms, sieves, 1-D dynamic-programming tables, a local `Vec`
or `MutableMap` used as a worklist or memo). It deliberately does not cover
several shapes that models also write, and those keep `mutation`:

- two-dimensional tables `var g = array_new(h, array_new(w, 0)); g[i][j] = v`.
  The rows alias each other and the argument row on every backend, so the
  write really is visible through other storage;
- rolling buffers (`prev = cur; cur = array_new(n, 0)`), because `prev = cur`
  copies the reference;
- an accumulator updated through a user higher-order function
  (`each(xs, fn(x: Int) do total = total + x end fn)`), because the lambda
  writes a captured variable;
- a fresh collection passed to a helper function and then written.

The probes also showed that 0.6 does not do what spec section 3.5 says. These
are the only sources of `mutation` (`_infer_stmt_effects` in
`geno/typechecker.py`):

| Construct | 0.6 |
| --- | --- |
| `var x = e`, `var (a, b) = e` | `mutation`, even if never assigned |
| `x = e` | `mutation` |
| `t.f = e` | `mutation` |
| `t[i] = e` | `mutation` |
| calling a function or lambda | the callee type's effects |
| `vec_push`, `vec_set`, `vec_pop`, `array_set`, `array_fill`, `set_add`, `set_remove`, `mutable_map_set`, `mutable_map_delete` | nothing, whatever the receiver |

Builtin effects come only from capabilities (`CAPABILITY_TO_EFFECT` in
`geno/builtin_registry.py`). No builtin carries `mutation`, so
`push_param(v: Vec[Int])` calling `vec_push(v, 1)` is pure in 0.6 although
its caller sees the push. Section 3.5 says "mutating ... an `Array`, `Vec`,
`Set`, or `MutableMap` contributes `mutation`", which is true only for index
assignment. This proposal keeps that rule normative, narrowed to collections
reachable from outside the writer, and records the checker's builtin gap as a
non-normative known limitation. It does not change what the builtins carry in
the checker; see "Mutating builtins" in the Reference Design and Unresolved
Question 1.

## Guide-Level Design

A function's effects describe what its callers can observe. The state a
function creates during its own call is private to that call, so changing it
is not an effect.

Each of these functions is pure in 0.7:

```geno
type Point = Point(x: Int, y: Int)

func histogram(xs: List[Int], buckets: Int) -> List[Int]
    example [0, 1, 1], 3 -> [1, 2, 0]
    var counts = array_new(buckets, 0)
    for x in xs do
        counts[x] = counts[x] + 1
    end for
    return array_to_list(counts)
end func

func moved(p: Point, dx: Int) -> Point
    example Point(x: 1, y: 2), 3 -> Point(x: 4, y: 2)
    var q = p
    q.x = q.x + dx
    return q
end func

func main() -> Unit with io
    var label = "histogram: "
    label = label + to_string(histogram([0, 1, 1], 3))
    print(label)
    print(moved(Point(x: 1, y: 2), 3))
end func
```

- In `histogram`, `counts` is an array the call allocated, and it is only
  indexed and read. Writing its elements is invisible outside the call.
- In `moved`, `var q = p` takes a snapshot of the record (records have value
  semantics, spec section 3.2.1), so `q.x = ...` cannot change the caller's
  `p`.
- `main` declares only `io`. In 0.6 its local `label` added `mutation` and
  `main` was rejected with E310.

Mutation that a caller can observe keeps the effect:

```geno
func bump(counter: Array[Int]) -> Int
    example array_new(1, 0) -> 1
    var alias = counter
    alias[0] = alias[0] + 1
    return alias[0]
end func

@untested("returns a closure")
func make_counter() -> () -> Int with mutation
    var n = 0
    return fn() do
        n = n + 1
        return n
    end fn
end func

func main() -> Unit with io, mutation
    let next = make_counter()
    print(next())
    print(next())
    print(bump(array_new(1, 0)))
end func
```

- `bump` is typed `(Array[Int]) -> Int with mutation`. `var alias = counter`
  copies the reference, not the array, so the write reaches the caller's
  array.
- The lambda in `make_counter` writes `n`, which it captured from its enclosing
  function. Successive calls of the closure return 1, then 2. The lambda's type
  is `() -> Int with mutation`, so `make_counter`'s declared return type must
  say so. Writing `-> () -> Int` is still E300 ("Return type mismatch:
  expected () -> Int, got () -> Int with mutation"). `make_counter` itself is
  pure: creating a closure does not perform the closure's effects. `main` is
  charged because it calls `next`.

The diagnostics do not change. A function that still carries `mutation` and is
passed where a pure function is expected reports the same E300. A declared
effect set that leaves out an observable `mutation` reports the same E310.

When a function a model expects to be pure still carries `mutation`, the cause
is one of the following:

- it writes through a parameter's `Array`, `Vec` or `MutableMap`, directly or
  through a `var` copied from it;
- it writes a variable captured from an enclosing function (in a lambda);
- it writes below a reference-typed field or element (`b.cells[0] = v`,
  `grid[i][j] = v`), which includes every two-dimensional table built with
  `array_new(h, array_new(w, 0))`;
- it calls a function or a local closure that carries `mutation`, including a
  lambda that updates an accumulator it captured;
- it lets a collection it allocated escape (stores it in a list, record or
  tuple that is not returned, passes it to a user function, captures it in a
  lambda, or copies it into a second binding, as a rolling buffer
  `prev = cur` does) and then writes the collection.

## Reference Design

### 1. Terms

- **Unit.** A unit is a function body, which covers top-level functions, impl
  methods, `main` and `async` functions, or a lambda body (block or
  expression). A lambda nested in a unit is a separate unit. The bodies of `if`
  and `match` expressions belong to the unit that contains them.
- **Unit-local binding.** A binding is unit-local to U when it is one of U's
  parameters or is declared in U outside any nested lambda. Declarations are
  `let`, `var`, `var (a, b)`, a `for` variable, a pattern variable and a
  comprehension variable.
  - The checker decides this from its own scope chain, while it type-checks
    the write statement (section 2, "Write classification"). At a statement
    inside a lambda, the binding scope is found below the lambda's capture
    environment (unit-local) or at or above it (captured). This is the same
    walk that `_record_captured_binding` does today. In a top-level function
    every local binding is unit-local, because module scope has no mutable
    bindings (module-level `var` is a parse error and module constants are
    literals, spec 4.7).
  - A binding whose scope cannot be resolved (no binding scope, a
    module-scope binding, or a statement whose check stopped at an error
    before classification) is treated as captured. Unknown never masks.
    `_record_captured_binding` returns early with "not captured" when the
    binding scope is `None`; the locality helper must not reuse that default.
- **Reference type.** `Array`, `Vec`, `Set` and `MutableMap` (spec 3.2.1).
  Every other type a write path can pass through has value semantics: records
  and other constructor values, `List`, `Map` and tuples. A container whose
  type is not known (`Any` after error recovery) counts as a reference type.
- **Write statement.** One of:
  - a plain assignment `x = e`;
  - an index assignment `P[i] = e`, where `P` must be an `Array`, `Vec` or
    `MutableMap` (spec 6.3);
  - a field assignment `P.f = e`, where `P` must be a constructor value.

  `P` is a chain `r s1 ... sk` from a root expression `r` through field
  accesses `.g` and index reads `[j]`.
- **Containers.** The containers of an index or field write are the values the
  write steps into: `c0 = r`, `c1 = r s1`, and so on up to `ck = P`. The write
  changes `ck`. It also changes every earlier container: either in place, if
  that container is a reference, or by write-back into the root binding's own
  copy, if it is a value. The write-back behaviour is the interpreter's
  `_eval_write_target`, compiled Python's `_geno_own_field`/`_geno_own_index`,
  and JavaScript's snapshots.

### 2. The rule

The rules below replace the unconditional `mutation` in `_infer_stmt_effects`.
Each applies to the unit U that contains the statement.

- **L1. Declarations.** `var x = e`, `var x: T = e` and `var (a, b) = e`
  contribute nothing, and neither does `let`. The effects of `e` count as
  before.
- **L2. Plain assignment.** `x = e` contributes `mutation` to U if and only if
  `x` is captured (not unit-local to U). The effects of `e` always count.
  - Parameters cannot be assigned (E306), so a unit-local `x` is always a cell
    of U's own activation. The cell may be a parameter cell redeclared with
    `var v = v`, which is still U's own cell.
- **L3. Index and field writes.** The write contributes `mutation` to U unless
  all of the following hold:
  1. the root `r` is an identifier;
  2. the binding `r` names is unit-local to U;
  3. every container `c0 ... ck` has a value type, except that `c0` may be a
     reference type when `r` is **owned** in U (section 3).

  The effects of the target's sub-expressions, of the index and of `e` always
  count. Index assignment always writes into a reference container, so an
  index write is masked only in the form `r[i] = e` with `r` owned. A field
  write such as `r[i].f = e` is masked when `r` is owned and `r[i]` is a value.
  A field write such as `q.items[0].x = e` is masked when every hop is a value
  (record, `List`, `Map`).
- **Write classification.** The facts L2 and L3 need do not depend on effects,
  so they are computed once, by the statement checks that already have them,
  and stored on the statement node. `_check_assign_statement`,
  `_check_index_assign_statement` and `_check_field_assign_statement`
  already compute the target type and the root name, and they already call
  `_record_captured_binding` on the root. They record three things: the root's
  locality (unit-local, captured or unresolved); whether `c0` has a reference
  type; and whether every later container `c1 ... ck` has a value type. The
  container types are read from the `_resolved_type` of each prefix node
  that the target check just set. A missing type counts as a reference.
  `_infer_stmt_effects` reads only this classification and never type-checks
  a target. A statement with no classification is charged. That covers a
  check that returned early on an error, and a body whose effects are
  inferred without being checked. Ordering holds in every pass: pass 3 checks
  every body before `_stabilize_function_effects`, the final pass checks a
  body before `validate_effects`, and `_check_lambda_body` checks a lambda's
  statements before it infers the lambda's effects. Re-checking the same
  node overwrites the classification with the same value.
- **L4. Calls.** Calls are unchanged. A call contributes the effects of the
  callee's function type, whether the callee is a top-level function, a
  module or trait function, a parameter or a local closure. No builtin carries
  `mutation` in the reference checker, which is also unchanged; this is the
  known limitation of section 5, not the language rule.
- **L5. Lambdas.** Unchanged: evaluating a lambda expression contributes
  nothing to the unit that creates it. The lambda's own function type gets the
  effects of its body under L1–L5, with the lambda as the unit. So a lambda
  that writes a variable declared in its own body is pure, and a lambda that
  writes a captured variable carries `mutation`.

| Construct | 0.6 | 0.7 draft |
| --- | --- | --- |
| `var x = e`, `var (a, b) = e`, never written | `mutation` | nothing |
| `x = e`, `x` unit-local | `mutation` | nothing |
| `x = e`, `x` captured by the writing lambda | `mutation` on the lambda | `mutation` on the lambda |
| `q.f = e`, `xs[i].f = e`, `q.items[0].x = e` (value hops only, unit-local root) | `mutation` | nothing |
| `a[i] = e`, `a[i].f = e`, `a` owned | `mutation` | nothing |
| `a[i] = e`, `a` not owned (aliases a parameter, escaped, captured, reassigned from a non-fresh value) | `mutation` | `mutation` |
| a write crossing a reference container below the root (`b.cells[0] = e`, `grid[i][j] = e`, `b.pts[0].x = e`) | `mutation` | `mutation` |
| any write rooted at a captured binding | `mutation` | `mutation` |
| `vec_push(v, x)`, `array_set(a, i, x)` and the other mutating builtins, any receiver | nothing | nothing |
| calling a function, a closure or a lambda | callee's effects | callee's effects |

### 3. Owned collections

A name `n` is **owned** in unit U when all of the following hold. The test
uses U's whole body at once: it is flow-insensitive and keyed by name.

- **O0.** U contains at least one declaration `var n = e` (or `var n: T = e`)
  with `e` a fresh expression, outside any nested lambda. Without O0, O1–O5
  would hold vacuously for a name U never declares, such as a binding a
  lambda captures or a module constant. L3's unit-local requirement would
  still keep such a write charged, but `owned_names` is a standalone API and
  must not report those names (Unresolved Question 1 plans to reuse it for
  builtin receivers).
- **O1.** `n` is not a parameter of U.
- **O2.** Every declaration of `n` in U, in any block, is `var n = e` or
  `var n: T = e` with `e` a fresh expression. A `let n`, a destructure, or a
  `for`, pattern or comprehension variable named `n` disqualifies `n`.
- **O3.** Every plain assignment `n = e` in U has `e` a fresh expression.
- **O4.** `n` does not appear in any lambda nested in U, whether as an
  identifier, an assignment target or a declaration.
- **O5.** Every other occurrence of the identifier `n` in U is in a permitted
  position:
  - **P1.** The root of a write statement's target.
  - **P2.** The target of an index read `n[i]`. The element's value may go
    anywhere.
  - **P3.** A borrowed argument of a builtin call, matched by position or by
    the manifest parameter name (section 4).
  - **P4.** The container operand of `in` or `not in`.
  - **P5.** The iterable of a `for` statement or a list comprehension.
  - **P6.** A return position: the operand of `return`, the result
    expression of an expression-bodied lambda, or, recursively, an element of
    a tuple or list literal or an argument of a constructor call that is
    itself in a return position.

  Any other occurrence is an **escape**. Escapes include:
  - an argument to a user function or to a builtin parameter that is not
    borrowed;
  - the initializer of another binding;
  - the right side of an assignment;
  - a value stored by an index or field write;
  - an element of a list, tuple or constructor that is not returned;
  - a pipeline stage input, a match scrutinee, an f-string interpolation, an
    operand of `==`, `?` or `await`;
  - a callee.

A **fresh expression** is a call whose callee resolves to an allocating builtin
(section 4). This is the checker's `_resolved_builtin_name`, so a user function
or local binding that shadows the name is not fresh. Method spellings the
checker rewrites to these builtins count too. The call's arguments may be
anything: `array_copy(param)` is fresh, and so is `array_new(n, param_row)`.
In the second case only the root array is fresh. Its elements alias
`param_row`, and writes below the root are charged by L3. No literal produces
a reference collection, so no literal is fresh.

Why the test has this shape:

- **Keyed by name.** Distinct bindings that share a name in one unit (a
  same-block redeclaration, which rebinds the same cell, or a nested-block
  shadow, which is a new one) are owned only if all of them qualify. Merging
  them can only reduce what is owned, so the test needs no scope model of its
  own. That keeps it from disagreeing with the checker's scoping. L3 still
  requires the root to be unit-local by the checker's own scope chain.
- **Flow-insensitive.** An assignment `a = param` later in the body (or in a
  loop) disqualifies `a` for writes that come before it.
- **No lambda occurrences.** A closure that holds the collection can outlive
  the write. A closure that only reads it could still observe the write after
  being handed out. Charging both cases is simpler than modelling closure
  lifetimes. Section 9 records the relaxation that was considered.

The analysis is a separate pure-AST module, described in the Implementation
Plan. For each unit it returns the set of owned names. It handles every
`Statement` and `Expression` class in `geno/ast_nodes.py` explicitly, and a
test enumerates the classes so that a new node type cannot be skipped
silently. It runs once per unit per checker run, after the unit's statements
are checked, so that `_resolved_builtin_name` is set. The checker caches the
result by unit node. The cost is linear in the size of the unit.

### 4. Builtin metadata

The facts about builtins are declared once, in `geno/builtin_manifest.py`,
next to `BUILTIN_MANIFEST`, and derived in `geno/builtin_registry.py`:

- `FRESH_COLLECTION_BUILTINS`: the builtins that return a reference collection
  that no other storage refers to. These are `array_new`, `array_from_list`,
  `array_copy`, `vec_new`, `vec_from_list`, `set_new`, `set_from_list`,
  `set_union`, `set_intersection` and `mutable_map_new`. `array_fill` mutates
  its argument and returns `Unit`, so it is not one.
- `BORROWED_COLLECTION_PARAMS`: for each builtin, the parameters that take a
  reference collection, use it during the call, and neither retain it nor
  return it. Today every parameter of a reference-collection type is borrowed:
  - `array_get`, `array_set`, `array_length`, `array_to_list`, `array_fill`,
    `array_copy`;
  - every `vec_*`, `set_*` and `mutable_map_*` receiver;
  - both operands of `set_union` and `set_intersection`.

  A parameter of type `T` is never borrowed. `append(xs, a)` and
  `vec_push(v, a)` store `a`, so they are escapes for `a`, though not for `v`.

`builtin_registry.py` derives `BORROWED_ARGUMENT_POSITIONS: dict[str,
frozenset[int]]` from the parameter names. `geno/tests/test_builtin_metadata.py`
checks three things:

- every fresh builtin returns a reference collection;
- every borrowed parameter has a reference-collection type;
- every reference-collection-typed parameter of every builtin is listed as
  borrowed.

The third check is a completeness test. A new builtin that retains a
collection must be added there deliberately. A parameter left out by mistake
can only reduce ownership.

### 5. Mutating builtins

Decision 2 of the design brief says that mutating builtins applied to a fresh
local are masked, and that mutation of parameters and other non-fresh values
"keeps `mutation`". In 0.6 no builtin carries `mutation` on any receiver, so
the first half holds trivially. The second half would be a new charge, not a
kept one. **This proposal does not start charging builtins in the reference
checker.** That reinterprets a fixed decision of the brief, so it needs the
project lead's explicit sign-off before the typechecker package (Package 2)
lands. Unresolved Question 1 asks for it.

The language rule and the implementation are kept apart:

- **Normative (spec 3.5).** Writing a reference collection that is reachable
  from outside the writing function or lambda is a `mutation`, whether by
  index assignment or by a mutating builtin. That is the rule 0.6's text
  already states, and 0.7 keeps it.
- **Known limitation (non-normative).** The reference checker does not yet
  charge mutating builtins on any receiver. A program that relies on this
  is outside the rule, and a later change that charges builtins is a
  conformance fix to the checker, not a language change. A pinned test
  records the current behaviour, so closing the gap shows up as a deliberate
  test change.

The reasons for not charging builtins in this proposal:

- **It would reject programs 0.6 accepts.** Every unannotated function that
  calls a mutator on a parameter would gain `mutation`. That includes the 23
  calls in `examples/apps/tetris.geno` and in the selfhost `TypeChecker.geno`,
  `Interpreter.geno` and `Main.geno`. Any caller with a declared effect set
  without `mutation` (`main() -> Unit with io` is the common case), and any
  use as a pure callback, would then fail.
- **It needs its own breaking-change record.** The change, its migration and
  its benefit deserve a separate proposal.
- **The analysis is already here.** The ownership analysis and borrow metadata
  in this proposal are exactly what such a charge needs: charge a mutator call
  unless its receiver is an owned name. The change would be confined to L4
  and the manifest.

Section 3.5 of the spec keeps the normative rule and adds the known
limitation as a non-normative note (section 8).

### 6. Soundness

**Claim.** Assume every mutation of storage that existed before a call is
charged, which the normative rule requires and the reference checker does not
yet do for builtins. Then no write masked by L1–L3 is observable by the
caller, or by a later call of the same closure value.

**Argument.** A masked write changes one of three things. The first is a cell
of U's own activation. The second is value-typed content held in such a cell.
The third is the top-level slots of a collection held by an owned name.

- **Cells.** U's cells are created per activation. Only closures created in U
  can share them. Writes by those closures are charged at the closure (L2, and
  L3 with a captured root). A closure leaves U only by being returned, which
  happens after U's own writes are complete, or by being stored into storage
  that existed before the call.
- **Value content in a cell.** This content is not always private to U.
  Binding, assignment, destructuring and storing into a collection snapshot a
  constructor value (spec 3.2.1; `test_deep_review_backends.py` pins those
  transfers, and builtin storage, on all three backends). **Argument passing
  does not.** A callee that builds a closure over a parameter aliases the
  object in the caller's `var`, and a later in-place field or index write
  through that `var` is visible through the closure. The interpreter,
  compiled Python and compiled JavaScript agree:

  ```geno
  type P = P(x: Int)

  @untested("returns a closure")
  func keep(q: P) -> () -> Int
      return fn() -> q.x
  end func

  func main() -> Unit with io
      var s = P(x: 1)
      let peek = keep(s)
      s.x = 7
      print(peek())
  end func
  ```

  This prints `7` on every backend. The `List` form (`var ys = [p]; let g =
  keep_list(ys); ys[0].x = 5; g()`) returns `5`. Both contradict spec 3.2.1
  ("Binding an existing constructor value creates a snapshot"). That is a
  pre-existing runtime deviation that this proposal does not fix, because
  the brief fixes effects as a static property with no runtime change
  (Unresolved Question 7). The masking stays sound despite it: a callee's
  closure reaches storage the caller can see only by being stored there, and
  storing needs either a charged write (a lambda assigning captured storage,
  or a write through a non-owned reference, both `mutation`) or an uncharged
  builtin. Inside U the alias changes only what U itself computes, which is
  not an effect. If U returns the closure, U's writes are complete.
- **Owned collections.** By O0, O2 and O3, every value the name holds comes
  from an allocating builtin, so no other storage refers to it. By O4 and O5,
  the reference leaves the name only by being returned or lent to a builtin
  that does not keep it. Until U returns, the object is reachable only
  through U's own cells. Writes into its elements' reference-typed contents
  are charged by L3. After U returns, the caller holds a new object.
- **Concurrency and panics.** Async interleaving needs one of the same escapes
  to reach U's cells, and so does recursion. A panic ends the program's
  observable state (there is no catch).

**Boundary.** The assumption fails for mutating builtins, which the reference
checker leaves uncharged on every receiver (section 5). Masking does not widen
that gap for owned collections, because O4 and O5 count any closure mention
and any non-borrowing use as an escape. It does widen it in two ways, and
both need an uncharged builtin store into caller-reachable storage:

1. **A closure over one of U's cells.** `vec_push(param_v, fn() -> n)`
   followed by a masked `n = 5`, or `vec_push(param_v, fn() -> q.x)` followed
   by a masked `q.x = 5`. The caller then sees the new value through the
   stored closure. 0.6 charged U for this only by accident, because U held a
   `var`; under 0.7, U is pure.
2. **A closure a callee built from a `var`-held value.** For example
   `cb(keep(s)); s.x = 7`, where `cb` is a pure callback that `vec_push`es its
   argument into storage it captured. 0.6 charged the write because of the
   `var`; 0.7 masks it.

In both cases the program already changes its caller's storage with no effect
at all, through the builtin store itself. Charging builtin mutators on
non-owned receivers (Unresolved Question 1) closes the base gap and both
widenings: in case 1 the `vec_push` on `param_v` is charged, and in case 2
`cb`'s lambda writes through a captured, non-owned `Vec`, so `cb` carries
`mutation` and the call is charged by L4. Package 3 pins both widenings as
current behaviour on all three backends. The spec note and
`docs/reference/runtime-semantics.md` state the boundary, including the
argument-passing alias.

### 7. Interactions

- **Inference and its fixpoint.** `_stabilize_function_effects` is unchanged
  and still terminates. A unit's masking depends only on its own body, and
  call effects stay monotone.
  - Pass 3 still treats unannotated functions as pure.
  - L2 and L3 read the write classification that the statement checks
    stored (section 2). The effect walk never type-checks a write target.
    Type-checking targets in the effect environment was considered and
    rejected. It would add side effects inside the stabilization fixpoint:
    `_record_captured_binding` on real parent scopes, repeated
    `_generic_value_refs` appends, and errors or `Any` where
    `_bind_pattern_effect_env` skips a pattern or an unannotated generic
    initializer comes back as a type variable. It would also cost time on
    every fixpoint iteration.
  - The perf ratchets (`check-medium` 166.2 ms, `test-medium` 186.8 ms,
    `perf-budgets.toml`) must stay green.
- **Declared effects.** A declared set is still an upper bound. `with
  mutation` on a function that now infers pure is a legal superset; no warning
  is added, because that would be noise on every 0.6 program that declared
  the effect to satisfy 0.6. E310 is reported only for undeclared effects that
  are still inferred.
- **Diagnostics.** No new code. An effect-only function type mismatch stays
  E300, and the declared-but-unused E312 stays unused. Changing the code would
  change diagnostics for programs that remain rejected, for no benefit. Message
  text changes only where it prints an inferred type that lost `mutation`.
  No diagnostic suggests adding `with mutation`, and none gains a fix:
  choosing between widening a declaration and removing the escape is not
  machine-decidable.
- **Property checks (proposal 0017).** Eligibility is "inferred effects ⊆
  {mutation}" (`_ALLOWED_EFFECTS`, `geno/property_check.py`). Masking only
  removes `mutation`, so the eligible set is unchanged:
  - a function that had `{mutation}` now has `{}`, and both are eligible;
  - one that had `{mutation, io}` now has `{io}`, and both are ineligible.

  The comment at `_ALLOWED_EFFECTS` stays true. A test pins this.
- **W300 (`geno/var_shadowing.py`).** Unaffected; the warning is syntactic. A
  redeclared name (which W300 warns about) is owned only if every one of its
  declarations is a fresh `var` (O2).
- **Same-scope rebinding.** The rule that a same-block redeclaration keeps
  its type and effects (`_bind_local`) is unchanged. It is one of the places
  where a function type that lost `mutation` can no longer be rebound to one
  that kept it (Compatibility).
- **Generic and trait calls.** Unchanged mechanics. A bounded trait call takes
  the union of its impl methods' effects, so masking in impl methods shrinks
  it. The type of a write container is never a bare type parameter, because a
  `T` cannot be indexed or field-assigned.
- **Async functions.** The same rule applies. Effect inference does not
  distinguish async bodies, and an `await` cannot reach U's cells or owned
  collections without an escape.
- **Requires, ensures, examples and defaults.** These are expressions, not
  write statements, so nothing changes for them.
- **LSP and tools.**
  - Hover on a local binding prints the type recorded in
    `TypeChecker.binding_types`. Each local is recorded twice: once in pass 3,
    where every unannotated function is still pure, and once in the final
    pass. `lsp_analysis._collect_facts` keeps the first fact per (line,
    column, name, kind), so hover already shows the pass-3 type in 0.6:
    `let g = bump` hovers as `g: (Int) -> Int` even when `bump` carries
    `mutation`, while the use of `g` on the next line shows `with mutation`.
    A hover test on a masked function would pass without this proposal. This
    proposal changes `_collect_facts` so that, for facts coming from
    `binding_types`, a later record for the same key replaces an earlier one.
    The final pass then wins. If the final pass stopped early at an error, the
    pass-3 fact stays, as today. Identifier-use facts already come from
    `_resolved_type`, which each pass overwrites. Tests pin that
    `let g = bump` (still charged) hovers with `with mutation`, that
    `let f = total` (masked) hovers without it, and that a lambda binding
    whose own body is masked hovers as pure.
  - Function hover (`_extract_user_func_sig`) and `geno doc` show declared
    effects only, as in 0.6. Showing inferred effects there is not part of this
    proposal (Unresolved Question 4).
  - REPL `:type` and every error message that prints a type follow the new
    inference automatically.
  - Effect-name completions are unchanged.
- **No syntax.** The lexer, parser, AST, formatter, constrained-decoding
  grammar (`geno/constraints.py`) and VS Code grammar are unchanged, because
  no keyword or form is added. The only LSP code change is the
  binding-fact precedence in `geno/lsp_analysis.py` described above.
- **Self-hosted frontend.** `selfhost/TypeChecker.geno` registers every builtin
  with an empty effect list and `format_gtype` drops effects. It never infers
  or checks effects, under 0.6 rules or these. Per `selfhost/SCOPE.md` it may
  lag. The gap is recorded there as "Effect inference gap", and its tests are
  unchanged.

### 8. Specification text

Section 3.5 of `docs/spec/v0.7.md` replaces its bullet list with the
following. The rest of the section is unchanged, apart from "valid effect
names in Geno 0.6", which becomes "in Geno 0.7".

> - Calling an effectful function propagates its effects to the caller.
> - A write is a `mutation` unless only the writing function or lambda can
>   observe it. Declaring a `var` is never an effect. Assigning a variable is
>   an effect only in a lambda that assigns a variable captured from an
>   enclosing scope; the lambda carries `mutation`, and the function that
>   creates the lambda does not. A field or index write through a
>   variable of the writing function or lambda is not an effect when every
>   value it steps into has value semantics (constructor values, `List`,
>   `Map`, tuples), or when the only reference collection it steps into is the
>   variable's own and the variable is *owned*. Every other write is a
>   `mutation`: through a parameter's collection or a copy of one, below a
>   reference-typed field or element, or through a captured variable.
> - A variable is owned when it is a `var` that is only ever initialized and
>   assigned from an allocating builtin (`array_new`, `array_from_list`,
>   `array_copy`, `vec_new`, `vec_from_list`, `set_new`, `set_from_list`,
>   `set_union`, `set_intersection`, `mutable_map_new`) and whose collection
>   never escapes. It is not mentioned in a nested lambda, and each use is an
>   assignment's target, an index read, an argument a builtin only borrows,
>   the container of `in`, the iterable of `for`, or a returned value. The
>   test considers the whole body of the function or lambda, by name.
> - Writing a reference collection that is reachable from outside the writing
>   function or lambda is a `mutation`, whether the write is an index
>   assignment or a mutating builtin (`vec_push`, `array_set`, `set_add`,
>   `mutable_map_set` and the others). A builtin applied to an owned
>   variable is not an effect.
> - Capability-gated builtins contribute the corresponding semantic effect.
> - When a function has no annotation, its effects are inferred. When an
>   annotation is present, every inferred effect must be included in the
>   declared set; a declared superset is allowed. A pure function can be used
>   where a more-effectful callback is accepted, but not the reverse.

After the list, section 3.5 gains a non-normative note:

> *Known limitation (0.7 draft).* The reference checker does not yet charge
> mutating builtins on any receiver, so `vec_push(v, x)` on a parameter `v`
> is not reported as `mutation`. A program that relies on this is outside the
> rule above, and a future checker may reject it. Passing a `var`-held
> constructor value to a function does not snapshot it on any backend, so a
> closure the callee builds over that parameter observes later field writes
> through the `var` (see 3.2.1). Neither case lets a masked write reach a
> caller without an uncharged builtin store.

Sections 6.2 and 6.3 gain one sentence each, pointing at 3.5: declaring or
assigning a local `var` is not an effect. Section 7.6 states that a lambda
assigning a captured variable carries `mutation`. The 0.6 sentence in 3.2.1
about snapshot semantics is unchanged, because it states the intended
semantics. The argument-passing deviation is recorded in the 3.5 note and in
Unresolved Question 7, not written into 3.2.1 as a rule.

## Backend And Target Parity

No backend reads effects. Outside the typechecker, `FuncType.effects` is read
only by property-check eligibility, the doc generator (declared effects) and
type display. `base_compiler.py` copies declared impl effects, JS generic
specialization keys erase function types, and no optimization depends on
purity. The interpreter, compiled Python, compiled JavaScript (CommonJS and
ESM), the isolated `geno run` lane, `geno test`, the embedding API and the
hosted server therefore run every program exactly as before. Hosted and
browser targets are not special.

The rule depends on the value and reference semantics that every backend
already implements:

- binding, assigning, destructuring or storing a value snapshots it;
- reference collections share storage;
- a write into a value inside a shared array writes back into the array.

Tests pin these today (`test_deep_review_backends.py`,
`test_call_capture_parity.py`). They cover binding and storage transfers, not
argument passing. Argument passing does not snapshot a `var`-held value on any
backend (section 6), and the rule does not depend on it doing so. Package 3
adds three-backend parity programs for every masked shape. Each prints the
caller's state after the call, so a backend that broke snapshot semantics
would now also break the effect system, and the parity test would say so.
Package 3 also pins the argument-passing alias (`keep(s)` then `s.x = 7`, and
the `ys[0].x` form) as current behaviour on all three backends. A runtime fix
for Unresolved Question 7 then shows up as a deliberate test change.

## Compatibility And Migration

**Programs 0.6 rejected that 0.7 accepts:**

- passing a function or lambda that mutates only its own state where a pure
  function type is expected (E300 in 0.6);
- a declared effect set without `mutation` on a function that declares or
  assigns its own `var`s, writes fields of its own records, or writes elements
  of collections it allocated (E310 in 0.6).

**No program changes meaning.** Effects are static, no runtime code changes,
and property-check eligibility is unchanged (section 7). The normative 3.5
statement that mutating builtins on reachable collections are `mutation`
restates the 0.6 text. The checker does not enforce it (the known
limitation), so it rejects no program. A later checker that enforces it
would be a conformance fix with its own compatibility record.

**Diagnostics.** Programs that remain rejected keep their codes. Messages
that print an inferred function type omit `mutation` where it is now masked.
LSP local-binding hover and REPL `:type` likewise.

**Programs 0.6 accepted that 0.7 rejects (breaking).** Geno requires equal
function types in some places: assignment to a `var`, same-block
redeclaration, the element type of an `Option` or list reassigned through a
`var`, and the binding of a generic type parameter from its first argument.
In those places, a function whose inferred type loses `mutation` no longer
matches one that keeps it. Both had `mutation` in 0.6, so the two matched by
accident:

```geno
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

func main() -> Unit with io, mutation
    var f = local_sum
    f = shared_bump
    let g = pick(local_sum, shared_bump)
    print(f(1) + g(1))
end func
```

In 0.7, `f = shared_bump` is E300 "Type mismatch in assignment: expected
(Int) -> Int, got (Int) -> Int with mutation". `pick(local_sum, shared_bump)`
is E300 on argument 2. Here `shared_bump` keeps `mutation` because
`var alias = cells` copies the reference, which is an escape, so
`alias[0] = n` is charged. If-expressions, match expressions, list
literals and map literals already join effects, so they are unaffected. The
migration restores the 0.6 type with an annotation, on either the binding or
the function:

```geno
func local_sum(n: Int) -> Int with mutation
    example 2 -> 3
    var y = n
    y = y + 1
    return y
end func
```

or `var f: (Int) -> Int with mutation = local_sum`. No automatic fix is
offered, because either site is a valid choice. Approximate masking was run
over the 274 repository `.geno` files, the 299 benchmark reference solutions
and every documentation snippet. It changed no file's accepted or rejected
status. Package 2 re-runs that check with the real implementation.

**Recording.** The compatibility policy gains "0.7 (draft): local mutation is
not an effect", meeting its five conditions:

1. this proposal;
2. spec v0.7 section 3.5 and the "Changes from 0.6" list;
3. a `CHANGELOG.md` `[Unreleased]` entry: under "Changed" for the programs now
   accepted, and under "Changed (breaking)" for the class above, with the
   before/after migration;
4. no frozen case is affected: `conformance/v0.4`, `v0.5` and `v0.6` contain no
   effect-dependent case. The development corpus `conformance/v0.7` gains three
   cases:
   - `local-mutation-is-pure`: the Summary program plus a fresh-array writer
     and a lambda with its own accumulator passed to `map`, run on
     `interpreter`, `cli-process`, `cli-direct`, `cli-json`, `python`, `js`
     and `js-esm`;
   - `diagnostic-mutation-parameter-alias`: `bump` passed to `apply`, checker,
     `["E300"]`;
   - `diagnostic-mutation-captured-var`: `make_counter` declared
     `-> () -> Int`, checker, `["E300"]`;
5. release evidence records it when 0.7 is released.

Packages, artifacts and the embedding API are unaffected. `api.check`
diagnostics follow the rules above.

## Security And Resource Limits

Effects are not a security boundary: capabilities authorize effectful
operations at run time, independently of effects (spec 3.5). Nothing in the
sandbox, the capability checks, the isolated lane or the resource limits reads
effects, and this proposal changes none of them. No diagnostic gains new
content, so nothing new can leak from the host. The analysis is linear in the
size of each unit and runs inside the checker's existing passes, so it adds no
denial-of-service surface. The perf ratchets bound it.

The one security-adjacent property is honesty. A type that claims purity
should not hide observable mutation, because callers may rely on it (memoization
by a caller, property-check eligibility, future optimizations). Section 6
states exactly where that holds and where the pre-existing builtin gap leaves
it open. The adversarial tests in Package 3 pin the boundary.

## Testing And Validation

- **Ownership analysis** (`geno/tests/test_mutation_ownership.py`, new):
  - each permitted position and each listed escape;
  - O0 to O4, including that a name a lambda captures and a module-constant
    name are never owned (O0);
  - a same-block redeclaration (fresh then parameter, parameter then fresh);
  - a nested-block shadow;
  - flow-insensitivity: a write before `a = param`, and a reassignment inside
    a loop;
  - a top-level user function named `array_new`, and a local binding named
    `array_new`. The local-binding case is checker-only: the JavaScript
    backend refuses a local binding named after a runtime builtin ("'array_new'
    is a reserved runtime name"), while the interpreter and compiled Python
    accept it;
  - method spellings and named arguments of borrowing builtins;
  - a completeness test over every `Statement` and `Expression` subclass.
- **Builtin metadata** (`test_builtin_metadata.py`): the three checks in
  section 4. `scripts/validate_builtin_parity.py` and `scripts/validate_spec.py`
  stay green.
- **Typechecker** (`test_effect_checking.py`, new `TestLocalMutationMasking`):
  - every row of the section 2 table;
  - declaration-only `var`;
  - mutable destructuring;
  - `apply(total, xs)`, `map`, `.map` and pipeline callbacks;
  - `main() -> Unit with io` with locals;
  - impl methods, async functions and generic functions;
  - a declared `with mutation` that stays legal;
  - E310 for a captured write and a parameter alias under a declared `with io`;
  - E300 for a capturing closure returned as `() -> Int`;
  - unknown never masks: a write whose root does not resolve (an undefined
    name, reported with its own error) still leaves the function's inferred
    effects containing `mutation`, and so does a statement whose check
    returned early on an error;
  - the classification is read from the statement node, and the effect walk
    does not call `_check_expression` on write targets (asserted by counting
    calls on a small program);
  - the local `array_new` binding shadow case;
  - the breaking class above.

  Existing tests that change:
  - `test_effect_checking.py`'s unannotated-`var` case now expects
    `frozenset()`;
  - `test_deep_review_frontend.py`'s `xs[offset()] = y` case keeps its intent
    by writing through a parameter alias, and a sibling asserts that the fresh
    form passes under `with io`.
- **Soundness** (`geno/tests/test_local_mutation_soundness.py`, new): a table
  of adversarial programs. Each lists a function, its expected effect set,
  and, for masked cases, a `main` that prints the caller's state after one or
  two calls. The table runs on the interpreter, compiled Python and compiled
  JavaScript, which must print the same output with the caller's state
  unchanged. The cases are:
  - aliasing through `let` and through `var`;
  - tuple, record and list wrapping, returned (masked) and stored (charged);
  - a closure that reads the collection;
  - a closure that writes a captured variable;
  - a returned closure;
  - loops that reassign from a parameter after the write;
  - match and `for` bindings that reuse the name;
  - `var v = v` over an `Array` parameter;
  - `grid[i][j] = v`, including `array_new(h, array_new(w, 0))` whose rows
    alias (charged);
  - `b.cells[0] = v` and `b.pts[0].x = v`;
  - `xs[0].x = v` on a `List` of records;
  - `m[k] = v` on a fresh `MutableMap`, and `v[i] = x` on a fresh `Vec`;
  - `array_copy(param)` and `set_union`;
  - a rolling buffer `prev = cur; cur = array_new(n, 0)` (charged);
  - passing a fresh collection to a user function, then writing it;
  - a nested lambda writing the enclosing lambda's variable;
  - an async function;
  - a generic higher-order call;
  - a top-level user function named `array_new` (not fresh; the
    local-binding shadow stays checker-only, above);
  - the argument-passing alias of section 6, asserted as current behaviour:
    `var s = P(x: 1); let peek = keep(s); s.x = 7; peek()` gives `7`, and
    `var ys = [p]; let g = keep_list(ys); ys[0].x = 5; g()` gives `5`, with
    the writing function pure;
  - the two widenings of section 6, asserted as current behaviour:
    `vec_push(param_v, fn() -> n)` followed by `n = 5`, and
    `cb(keep(s)); s.x = 7` with a pure `cb` that `vec_push`es into captured
    storage. Each writing function is pure, and the caller observes the
    write. These and the base builtin gap are referenced from Unresolved
    Questions 1 and 7, so closing either shows up as a deliberate test
    change.
- **Backend parity:** a `local_mutation_masking` entry in
  `test_backend_parity.py`'s `PARITY_PROGRAMS`.
- **Interactions:**
  - `test_property_check.py`: the eligible set is unchanged for `{mutation}`
    and `{mutation, io}` functions;
  - `test_lsp.py`: `let g = bump` (still charged) hovers with
    `with mutation`; `let f = total` (masked) hovers without it; a lambda
    binding whose own body is masked hovers as pure; function hover still
    shows declared effects. The first case fails before the
    `_collect_facts` precedence change, so the test is not vacuous;
  - `test_doc_generator.py`: declared effects only, as before;
  - `test_var_shadowing.py`: W300 is unchanged.
- **Conformance:** the three v0.7 cases, the new ids in
  `test_the_repository_development_corpus_is_v07`, a lane test like the
  existing ones in `test_conformance_runner.py`, and
  `python3 scripts/run_conformance.py --all-retained --target all
  --require-node`.
- **Docs and contract:**
  - `test_language_contract.py`: a new
    `test_the_draft_records_local_mutation_masking` checks the
    `development_line.additions` entry contains "proposal 0019", the Changes
    entry names 3.5 and links this proposal, and the compatibility heading
    exists;
  - `test_docs_snippets.py` gets tests for the new tour and llm-prompting
    snippets;
  - `test_formatter_corpus.py` checks every new `.geno` file and snippet.
- **Gates:**
  - `ruff check` and `ruff format --check`;
  - `mypy geno/`;
  - `scripts/check_ci_dx_ratchets.py`;
  - `scripts/check_perf_ratchets.py`;
  - `python3 scripts/local_ci.py targeted` per package and
    `python3 scripts/local_ci.py full` at the end;
  - `python3 scripts/local_ci.py optional`, since this is typechecker work.

## Rollout And Observability

The change lands on the 0.7 development line behind no flag. The package
version stays 0.6.0, and a flag would split effect types across tools that
share the checker. Release notes are the CHANGELOG entries and the
compatibility-policy record. Rollback is reverting the typechecker rule;
nothing outside the checker depends on it.

The signal to watch is the benchmark harness: the share of model-written
solutions rejected by E300 or E310 for `mutation` should drop. The rejected
cases that remain should name only observable mutation, which the guide's
list of causes explains.

## Alternatives

- **Do nothing.** This keeps the most common loop shape incompatible with
  higher-order use, and keeps `main() -> Unit with io` failing for any local
  `var`. Rejected: the effect buys callers nothing.
- **Mask only declarations and plain assignment (decision 1 alone).** This is
  simpler and covers the accumulator. It still charges every algorithm over a
  one-dimensional array it allocates (histograms, sieves, 1-D
  dynamic-programming tables), which is the next most common shape. Decision
  2 requires ownership, and the ownership test is small. Two-dimensional
  tables stay charged either way (Motivation, Unresolved Question 6).
- **Charge mutating builtins on non-owned receivers now.** Rejected for this
  proposal (section 5): it rejects 0.6 programs, including the repository's
  own apps and selfhost, and it deserves its own breaking-change record. It is
  the natural next step (Unresolved Question 1). The spec states the rule
  normatively anyway, so the later change is a checker conformance fix.
- **Charge writes to a cell that a nested lambda mentions, and field writes
  through a `var` passed to a user function.** This would close the two
  widenings of section 6 without touching builtins. Rejected for now. The
  first half contradicts the brief's decision 1, which masks every assignment
  to a function's own `var`. Both halves charge common shapes:
  `xs.map(fn(x: Int) -> x * 100 / total)` after an accumulator loop, and
  `st.n = step(st)` on a state record. And both widenings need an uncharged
  builtin store, which Unresolved Question 1 closes at the source. It stays
  an option for the project lead if Unresolved Question 1 is declined.
- **Koka-style effect masking with regions or local state types.** This is
  precise, but it changes the type language. Geno's function types cannot
  name a region, and models would have to write region annotations. Rejected
  in favour of a syntax-free rule.
- **A precise alias and escape analysis**, which would allow `let b = a`,
  closures that only read, and `to_string(a)`. Each relaxation is sound under
  more machinery: alias sets per binding, and lifetimes for closures. The
  conservative rule is easy to state in the spec and to explain in a
  diagnostic-free way. It can be relaxed later without breaking anything,
  because relaxing it only accepts more programs.
- **Mask calls of local closures that mutate only this unit's cells**
  (`let inc = fn() do c = c + 1 ... end fn; inc()`). The closure's type says
  `mutation` and cannot say which cells, so the checker would need to track,
  per closure binding, that every source of its `mutation` is a captured write
  to a unit-local cell of the caller. Deferred (Unresolved Question 2). The
  call stays charged, which is conservative.
- **Widen inferred `var` function types to avoid the breaking class.** For
  example, infer `var f = local_sum` as the join of every value assigned to
  `f`. That is cross-statement inference for a rare corner, and it needs a
  second pass over each body. The corpora show no case. Rejected in favour of
  the recorded break and its one-line migration.
- **Report effect-only mismatches as E312.** E312 exists but is never emitted.
  Rejected: it would change the code of diagnostics that 0.6 programs which
  remain rejected already get.
- **Warn on an unnecessary `with mutation`.** Rejected: it would flag every
  0.6 program that declared the effect because 0.6 required it.

## Unresolved Questions

1. **Mutating builtins carry no effect (sign-off required before Package 2).**
   The brief's decision 2 assumes that mutating parameters through builtins
   "keeps `mutation`". It never had it. This proposal reinterprets decision
   2. The spec states the rule normatively (a write to a reference collection
   reachable from outside is `mutation`, builtins included), and the reference
   checker's failure to charge builtins is a recorded known limitation with a
   pinned test. The project lead must explicitly accept that reinterpretation
   before the typechecker package lands. Under 0.7, masking also widens the
   gap in two narrow ways (section 6): a closure over one of the function's
   cells, or one a callee built from a `var`-held value, stored by a builtin
   into caller storage. 0.6 charged those functions by accident, because they
   held a `var`. Three options:
   - accept the limitation for 0.7 and follow up later;
   - a follow-up proposal in 0.7 that charges mutators on non-owned
     receivers. That would break programs 0.6 accepts (tetris, selfhost),
     with `with mutation` as the migration. The ownership analysis here (with
     O0) is designed to be reused for it;
   - the narrower cell-capture and argument restrictions listed under
     Alternatives.
2. **Calls of local closures.** These are charged conservatively. If models
   write `let add = fn(x: Int) do total = total + x ... end fn` inside
   functions often enough, masking such calls is worth a follow-up.
3. **The breaking class.** Is a recorded break with an annotation migration
   acceptable, or should the checker join effects for `var` function types?
   The corpora suggest the break is acceptable.
4. **Inferred effects in hover and `geno doc`.** Both show declared effects
   only. Showing the inferred set would help readers see what masking
   decided. That is a tooling change orthogonal to this rule.
5. **Diagnostics that explain the effect.** E300 and E310 do not say which
   statement made a function carry `mutation`. A note naming the first charged
   write ("assigns captured variable `n` at 5:9") would make the remaining
   rejections self-explanatory. It is left out here to keep the diagnostic
   surface unchanged. Until then, `docs/reference/common-pitfalls.md` and
   `docs/llm-prompting.md` list the shapes that stay charged (Motivation), so
   models do not drop `with mutation` where it is still needed.
6. **Two-dimensional tables built row by row.** `var g = array_new(h,
   array_new(w, 0))` must stay charged, because the rows alias. A table whose
   rows are each fresh (`g[i] = array_new(w, 0)` in a loop before any `g[i][j]`
   write) is private in fact, but proving it needs per-element freshness.
   That is worth a follow-up if the benchmark harness shows 2-D DP tables
   among the remaining E300/E310 rejections.
7. **Argument passing does not snapshot (spec 3.2.1 deviation).** On every
   backend, a callee that captures a constructor-value parameter in a closure
   aliases the caller's `var`-held object, and later field writes through the
   `var` are visible through the closure (section 6). Spec 3.2.1 says binding
   an existing constructor value creates a snapshot. This is a pre-existing
   runtime bug, outside this proposal's static-only scope, and it should be
   filed as its own issue. The masking rule does not rely on argument
   snapshots, and Package 3 pins the current behaviour so a fix shows up as a
   deliberate test change.

## Implementation Plan

Four sequential packages; each leaves the tree green and builds on the
previous one.

1. **Builtin ownership metadata and the ownership analysis.**
   - Add `FRESH_COLLECTION_BUILTINS` and `BORROWED_COLLECTION_PARAMS` to
     `geno/builtin_manifest.py`, and derive `BORROWED_ARGUMENT_POSITIONS` in
     `geno/builtin_registry.py`.
   - Add `geno/mutation_ownership.py`, a pure-AST module with `owned_names(body,
     params) -> frozenset[str]` implementing section 3. It reads
     `_resolved_builtin_name` and has an explicit case for every AST class.
   - Tests: `geno/tests/test_mutation_ownership.py` (new) and
     `geno/tests/test_builtin_metadata.py`.
   - No checker behaviour changes in this package.
2. **Typechecker masking.** Changes to `geno/typechecker.py`:
   - gate: the project lead's sign-off on Unresolved Question 1;
   - write classification in `_check_assign_statement`,
     `_check_index_assign_statement` and `_check_field_assign_statement`,
     stored on the statement node (section 2). It records root locality,
     whether `c0` is a reference type, and whether all later hops are values;
   - a `_binding_locality` helper sharing the capture walk with
     `_record_captured_binding`, returning unit-local, captured or
     unresolved. Unresolved never masks;
   - L1–L3 in `_infer_stmt_effects`, reading only that classification. An
     unclassified statement is charged;
   - owned-name state saved and restored by `_function_context`, set in
     `_infer_function_effects` and in `_check_lambda_body` for both block and
     expression bodies, and cached by unit node.

   Tests: update `geno/tests/test_effect_checking.py` (the unannotated-`var`
   case, and the new `TestLocalMutationMasking`) and
   `geno/tests/test_deep_review_frontend.py`; extend
   `geno/tests/test_var_shadowing.py` and `geno/tests/test_property_check.py`.
   Re-run the corpus status probe with the real rule.
3. **Adversarial soundness and parity.**
   - `geno/tests/test_local_mutation_soundness.py` (new, three backends);
   - a `local_mutation_masking` entry in `geno/tests/test_backend_parity.py`;
   - `geno/lsp_analysis.py`: later `binding_types` facts replace earlier ones
     for the same key, so hover shows final-pass types (section 7), with
     hover tests in `geno/tests/test_lsp.py`;
   - a declared-effects pin in `geno/tests/test_doc_generator.py`.

   Any unsoundness found is fixed in `geno/mutation_ownership.py` or
   `geno/typechecker.py`.
4. **Specification, documentation, conformance and compatibility.**
   - `docs/spec/v0.7.md`: sections 3.5, 6.2, 6.3 and 7.6, the "Geno 0.7"
     effect-name line, and a "Changes from 0.6" entry linking this proposal and
     the compatibility record.
   - `spec.json`: a `development_line.additions` entry. The 0.6-normative
     `effect_system` block is left alone, as proposals 0017 and 0018 left
     theirs.
   - `geno/tests/test_language_contract.py`.
   - `conformance/v0.7/manifest.toml` and three programs, plus
     `geno/tests/test_conformance_runner.py`.
   - `docs/operations/compatibility-policy.md` and `CHANGELOG.md`.
   - User docs:
     - `docs/guide/language-tour.md`: an `### Effects` subsection appended at
       the end of `## Functions`, so that pinned snippet indices do not move;
     - `docs/llm-prompting.md`: a rule saying not to add `with mutation` for
       local accumulators or 1-D arrays the function allocates, and listing
       the shapes that keep it (2-D tables, rolling buffers, accumulators
       updated through a user callback, collections passed to helpers);
     - `docs/reference/runtime-semantics.md`: the section 6 boundary,
       including the argument-passing alias and the builtin known limitation;
     - `docs/reference/common-pitfalls.md`: an entry on which writes keep
       `mutation`, with the same list of shapes.
   - `geno/tests/test_docs_snippets.py` for the new snippets.
   - `selfhost/SCOPE.md`: "Effect inference gap".
   - Explicitly unchanged, with the reason recorded in the package's handoff:
     lexer, parser, AST, formatter, `geno/constraints.py`, `vscode-geno` and
     the doc generator. The only LSP code change is Package 3's fact
     precedence.
