# Proposal 0004: Generic Functions

- Status: Draft
- Authors: David Iach (direction), Claude Code (drafting and implementation)
- Created: 2026-10-23
- Discussion: TBD
- Supersedes: None
- Review: None (single-maintainer acceptance under `GOVERNANCE.md`); automated
  review and CI only
- Roadmap: milestone M1 of [proposal 0003](0003-geno-0.6-language-upgrade.md)

## Summary

A function may declare type parameters after its name:
`func first_or[T](xs: List[T], fallback: T) -> T`. Each call, each example
clause, and each use of the function as a value instantiates the parameters
from context. Inside the function, a type parameter is an opaque type: the
body may pass, store, compare, and print values of that type, and nothing
else. The standard library's `List`, `Option`, and `Result` modules become
generic, replacing `Int`-only wrappers.

## Motivation

Geno 0.5 has generic *types* (`type Tree[T] = ...`) and generic *builtins*
(`map`, `filter`, `fold`), but a user cannot write a generic function. The
standard library shows the cost: `List.zip`, `List.find`, `Option.map`, and
`Result.map` are declared over `Int` only, and the checker swaps in a
builtin's generic signature behind the user's back
(`_STDLIB_FORWARDER_BUILTINS`) so that they work at other types. Users get no
such workaround: every helper over a collection must be duplicated per
element type or written with the builtins inline.

Every language in the proposal 0003 comparison set supports generic
functions. For a language whose users are language models, the absence also
costs correctness: a model trained on Rust, TypeScript, Kotlin, or Swift writes
`func f[T](...)` or `fn f<T>(...)` by reflex and receives a parse error with no
equivalent to fall back on.

## Guide-Level Design

```geno
func first_or[T](xs: List[T], fallback: T) -> T
    example [1, 2], 0 -> 1
    example [], "none" -> "none"
    match xs with
        | [] -> return fallback
        | [x, ...] -> return x
    end match
end func

func main() -> Unit
    print(first_or([3.5], 0.0))   // 3.5
    print(first_or([], "empty"))  // empty
end func
```

Type parameters are PascalCase like every type name. A call needs no type
arguments; they are inferred from the arguments, and from the expected type
when a parameter appears only in the result (`let xs: List[String] = empty()`).

The body sees `T` as an unknown type, so it cannot assume anything about it:

```text
func double[T](x: T) -> T
    return x + x
           ^
Cannot apply '+' to T and T: both operands must be numeric (Int or Float) or
both String for concatenation
```

Values of a type parameter may be passed to other functions that accept any
type, stored in lists, tuples, options, results, and user types, compared
with `==` and `!=`, converted with `to_string`, printed, and interpolated in
f-strings. Ordering, arithmetic, and use as a `Map` key or `Set` element need
a bound, which proposal 0005 adds; until then the checker rejects them with
an explanation.

A generic function can be passed as a value when its context fixes the type
arguments:

```geno
print(map([1.5, 2.5], show))              // T = Float, from the list
let render: (Int) -> String = show         // T = Int, from the annotation
```

When nothing fixes them, the program is rejected with E321 rather than
guessing (`print(map([], show))`), and the fix is an annotation.

## Reference Design

### Syntax

```ebnf
func_def    = "async"? "func" IDENT type_params? "(" params? ")" "->" type
              effects? spec_clause* statement* "end" "func" IDENT? ;
type_params = "[" TYPE_IDENT ("," TYPE_IDENT)* "]" ;
```

A lowercase name in the list is a parse error that names the PascalCase
spelling. An empty list `[]` is a parse error.

### Validity of the parameter list (E320)

- Names are distinct.
- A name may not be a built-in type name, a type or alias defined in or
  imported into the module.
- Every parameter appears in at least one parameter type or the return type;
  otherwise no call could determine it.
- `main`, `init`, `update`, and `render` cannot be generic, because the host
  calls them without type arguments.
- Impl methods cannot declare type parameters in 0.6; a generic helper can be
  a top-level function the method calls.

### Typing the body

Within the parameter types, return type, `requires`/`ensures` clauses, body,
default values, local annotations, and lambdas of a generic function, each
type parameter denotes a distinct rigid type that is compatible only with
itself. Such a type is not numeric, not ordered, and not hashable. `Map[T, V]`
and `Set[T]` with a type parameter key are rejected inside the body.

### Typing a use

Callers see the signature with each type parameter replaced by a fresh
variable per use. A call binds the variables by matching argument types in
order, using the existing rules (including Int-to-Float widening at a
`Float` slot); the result type is the return type under that substitution.
An argument inconsistent with an earlier binding is a type mismatch that names
the bound type: "expected Int, got String".

Each `example` clause is an independent instantiation.

A generic function referenced as a value is instantiated by the expected type
of its context: a call parameter (after all of that call's arguments are
checked), an annotated `let`/`var`, a return, a list element, or a
constructor field. If a type argument remains undetermined after the program
is checked, the program is rejected with E321.

### Modules

Generic functions are exported, imported, and called qualified
(`Lib.show(x)`) or unqualified like any other function. Type parameters are
local to the function and are not part of a module's exported type surface.

## Backend And Target Parity

The interpreter and the Python backend erase types, so one body serves every
instantiation.

The JavaScript backend formats numbers by static type, because a JavaScript
`2.0` and `2` are the same value. Inside a generic body the static type is the
opaque parameter, so a single body would print `show(2.0)` as `2` where every
other backend prints `2.0`. The JavaScript backend therefore compiles one
extra copy of a generic function for each distinct way its type arguments
contain `Float` (`show$g1`, ...), with the parameters replaced by those
arguments, and keeps the ordinary erased body for every instantiation that
involves no `Float`. Copies are planned across the whole project before code
is emitted, exported from the defining module next to the base function, and
capped at 32 per function; polymorphic recursion past that depth falls back
to the erased body.

TypeScript declarations declare type parameters
(`export declare function show<T>(x: T): string;`).

## Compatibility And Migration

Additive. No 0.5 program changes meaning: `func name[` was a parse error.

The standard-library `List`, `Option`, and `Result` functions widen from
`Int`-specific signatures to generic ones. Programs that called them at `Int`
keep type-checking; the checker no longer substitutes builtin signatures for
these modules.

Two diagnostic codes are added: E320 (invalid type parameter list) and E321
(generic function value with undetermined type arguments).

## Security And Resource Limits

No capability, sandbox, or host boundary changes. The JavaScript copy limit
bounds code size for polymorphic recursion.

## Testing And Validation

`geno/tests/test_generic_functions.py` covers parsing, the E320 rules, the
opaque-body rules, per-call and per-example instantiation, function values and
E321, interpreter/Python/JavaScript parity (including `Float` formatting
through type parameters, generic-calls-generic, polymorphic recursion, and a
two-module project), the generic standard library through the CLI, JavaScript
copy planning, TypeScript declarations, editor signature help, and generated
documentation. `conformance/v0.6` gains run and diagnostic cases.

## Alternatives

- **Angle brackets** (`func f<T>`). Ambiguous with comparison in expression
  position if explicit type arguments are ever added, and inconsistent with
  Geno's existing `List[T]` and `type Tree[T]`.
- **Implicit type variables** (any unknown single-letter type is generic).
  Shorter, but a typo in a type name silently becomes a type variable.
- **JavaScript dictionary passing** (a hidden formatter argument per type
  parameter). Changes every generic function's JavaScript arity, which is a
  public interop surface for ESM and TypeScript users.
- **Monomorphizing every instantiation in JavaScript.** Correct but grows code
  with every distinct type; only `Float` placement changes behavior.

## Unresolved Questions

- Explicit type arguments at a call site (`empty[Int]()`) are not proposed;
  an annotated binding covers every case found so far.
