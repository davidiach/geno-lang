# Proposal 0005: Type Parameter Bounds

- Status: Accepted
- Accepted: 2026-10-24
- Accepted by: David Iach, project lead
- Authors: David Iach (direction), Claude Code (drafting and implementation)
- Created: 2026-10-23
- Discussion: https://github.com/davidiach/geno-lang/pull/212
- Supersedes: None
- Review: No independent review (single-maintainer acceptance under
  `GOVERNANCE.md`). The implementation was reviewed by the project lead and by
  Codex automated review on #212, and by CI.
- Roadmap: milestone M2 of [proposal 0003](0003-geno-0.6-language-upgrade.md)

## Summary

A generic function's type parameter may declare bounds:
`func largest[T: Ord](xs: List[T], fallback: T) -> T`. A bound is one of three
built-in bounds (`Num`, `Ord`, `Hash`) or the name of a trait. Inside the
function a bound unlocks the operations it names; at each use the type
argument must satisfy it. The standard library's `Map` and `Math` modules
become generic, and the checker's last special case for standard-library
signatures is removed.

## Motivation

Proposal 0004 made type parameters opaque, which is sound but leaves out the
most common generic code: finding a maximum, summing, deduplicating, grouping
by a key, and calling a trait method on each element. Without bounds, `Map.get`
and `Math.max` cannot be written as ordinary Geno functions, and the checker
kept substituting builtin signatures for them.

## Guide-Level Design

```geno
func largest[T: Ord](xs: List[T], fallback: T) -> T
    example [3, 9, 2], 0 -> 9
    example ["b", "a"], "" -> "b"
    var best = fallback
    for x: T in xs do
        if x > best then
            best = x
        end if
    end for
    return best
end func

func total_area[T: Shape](shapes: List[T]) -> Float
    example [Square(1.0), Square(2.0)] -> 5.0
    var total = 0.0
    for s: T in shapes do
        total = total + area(s)
    end for
    return total
end func
```

A missing bound is reported where the operation is, naming the bound to add:

```text
Cannot apply '>' to T and T: operands must both be numeric (Int or Float) or
both String. Declare an ordering bound to compare: [T: Ord]
```

An instantiation that misses a bound is reported at the use:

```text
Type Bool does not satisfy the bound 'Ord' on type parameter T of 'largest';
only Int, Float and String are ordered
```

## Reference Design

### Syntax

```ebnf
type_params = "[" type_param ("," type_param)* "]" ;
type_param  = TYPE_IDENT (":" bound ("+" bound)*)? ;
bound       = TYPE_IDENT ;
```

### Bounds

| Bound | Satisfied by | Unlocks inside the body |
|---|---|---|
| `Num` | `Int`, `Float` | arithmetic and negation, ordering, hashing |
| `Ord` | `Int`, `Float`, `String` | `<`, `<=`, `>`, `>=`, hashing |
| `Hash` | every type that can be a `Map` key or `Set` element | use as a `Map`/`MutableMap` key or `Set` element |
| a trait `Tr` | a user-defined type with `impl Tr for` it | calling `Tr`'s methods with the value as receiver |

`Num` implies `Ord`, which implies `Hash`. A type parameter satisfies a bound
it declares or implies, so a generic function can forward its own bound to
another. An unknown bound name is reported at the definition (E322); a type
argument that misses a bound is reported at the call, example clause, or
function value that instantiates it (E323).

### Numeric parameters

For a type parameter `T: Num`, `T op T`, `T op Int`, and `Int op T` have type
`T` for `+`, `-`, `*`, `/`, and `%`, because for either instantiation, `Int`
or `Float`, the operation yields that type. `T op Float` and `Float op T` have
type `Float`. Two different numeric parameters do not mix. `T` compares with
any number. Integer literals are not values of type `T`; a function that needs
a starting value takes it as a parameter (`add_all(xs, zero: 0.0)`).

### Trait bounds

Inside the body a value of type `T: Tr` is a valid receiver for `Tr`'s methods.
The call dispatches on the value at run time, as calls on a user-defined type
already do. Trait signatures declare no effects, so such a call carries the
union of the effects of every implementation of that method the program can
see.

## Backend And Target Parity

Bounds are erased in the interpreter and the Python backend, which already
choose integer or float division from the runtime value.

The JavaScript backend chooses division and arithmetic helpers from static
types because a JavaScript `7.0` is an integer. Every instantiation whose type
arguments contain `Float` compiles its own copy (proposal 0004), so a `Num`
parameter left in an erased body is always an `Int`, and the backend compiles
it as one. Inside a copy, the substituted type decides.

## Compatibility And Migration

Additive. `Map`, `Math.abs`, `Math.min`, `Math.max`, and `Math.clamp` widen to
generic signatures with bounds; calls that type-checked before still do. New
diagnostics: E322 and E323.

## Security And Resource Limits

None.

## Testing And Validation

`geno/tests/test_generic_bounds.py` covers parsing, each built-in bound,
trait bounds, bound forwarding, unsatisfied bounds at calls, examples and
function values, the hints that name the missing bound, three-backend parity
including integral `Float` division through a `Num` parameter, the erased
JavaScript body, and the generic `Map` and `Math` modules through the CLI.

## Alternatives

- **An `Eq` bound.** Geno's `==` is structural on every type today, so `Eq`
  would only reject programs that run correctly.
- **Literals as `Num` values.** `return 0` in a `T: Num` function would print
  `0` instead of `0.0` on the interpreter and Python when `T` is `Float`,
  because no runtime value records `T`. Taking the starting value as a
  parameter keeps every backend identical.
- **Declared effects on trait methods.** The sound long-term design; deferred
  to the effect-system refinements in the roadmap.
