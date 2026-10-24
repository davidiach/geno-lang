# Proposal 0007: Types Inferred From Context

- Status: Draft
- Authors: David Iach (direction), Claude Code (drafting and implementation)
- Created: 2026-10-23
- Discussion: TBD
- Supersedes: None
- Review: None (single-maintainer acceptance under `GOVERNANCE.md`); automated
  review and CI only
- Roadmap: milestone M4 of [proposal 0003](0003-geno-0.6-language-upgrade.md)

## Summary

Four annotations become optional where the surrounding code already fixes the
type: lambda parameters (`fn(x) -> x * 2`), `for` loop variables
(`for x in xs do`), comprehension variables (`[x * 2 for x in xs]`), and
tuple destructuring (`let (a, b) = pair`). Function parameters and return
types stay mandatory, since they are the checked interface examples and
callers rely on.

## Motivation

In 0.5 each of these is a parse error. They are the most common shapes in
code a model writes from Python, TypeScript, Kotlin, Swift, or Rust habit, and
the annotation adds no information: in `xs.map(fn(x: Int) -> x * 2)` the `Int`
repeats what `xs` already says, and a wrong annotation is a new way to fail.

## Guide-Level Design

```geno
let doubled = scores.map(fn(s) -> s * 2)
let total = scores.fold(initial: 0, reducer: fn(acc, s) -> acc + s)
for s in scores do
    print(s)
end for
let squares = [s * s for s in scores if s > 1]
let (name, age) = person
```

A lambda without a usable context is rejected with the fix:

```text
Cannot infer the type of lambda parameter 'x' from where the lambda is used;
annotate it, as in `fn(x: Int) -> ...`
```

## Reference Design

- **Lambda parameters.** A lambda parameter may omit `: Type`. Its type is the
  corresponding parameter type of the function type the lambda's context
  expects: a call parameter, an annotated `let` or `var`, a `return` in a
  function or lambda with a declared function return type, or a constructor
  field. Within a call, lambdas with unannotated parameters are checked after
  the call's other arguments, so `apply(fn(x) -> x * 2, 21)` learns
  `x: Int` from `21`. If the expected parameter type is unknown or still
  contains an undetermined type variable, the program is rejected (E321).
  Annotated and unannotated parameters may be mixed.
- **`for` and comprehension variables.** `: Type` may be omitted; the variable
  has the iterable's element type.
- **Tuple destructuring.** `let (a, b) = value` and `var (a, b) = value` may
  omit the annotation when `value` has a tuple type whose element types are
  determined; the names take those types in order.
- Function parameters, function return types, and record fields still
  require annotations.

## Backend And Target Parity

No backend change: the backends never read these annotations.

## Compatibility And Migration

Additive. Every annotated form keeps its meaning.

## Security And Resource Limits

None.

## Testing And Validation

`geno/tests/test_contextual_inference.py` covers parsing, each context,
deferred lambdas, constructor fields, that inferred types are enforced, the
single error for a lambda without context, loop and comprehension variables,
destructuring, and three-backend parity.

## Alternatives

- **Full Hindley-Milner inference.** Would also infer function signatures,
  which Geno keeps explicit by design: examples and callers depend on them.
- **Inferring lambda parameters from their use in the body.** Gives
  surprising types (`fn(x) -> x + 1` could be Int or Float) and errors far
  from their cause.
