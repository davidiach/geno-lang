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
been stored, passed or captured elsewhere.

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
assignment. This proposal corrects that text. It does not change what the
builtins carry; see "Mutating builtins" in the Reference Design and
Unresolved Question 1.

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
  `grid[i][j] = v`);
- it calls a function or a local closure that carries `mutation`;
- it lets a collection it allocated escape (stores it in a list, record or
  tuple that is not returned, passes it to a user function, captures it in a
  lambda, or copies it into a second binding) and then writes the collection.

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
  - The checker decides this from its own scope chain. At a statement inside a
    lambda, the binding scope is found below the lambda's capture environment
    (unit-local) or at or above it (captured). This is the same walk that
    `_record_captured_binding` does today. In a top-level function every local
    binding is unit-local, because module scope has no mutable bindings
    (module-level `var` is a parse error and module constants are literals,
    spec 4.7).
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
- **L4. Calls.** Calls are unchanged. A call contributes the effects of the
  callee's function type, whether the callee is a top-level function, a
  module or trait function, a parameter or a local closure. No builtin carries
  `mutation`, which is also unchanged.
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
kept one. **This proposal does not start charging builtins.** The reasons:

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

Section 3.5 of the spec is corrected to say what the checker does: index and
field writes and assignments are the mutation the checker sees. Mutating
builtins carry no effect in 0.7, which is a known gap (Unresolved Question 1).

### 6. Soundness

**Claim.** Assume every mutation of storage that existed before a call is
charged. Then no write masked by L1–L3 is observable by the caller, or by a
later call of the same closure value.

**Argument.** A masked write changes one of three things. The first is a cell
of U's own activation. The second is value-typed content held in such a cell,
which is U's own snapshot because transfers copy values (spec 3.2.1; pinned
on all three backends by `test_deep_review_backends.py`). The third is the
top-level slots of a collection held by an owned name.

- **Cells.** U's cells are created per activation. Only closures created in U
  can share them. Writes by those closures are charged at the closure (L2, and
  L3 with a captured root). A closure leaves U only by being returned, which
  happens after U's own writes are complete, or by being stored into storage
  that existed before the call.
- **Owned collections.** By O2 and O3, every value the name holds comes from
  an allocating builtin, so no other storage refers to it. By O4 and O5, the
  reference leaves the name only by being returned or lent to a builtin that
  does not keep it. Until U returns, the object is reachable only through U's
  own cells. Writes into its elements' reference-typed contents are charged by
  L3. After U returns, the caller holds a new object.
- **Concurrency and panics.** Async interleaving needs one of the same escapes
  to reach U's cells, and so does recursion. A panic ends the program's
  observable state (there is no catch).

**Boundary.** The assumption fails for mutating builtins, which 0.6 leaves
uncharged everywhere (section 5). Masking does not widen that gap for owned
collections, because O5 counts any non-borrowing use as an escape. For
variable cells there is a residual case. An uncharged builtin can store a
closure that captures one of U's cells into caller-reachable storage, for
example `vec_push(param_v, fn() -> n)`. U's later masked `n = ...` is then
visible through that closure. Such a program already changes its caller's
storage with no effect at all in 0.6, through the `vec_push` itself, so this
is the same gap and not a new one. Charging builtin mutators on non-owned
receivers closes both. The spec and `docs/reference/runtime-semantics.md`
state the boundary.

### 7. Interactions

- **Inference and its fixpoint.** `_stabilize_function_effects` is unchanged
  and still terminates. A unit's masking depends only on its own body, and
  call effects stay monotone.
  - Pass 3 still treats unannotated functions as pure.
  - L3 needs the container types: the effect walk type-checks each prefix of
    the write target in the effect environment. It already visits the target
    to infer the target's effects.
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
  - Hover on a local binding prints its inferred type through `hover_label`
    and now shows the masked type: `let f = total` hovers as `f: (List[Int])
    -> Int`.
  - Function hover (`_extract_user_func_sig`) and `geno doc` show declared
    effects only, as in 0.6. Showing inferred effects there is not part of this
    proposal (Unresolved Question 4).
  - REPL `:type` and every error message that prints a type follow the new
    inference automatically.
  - Effect-name completions are unchanged.
- **No syntax.** The lexer, parser, AST, formatter, constrained-decoding
  grammar (`geno/constraints.py`) and VS Code grammar are unchanged, because
  no keyword or form is added.
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
> - Builtins carry no `mutation`: `vec_push`, `array_set`, `set_add`,
>   `mutable_map_set` and the other mutating builtins are not effects in 0.7,
>   whatever they mutate. This is a known limit of the effect system.
> - Capability-gated builtins contribute the corresponding semantic effect.
> - When a function has no annotation, its effects are inferred. When an
>   annotation is present, every inferred effect must be included in the
>   declared set; a declared superset is allowed. A pure function can be used
>   where a more-effectful callback is accepted, but not the reverse.

Sections 6.2 and 6.3 gain one sentence each, pointing at 3.5: declaring or
assigning a local `var` is not an effect. Section 7.6 states that a lambda
assigning a captured variable carries `mutation`. The 0.6 sentence in 3.2.1
about snapshot semantics is unchanged; the rule now depends on it.

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

- binding or assigning a value snapshots it;
- reference collections share storage;
- a write into a value inside a shared array writes back into the array.

Tests pin these today (`test_deep_review_backends.py`,
`test_call_capture_parity.py`). Package 3 adds three-backend parity programs
for every masked shape. Each prints the caller's state after the call, so a
backend that broke snapshot semantics would now also break the effect system,
and the parity test would say so.

## Compatibility And Migration

**Programs 0.6 rejected that 0.7 accepts:**

- passing a function or lambda that mutates only its own state where a pure
  function type is expected (E300 in 0.6);
- a declared effect set without `mutation` on a function that declares or
  assigns its own `var`s, writes fields of its own records, or writes elements
  of collections it allocated (E310 in 0.6).

**No program changes meaning.** Effects are static, no runtime code changes,
and property-check eligibility is unchanged (section 7).

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
  - O1 to O4;
  - a same-block redeclaration (fresh then parameter, parameter then fresh);
  - a nested-block shadow;
  - flow-insensitivity: a write before `a = param`, and a reassignment inside
    a loop;
  - a user function and a local binding named `array_new`;
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
  - `grid[i][j] = v`;
  - `b.cells[0] = v` and `b.pts[0].x = v`;
  - `xs[0].x = v` on a `List` of records;
  - `m[k] = v` on a fresh `MutableMap`, and `v[i] = x` on a fresh `Vec`;
  - `array_copy(param)` and `set_union`;
  - passing a fresh collection to a user function, then writing it;
  - a nested lambda writing the enclosing lambda's variable;
  - an async function;
  - a generic higher-order call;
  - the documented builtin gap of section 6, asserted as the current
    behaviour and referenced from Unresolved Question 1, so that closing it
    shows up as a deliberate test change.
- **Backend parity:** a `local_mutation_masking` entry in
  `test_backend_parity.py`'s `PARITY_PROGRAMS`.
- **Interactions:**
  - `test_property_check.py`: the eligible set is unchanged for `{mutation}`
    and `{mutation, io}` functions;
  - `test_lsp.py`: local-binding hover shows the masked type, and function
    hover still shows declared effects;
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
  simpler and covers the accumulator. It still charges every fresh-array
  algorithm (histograms, sieves, dynamic-programming tables), which is the
  next most common shape. Decision 2 requires ownership, and the ownership
  test is small.
- **Charge mutating builtins on non-owned receivers now.** Rejected for this
  proposal (section 5): it rejects 0.6 programs, including the repository's
  own apps and selfhost, and it deserves its own breaking-change record. It is
  the natural next step (Unresolved Question 1).
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

1. **Mutating builtins carry no effect.** The brief's decision 2 assumes that
   mutating parameters through builtins "keeps `mutation`". It never had it.
   This proposal keeps decision 2 (masking on fresh receivers is trivially
   true) and records the concern: the effect system does not see `vec_push`,
   `array_set` and similar calls on non-owned values, so section 6's soundness
   claim holds only up to that gap. The project lead should decide whether a
   follow-up proposal charges mutators on non-owned receivers in 0.7. That
   would break programs 0.6 accepts (tetris, selfhost), with the migration of
   declaring `with mutation`. The ownership analysis here is designed to be
   reused for it.
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
   surface unchanged.

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
   - L1–L3 in `_infer_stmt_effects`;
   - a `_binding_is_unit_local` helper sharing the capture walk with
     `_record_captured_binding`;
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
   - hover tests in `geno/tests/test_lsp.py`;
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
       local accumulators or arrays the function allocates;
     - `docs/reference/runtime-semantics.md`: the section 6 boundary;
     - `docs/reference/common-pitfalls.md`: an entry on which writes keep
       `mutation`.
   - `geno/tests/test_docs_snippets.py` for the new snippets.
   - `selfhost/SCOPE.md`: "Effect inference gap".
   - Explicitly unchanged, with the reason recorded in the package's handoff:
     lexer, parser, AST, formatter, `geno/constraints.py`, `vscode-geno`, LSP
     code and the doc generator.
