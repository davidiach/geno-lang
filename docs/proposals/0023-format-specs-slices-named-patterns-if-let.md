# Proposal 0023: Format Specs, Slices, Named-Field Patterns and If Let

- Status: Accepted
- Accepted: 2026-11-11 by David Iach (project lead)
- Authors: Claude Code (drafted and implemented at the project lead's request)
- Created: 2026-11-11
- Discussion: https://github.com/davidiach/geno-lang/issues/238
- Supersedes: None
- Review: No independent human technical review took place. The project lead
  accepted under the sole-maintainer provision in `GOVERNANCE.md`. Review
  received: local validation, and GitHub CI, Release Gate and External Apps on
  #260. The automated Codex review did not run. These are not independent
  approval.
- Target: the 0.7 development line (`docs/spec/v0.7.md`, Draft)

## Acceptance Decision

On 2026-11-11 the project lead, David Iach, accepted this proposal as
implemented on #260. In the Claude Code session that built it, offered the
choice between "Accept proposal 0023 as written, then merge" and merging with
the proposal still a draft, the project lead chose the first. This records
acceptance for the 0.7 development line and authorization to merge #260
after green CI.

The decision accepts the choices listed under Reference Design as written:
a spec starts at the first top-level `:`; the spec subset and its limits; a
Float without a type is padded text; `if let` takes no guard; and `..` goes
with named fields only. The Unresolved Questions stay open and do not block
acceptance.

The proposal was created on 2026-11-11 and accepted the same day. The usual
seven-day review period in `docs/proposals/README.md` did not elapse; the
project lead's explicit acceptance is recorded here as an exception to it. It
does not imply that independent human review occurred.

## Summary

Four forms that models write constantly, and that 0.6 rejects as parse
errors, become Geno:

```geno
f"{price:.2f} {name:>10} {count:05d}"       // f-string format specs
xs[1:3]   s[:5]   xs[-2:]                   // slices of a List or a String
| Cfg(port: p, ..) -> p                     // patterns that name fields
if let Some(user) = find_user(id) then ...  // match one pattern
```

Each is the native spelling in most languages models learned from, and none
weakens static typing, effects or capabilities. Every program 0.6 accepts
keeps its meaning.

## Motivation

Proposal 0012 deferred slicing, and proposal 0026 chose to accept the forms
that buy no safety by being rejected. These four are the remaining ones of
that kind:

- Formatting a number for output needs `to_string`, `round` and manual
  padding today. `f"{x:.2f}"` is what models write first.
- Taking a sublist or a substring needs `slice(list: xs, start: 1, stop: 3)`
  with three named arguments, and has no way to count from the end.
- Matching one field of a record with eight fields means writing eight
  positional subpatterns in declaration order.
- Unwrapping an `Option` to use its value in one branch needs a full `match`
  with a second arm that does nothing.

## Guide-Level Design

### Format specs

An interpolation may end with a colon and a spec:

```geno
f"{n:5}"          // "   42"
f"{n:05d}"        // "00042"
f"{total:,}"      // "1,234,567"
f"{ratio:.1%}"    // "12.5%"
f"{name:>10}"     // "     Alice"
f"{x:.2f}"        // "3.14"
```

The spec is `[[fill]align][sign][0][width][,][.precision][type]`. The types
are `d x X o b` for an Int, `f e %` for an Int or a Float, and `s` for any
value. Without a type an Int is written like `d` and anything else is padded
text.

### Slices

`xs[a:b]`, `xs[a:]`, `xs[:b]` and `xs[:]` take part of a List or a String.
A negative bound counts from the end and a bound past either end is clamped,
so a slice never fails at run time.

### Named-field patterns

`Cfg(port: p, host: _)` names every field; `Cfg(port: p, ..)` names some and
ignores the rest. Fields come in any order.

### If let

```geno
if let Some(user) = find_user(id) then
    print(user.name)
else
    print("no such user")
end if
```

The bindings are visible in the then branch only. It also works as an
expression: `let n = if let Some(x) = o then x else 0 end if`.

## Reference Design

### 1. Format specs

**Where the spec starts.** At the first `:` in the interpolation that is
outside every bracket and string literal. A named argument, a slice and a
map key are inside brackets, and Geno has no other expression-level colon, so
no existing interpolation contains one. Everything after it is the spec,
which lets a `:` be a fill character (`{n::>5}`). Issue #238 said "the last
top-level `:`"; the first is what Python does and is the only choice that
allows a colon fill.

**Representation.** The parser turns `{value:spec}` into a binary operation
with the operator `FORMAT_OPERATOR`, the value on the left and the spec's
text as a string literal on the right. Like `x in c`, that reuses a node
every pass that walks operands already handles: effect inference, capture
analysis, ownership analysis and the language server reach the value with no
new case.

**Checking.** `geno/format_spec.py` parses the spec and decides, from the
static type of the value, how the runtime formats it:

| Value | Spec type | Formatted as |
|-------|-----------|--------------|
| Int | none, `d x X o b` | an integer |
| Int or Float | `f e %` | a float |
| any | `s` | text |
| Float, other | none | text |

For text the checker wraps the value in its own one-part f-string, so the
backends pad exactly what the f-string would have written, bounded the same
way. A Float formatted as text is right-aligned by default, as numbers are.

The subset leaves out, each with a message that says what to write instead:

- the `g`, `G`, `n`, `E`, `F` and `c` types, and `=` alignment;
- a `0` flag together with an alignment, or together with `,`;
- a precision on an integer type, or on an Int without a type;
- a sign, `0`, `,` or precision on a Float without a type (Python would
  switch to its general format, which is not portable);
- a width over 1000 or a precision over 100.

A spec that is malformed or does not fit is E331, a new code, reported at the
interpolation.

**Formatting.** Python's `format` defines the output. The interpreter and
compiled Python apply the spec through Python itself. Compiled JavaScript
implements it in the prelude (`_genoFormatSpec`): a double is taken apart
into an integer and a power of two, scaled in BigInt arithmetic and rounded
half to even. `Number.prototype.toFixed` rounds a tie away from zero
(`(2.5).toFixed(0)` is `"3"`, Python writes `"2"`) and switches to exponent
notation at 1e21, so it cannot be used. `%` multiplies the double by 100
first, as Python does.

### 2. Slices

`xs[a:b]` is a binary operation with the operator `SLICE_OPERATOR`, the
target on the left and the pair of bounds on the right; a bound that was left
out is the empty tuple. The checker requires a List or a String target and
Int bounds, and the result has the target's type. The interpreter slices with
Python's own slicing; compiled Python calls `_geno_slice` and compiled
JavaScript `_genoSlice`, which uses `Array.prototype.slice` and code points
for a String. All three clamp and count negative bounds from the end.

A step is a parse error naming `reverse` and comprehensions. A slice is not
an assignment target. `Array`, `Vec`, `Map` and tuples are not sliced.

The existing `slice` and `substring` builtins are unchanged; they clamp but
treat a negative start as 0.

### 3. Named-field patterns

The parser records the names on the constructor pattern and requires that a
pattern names all of its fields or none, that no field appears twice and
that `..` comes last. `...` is accepted for `..`.

The type checker, which knows the constructor's fields, rewrites the pattern
to the positional one it means: each declared field takes the subpattern
written for it, or `_` where `..` left it out. The names are then cleared.
Exhaustiveness and every backend therefore see an ordinary constructor
pattern, and an ignored field is a wildcard for exhaustiveness. An unknown
field, or a field left out without `..`, is E308 with the constructor's
fields listed.

The interpreter and both compilers also read the names themselves, so a
program compiled without type checking still matches by name.

### 4. If let

`if let PATTERN = value then A else B end if` is parsed into
`match value with | PATTERN -> A | _ -> B end match`, with an arm that does
nothing when there is no else branch. As with the if expression, no later
pass needs a new construct. The pattern is anything a match arm accepts,
or-patterns included. A `when` guard is a parse error that says to test the
condition inside the branch or to write a match. `else if let` chains.

### 5. Formatter

The formatter is line-based and needed three spacing rules: a slice's colon
is written without spaces (`xs[1:-1]`), type-parameter bounds keep their
space (`[T: Ord]`), and `..` keeps the space after its comma.

## Backend And Target Parity

All four forms run on the interpreter, compiled Python and compiled
JavaScript, and each test file runs its programs on all three. Format specs
are additionally checked against Python's `format` over a grid of about 900
values and specs, ties, subnormals, the largest double and negative zero
included. The self-hosted frontend does not parse the new forms yet; see
`selfhost/SCOPE.md`.

## Compatibility And Migration

Additive. Each form was a parse error in 0.6, so no accepted program changes
meaning. `{x:...}` inside an f-string was "Unexpected token in f-string
expression"; `xs[1:3]` was "Expected ']'"; `Cfg(port: p)` in a pattern and
`if let` were parse errors too.

One new diagnostic code, E331. No frozen conformance case is affected.

## Security And Resource Limits

No capability and no effect is involved. A formatted interpolation is still
a part of its f-string, so the f-string's size limit applies to what it
writes. Width and precision are capped at 1000 and 100 at check time, which
bounds the work a spec can ask for. The Python prelude applies a spec through
an f-string because the sandbox worker has no `format` builtin. A slice
cannot be larger than what it slices.

## Testing And Validation

- `geno/tests/test_fstring_format_specs.py`: the grid against Python's
  `format` on each backend, specs on other values, colons that are not the
  spec's, each rejected spec with its message and code.
- `geno/tests/test_slicing.py`: thirteen bound shapes on a List and a String
  with Python's slicing as the oracle, evaluation order, types, rejected
  targets and steps, formatter spacing.
- `geno/tests/test_named_field_patterns.py`: order, `..`, nesting,
  or-patterns, exhaustiveness, each error, and a program that skips type
  checking.
- `geno/tests/test_if_let.py`: statements, expressions, chains, scoping,
  each error, formatter.
- `conformance/v0.7`: one run case per form on all three targets and a
  checker case for E331.

## Rollout And Observability

Ships in the 0.7 development line with the other 0.7 additions. Nothing is
gated.

## Alternatives

- **Keep rejecting these forms with fixes**, as proposal 0026 does for other
  foreign syntax. A fix cannot express a format spec or a negative slice
  bound in today's builtins, and these are the spellings models use.
- **New AST nodes for slices and formatted interpolations.** The code base has
  many hand-written walkers over expressions; a node one of them skipped
  would silently lose an effect or a captured variable. A binary operation is
  already walked everywhere.
- **`toFixed` for `f` on JavaScript.** Simpler, and wrong on ties.
- **Guards on `if let`.** `if let P = x when c then` reads ambiguously with
  `else`; a nested `if` or a `match` says the same thing clearly.
- **`..` with positional patterns.** `Node(l, ..)` would have to say which
  fields the rest covers; naming fields is unambiguous.

## Unresolved Questions

1. **Slicing `Array` and `Vec`.** They are reference types, and a slice would
   have to say whether it copies. Left out.
2. **A `g` type.** Python's general format is the natural default for Floats
   but its rules are intricate. `f` and `e` cover the stated uses.
3. **Shorthand `Cfg(port, ..)`** for `Cfg(port: port, ..)`. Not included.
4. **`while let`.** The same desugaring would apply; no evidence yet that
   models reach for it.
5. **The self-hosted frontend** does not parse these forms.

## Implementation Plan

One pull request, four commit series, each passing on all three backends
before the next: if let, slices, named-field patterns, format specs; then
this proposal, the spec text, `spec.json`, the prompting guide and the
conformance cases.
