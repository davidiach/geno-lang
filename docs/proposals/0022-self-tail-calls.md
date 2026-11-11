# Proposal 0022: Self Tail Calls In Constant Stack

- Status: Draft
- Authors: Claude Code (drafted and implemented at the project lead's request)
- Created: 2026-11-11
- Discussion: https://github.com/davidiach/geno-lang/issues/237
- Supersedes: None
- Review: None yet. No independent human technical review has taken place.
- Target: the 0.7 development line (`docs/spec/v0.7.md`, Draft)

## Summary

A function's call to itself in tail position replaces the running call
instead of nesting inside it, on the interpreter, compiled Python and compiled
JavaScript. A tail-recursive function is then as deep as a loop is long:

```geno
func sum_to(n: Int, acc: Int = 0) -> Int
    example (3) -> 6
    if n == 0 then
        return acc
    end if
    return sum_to(n - 1, acc + n)
end func
```

`sum_to(200000)` failed with "maximum recursion depth exceeded" on every
backend in 0.6. It now returns. Nothing else about a call changes.

## Motivation

Geno is functional-first, and accumulator recursion is how its users and the
models that write it express a loop over a list, a state machine or a parser.
In 0.6 that style stops at about 10,000 steps on the interpreter and at the
host's stack on the compiled backends, so a correct program fails on a large
input and has to be rewritten with `while` and `var`. The depth at which it
fails also differs by backend, which is a parity gap.

## Guide-Level Design

Write the recursive call as the last thing the function does:

```geno
return go(rest, acc + 1)                              // a return
return if done then acc else go(rest, acc + 1) end if // a branch of an if
| [first, ...rest] -> return go(rest, acc + first)    // a match arm
```

A function that returns `Unit` may simply end with the call:

```geno
func drain(n: Int, seen: Vec[Int]) -> Unit
    example (0, vec_new()) -> ()
    if n > 0 then
        vec_push(seen, n)
        drain(n - 1, seen)
    end if
end func
```

A call whose result is still used is not a tail call and nests as before:
`return 1 + depth(n - 1)` reaches the recursion limit at the same depth it
always did.

## Reference Design

### 1. Which calls

A call is a **self tail call** when all of these hold:

1. It is in a top-level function that is not `async` (and is not a `main`
   that awaits). Methods of an `impl` and lambdas have none.
2. Its callee is the function's own name, written as a plain name. The
   function binds that name nowhere: no parameter, `let`, `var`, loop
   variable, pattern variable or lambda parameter of that name, in any scope.
3. It is in **tail position**:
   - the whole value of a `return` statement, at any nesting inside `if`,
     `match`, `for` and `while` statements;
   - the value of a branch of an `if` expression or of an arm of a `match`
     expression that is itself in tail position;
   - in a function that returns `Unit`, the last statement of the body, or
     the last statement of a branch of an `if` statement or an arm of a
     `match` statement that is itself the last statement.
4. It is not inside a lambda.

Everything else is an ordinary call: mutual recursion, a call through a
function value or a module-qualified name, a pipeline stage (`x |> f`),
`f(x)?`, and any call whose result is used.

The rule is syntactic and lives in one place, `geno/tail_calls.py`, which the
interpreter and both compilers share. A program is therefore as deep on one
backend as on another.

### 2. What is kept

A self tail call behaves as the call it replaces in every way but depth:

- **Arguments** are evaluated in the order written, named arguments included,
  and all of them before any parameter changes: `swap(n - 1, b, a)` swaps.
- **Defaults** are evaluated for each logical call that omits the argument.
- **`requires`** is checked on each logical call's arguments.
- **`ensures`** holds for each logical call. Every call a tail call replaced
  returns the final result, so once the result is known each call's clause is
  checked with that call's own parameters, innermost first. A failure names
  the call whose clause failed. When no clause reads a parameter or calls a
  function, the checks would all compute the same thing from the same result,
  and one check is made.
- **Closures** keep the bindings of the logical call that created them. A
  list of closures built over a tail-recursive loop sees a different `n` in
  each.
- **Limits.** The step limit and the timeout apply as before, and a tail loop
  that never ends is stopped by them. A self tail call does not count toward
  the recursion depth limit, and it adds no host frame.
- **Whose call a contract rejected.** `geno test` tells an example or a
  generated input that its own call's `requires` rejected from a violation
  deeper in. A violation in a later logical call counts the calls a tail call
  replaced, so it is still a violation deeper in.

### 3. Runtime failure reports

A report (proposal 0018) shows the frames that exist. The calls a tail call
replaced leave none: a failure in the 40th logical call shows the function
once, called from where the first call was made, and positioned at the
failure. No count of replaced calls is shown. This closes the open question
proposal 0018 left for this proposal.

### 4. How each backend runs them

The interpreter restarts the frame of the running call with the new
arguments.

The compilers choose one of two forms per function:

- **Loop.** The body becomes a loop; a tail call assigns the parameters and
  continues it. Used when nothing can observe that the parameters are reused.

  ```python
  def count(n, acc):
      while True:
          if n == 0:
              return acc
          n, acc = n - 1, acc + 1
          continue
  ```

- **Trampoline.** Each logical call is one call of an inner function, which
  returns a request for the next call instead of making it; a loop in the
  outer function makes the calls one after another. Every logical call has
  its own bindings, so closures and `ensures` checks see the right values.
  Used when the function creates a closure, has an `ensures` clause, or makes
  a tail call from inside a loop or from an `if` or `match` expression.
  JavaScript also uses it when a tail call leaves an argument to its default.

The choice changes speed, not behavior. The loop form is faster than the
recursion it replaces; the trampoline costs about what the recursion did.

## Backend And Target Parity

All three backends run the same calls in constant stack, because they share
the rule. `geno/tests/test_tail_calls.py` runs each tail position, the
argument-order, closure, contract and report cases on all three, at a depth
past every backend's stack. The self-hosted interpreter does not implement
this; see `selfhost/SCOPE.md`.

## Compatibility And Migration

No program that ran to completion changes its result or its output. Two
things a program could observe do change:

- A self-tail-recursive program that reached the recursion limit now runs to
  completion.
- **A tail recursion that never ends is a loop that never ends.** In 0.6 a
  function that forgot its base case failed quickly with "maximum recursion
  depth exceeded". Written with a tail call, it now runs until the step limit
  or the timeout stops it, as `while true` does, and a compiled program run
  on its own runs until it is stopped. The report names the step limit or the
  timeout, not the recursion depth. This is the cost of the feature, and the
  one point the project lead should weigh.
- A runtime failure report from inside such a function shows one frame for
  the function where it showed one per call (bounded to the first and last
  ten).

No diagnostic code, syntax or type rule is added.

No frozen conformance case changes. Five entries of the security corpus and
one 0.7 development conformance case used a tail-recursive function to mean
"deep recursion"; they now use a call whose result is used, or state that the
step limit is what stops them, and a new entry covers the never-ending tail
call.

## Security And Resource Limits

A tail loop is bounded the way a `while` loop is: by the step limit and the
timeout on the interpreter and in the hosted lanes, and by the host's limits
in compiled programs. The recursion depth limit keeps bounding every call
that is not a self tail call.

When `ensures` is checked per call, each replaced call's bindings stay on the
heap until the result is known, so memory grows with the number of logical
calls, as the stack did before. The collection and memory limits are
unchanged.

## Testing And Validation

- `geno/tests/test_tail_calls.py`: every tail position at depth 70,000 on
  each backend; argument order, simultaneous assignment, per-call closures
  and shadowed parameters; `requires` and `ensures` on every logical call,
  innermost first; calls that are not tail calls; the recursion limit, step
  limit and timeout; the frames a failure reports; a function of an imported
  module.
- `conformance/v0.7`: `self-tail-calls`, on all three targets.

## Rollout And Observability

Ships in the 0.7 development line. Nothing is gated.

## Alternatives

- **A trampoline for every call.** General, and it would cover mutual
  recursion, but every call would pay for it and the performance budgets
  would not hold.
- **Raising the recursion limits.** Moves the cliff and keeps the backends
  different.
- **An explicit marker** (`tailcall f(...)` or an annotation). Would turn a
  silent non-tail call into an error, which is useful, but it is new syntax
  models do not write. A lint could do the same later.
- **Only the loop form.** Simpler, but closures created in the body would see
  later calls' values, and `ensures` could not be checked per call.

## Unresolved Questions

1. **Mutual recursion** (`is_even`/`is_odd`) and tail calls to other
   functions. Not covered.
2. **Methods of an `impl`.** A method's call to itself goes through the
   trait's dispatcher and is not covered.
3. **A pipeline whose last stage is the function** (`x |> f` in tail
   position). Not covered; write `f(x)`.
4. **A repeat count in failure reports.** Not shown; see Reference Design 3.
5. **A lint for "this recursive call is not a tail call".** Not included.
6. **The self-hosted interpreter** does not run tail calls in constant stack.

## Implementation Plan

One pull request: the shared rule, the interpreter, the Python backend, the
JavaScript backend, then this proposal, the spec text, `spec.json` and the
conformance case.
