# Proposal 0026: Forms Models Write Without the Prompt

- Status: Accepted
- Accepted: 2026-11-06 by David Iach (project lead)
- Authors: David Iach (direction), Claude Code (drafting and implementation)
- Created: 2026-11-04
- Target release: 0.7 (project-lead direction, 2026-11-06)
- Numbering: reassigned from 0017 to 0026 on 2026-11-06 to avoid the
  0017-0025 allocation in PR #233, retaining the original review opening.
- Discussion: https://github.com/davidiach/geno-lang/pull/232 (opened
  2026-11-04)
- Supersedes: None
- Review: No independent human technical review took place. The project lead
  accepted under the sole-maintainer provision in `GOVERNANCE.md`. Review
  received: automated Codex review, focused local validation, and GitHub CI,
  Release Gate and External Apps on #232. These are not independent approval.

## Acceptance Decision

On 2026-11-06, David Iach instructed: "I accept proposal 0026. Merge #232 when
CI is green". This records acceptance for Geno 0.7 and authorization to merge
the implementation after green CI.

The proposal opened on 2026-11-04. The usual seven-day review period in
`docs/proposals/README.md` would have ended on 2026-11-11; the project lead's
explicit acceptance and merge direction on November 6 is recorded here as an
exception to that waiting period. It does not imply that seven days elapsed
or that independent human review occurred.

## Summary

Four forms that models write from Python, Rust and TypeScript habit, and that
buy no safety by being rejected, become Geno:

```geno
total += x                                  // also -=, *=, /=, %=
for (i, word) in words.enumerate() do ...   // tuple patterns in loops
[a + b for (a, b) in pairs]                 // and in comprehensions
f"count: {m.get("the", 0)}"                 // string literals in f-strings
func describe(self) -> String               // untyped receiver in trait/impl
```

Other languages' spellings that Geno keeps different get a fix that
`geno check --fix` applies: `match x` without `with`, a TypeScript record type
`type P = { x: Int }`, an F# or JavaScript record update `{ p with x = 1 }` /
`{ ...p, x: 1 }`, a Rust struct literal `Point { x: 1, y }`, Python's
`for i, x in pairs`, an assignment to a `let`, and conversion names such as
`int_to_string`. A function written without a return type gets the fix that
writes the type its `return` values have.

## Motivation

Pillar P1 of proposal 0003 is first-try correctness, and its first design
principle says a difference from the languages models learned from is a defect
unless it buys safety. Proposal 0012 closed the gaps in everyday operations.
Writing an ordinary program from habit, without Geno's prompt rules, still hit
these, each a parse or type error with no fix:

- `t += x`: "Expected expression, got '='". Compound assignment is in every
  language on the 0003 comparison list but Elm.
- `for (i, x) in xs.enumerate() do`: "Expected identifier, got '('", although
  `let (i, x) = pair` destructures and `enumerate` exists to be unpacked.
- `f"{m.get("k", 0)}"`: "String literals are not allowed inside f-string
  expressions". Python 3.12, JavaScript template literals, Kotlin and Swift all
  allow it; both backends compile interpolations as ordinary expressions.
- `func describe(self) -> String` in a trait: "Expected ':', got ')'". The
  receiver's type is fixed by the trait or impl, so writing it adds nothing.
- `match n` / `match n:`, `type Point = { x: Int, y: Int }`, `{ p with x: 5 }`,
  `Point { x: 1 }`, `int_to_string(n)`: errors with no fix, some misleading
  ("Expected variant name", "Expected ':' between a map key and its value").
- A missing return type was the only seeded mistake besides missing examples
  that `geno check --fix` never repaired (0 of 78 mutants).

None of the accepted forms weakens static typing, effects or capabilities.
Compound assignment is still an assignment, so it is still checked against
`var`; tuple loop patterns are irrefutable; an f-string interpolation is still
one expression.

## Guide-Level Design

### Compound assignment

`x op= v` is `x = x op v` for `+`, `-`, `*`, `/` and `%`, on a variable, a
field (`p.x += 1`) or an index (`arr[i] += 1`). It is a statement, not an
expression. The target is read and then written, so it may not call a
function: `xs[next()] += 1` is rejected with an error that says to bind the
call with `let` first. `+=` concatenates strings and lists as `+` does.

Assigning to a `let` (with `=` or `op=`) is an error whose fix declares the
variable with `var`. A `MutableMap` entry or a `Vec` element cannot be read
with `[...]`, so `counts[w] += 1` on a `MutableMap` is an error that says to
write `m[key] = m.get(key).unwrap_or(0) + value`; on an immutable `Map` the
error says to build a new map with `insert`.

### Tuple patterns in loops

`for (a, b) in pairs do`, `for (i, (k, v)) in ... do`, `for (_, x) in ...`
and `for (k, v): (String, Int) in ...` unpack each element. Comprehensions
take the same patterns: `[k for (k, v) in m.entries() if v > 1]`. The pattern
may only bind names (or `_`); a literal or constructor is rejected, since a
loop has no arm for the elements it would not match. Python's unparenthesized
`for i, x in pairs` gets the fix that adds the parentheses. An element that is
not a tuple is one error ("Tuple pattern used with non-tuple type"), without
follow-on errors for the names the pattern binds.

### String literals in f-strings

An interpolation may contain string literals, including ones that contain
`{`, `}`, `//` or an escaped quote, and nested f-strings:
`f"{s.replace("}", "")}"`, `f"{f"[{x}]"}"`. Braces outside string literals in
an interpolation stay rejected (a map literal is written outside the
f-string). Errors inside an interpolation are now reported at the line and
column where the expression is written.

### Untyped receiver

In a trait, `func m(self, ...)` is `func m(self: Self, ...)`; in
`impl T for P`, `func m(self, ...)` is `func m(self: P, ...)`. Only the first
parameter of a trait or impl method may omit its type this way.

### Fixes

| Written | Fix |
| --- | --- |
| `match n` / `match n:` | `match n with` |
| `type P = { x: Int, y: Int }` (also `;`, trailing separator, several lines) | `type P = P(x: Int, y: Int)` |
| `{ p with x = 1 }`, `{ p with x: 1 }`, `{ ...p, x: 1 }` | `p with (x: 1)` |
| `Point { x: 1, y }`, `Point { x: 1, ..p }` | `Point(x: 1, y: y)`, `p with (x: 1)` |
| `for i, x in pairs` | `for (i, x) in pairs` |
| `let t = 0` then `t += 1` | `var t = 0` |
| `int_to_string(n)`, `string_of_int(n)`, `n.int_to_string()` | `to_string` |
| `parseInt(s)`, `string_to_int(s)`, `parseFloat(s)` | `parse_int`, `parse_float` |
| `func f(x: Int)` with no `->` | `func f(x: Int) -> T`, `T` inferred |

`trait Container[T]` is one error that says traits take no type parameters
and shows a bounded generic function instead.

## Reference Design

All accepted forms are desugared by the parser onto constructs every backend
already implements, so no backend changes:

- `x op= v` parses as `AssignStatement(x, BinaryOp(op, x, v))`, and
  `e[i] op= v` / `e.f op= v` as the index or field assignment of
  `BinaryOp(op, e[i], v)` with a copy of the target. The operator and `=` are
  two tokens, recognized only when written with nothing between them. The
  target may contain names, literals, field and index accesses and operators,
  never a call, so reading it twice is unobservable.
- `for (p) in xs do body end for` parses as a loop over a fresh synthetic
  name (`_geno_item_<id>`) whose body is
  `match item with | (p) -> body end match`. A comprehension's element and
  condition become match expressions on the same name. IDs follow parse order,
  remain stable under formatting, and skip user identifiers, including those
  inside f-string interpolations. Interpolation sub-parsers share the allocator.
- The lexer copies a string literal inside an interpolation unchanged and
  records each interpolation's span; the parser reads the expression from
  where it is written in the source.
- A trait or impl method's first parameter `self` with no `:` gets the type
  annotation `Self` or the impl's target type.
- A function with no return type is still a parse error (E201), and the
  function is parsed with an inferred return type. Once the file is parsed,
  the checker infers each such function's type from its `return` values as it
  does a block lambda's, ignoring values whose type is unknown (a recursive
  call's). When they settle on one type without `Any`, the error carries the
  fix that writes it; otherwise it carries none.

## Backend And Target Parity

Unchanged by construction: the interpreter, compiled Python and compiled
JavaScript evaluate the desugared forms. `geno/tests/test_cold_write_forms.py`
runs each accepted form on all three and compares the output. The self-hosted
frontend does not parse the new forms yet; `selfhost/SCOPE.md` lists them as
syntax gaps.

## Compatibility And Migration

Additive. Every form accepted here was a parse error, and every program that
checked before checks the same way with the same meaning. Diagnostics change
only for programs that already failed: the f-string literal error is gone,
errors inside interpolations move to their true location, a tuple-pattern
type error no longer adds "Undefined variable" errors (one of which carried a
wrong rename fix), and the fixes above are new.

These forms target Geno 0.7. `docs/spec/v0.6.md` and the retained 0.6/0.5
corpora stay unchanged. `docs/spec/v0.7.md` starts from the 0.6 contract and
describes the additions; `spec.json.development_line` records them separately
from its normative 0.6 fields. The development corpus `conformance/v0.7`
adds executable cases for the new forms and diagnostic cases for rejected
forms. `--all-retained` runs it after the frozen corpora, so inherited cases
are exercised without copying them. This shares the 0.7 structure used by
PR #233; later integration combines both proposals' sections and cases.

## Security And Resource Limits

None. No capability, sandbox or host boundary changes. The lexer's scan of a
string literal inside an interpolation is linear and stops at the end of the
line. Inferring a missing return type runs the checker once more on a file
that already failed to parse, which the LSP may do on each keystroke of such a
file; the cost is one ordinary check.

## Testing And Validation

`geno/tests/test_cold_write_forms.py` covers every accepted form on every
backend, every error and fix (each fixed program must check), the formatter,
and error locations. `docs/benchmark/seeded-mistakes.md` adds `match` without
`with` and a JavaScript `let` for a reassigned variable, and now repairs the
missing return type: 828 of 907 mutants repaired, none misrepaired.

## Rollout And Observability

The prompt rules in `docs/llm-prompting.md` and the language tour mention the
new forms. The changelog targets them at 0.7 under Unreleased. The package
version remains 0.6.0 during development, as in PR #233; no 0.6 patch release
is intended to ship these additions. Acceptance is recorded above; the
implementation lands through #232 after green CI.

## Alternatives

- **Fixes instead of acceptance for `+=`, loop patterns and f-string
  literals.** Keeps one spelling, but each of these is the spelling in most
  of the languages models learned from, and rejecting it buys nothing.
- **A dedicated AST node for compound assignment.** Would let a target call
  a function once, at the cost of new code in the checker and three backends.
  Rejecting calls in the target keeps the desugaring exact.
- **Accept TypeScript record types and Rust struct literals.** Two more ways
  to write a constructor; a fix teaches the one Geno spelling instead.
- **Generic traits.** `trait Container[T]` is a type-system feature with its
  own design questions (impl selection, bounds with arguments); it needs its
  own proposal and is out of scope.

## Unresolved Questions

- Whether `m[key]` should read a `MutableMap` entry and `v[i]` a `Vec`
  element, as `m[key]` reads a `Map`. That would make `counts[w] += 1` work
  and needs a runtime change in all three backends.
- Whether `**=` and the bitwise compound operators are worth adding; none was
  observed in model output.
