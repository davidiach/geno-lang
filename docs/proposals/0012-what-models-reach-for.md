# Proposal 0012: What Models Reach For

- Status: Draft
- Authors: David Iach (direction), Claude Code (drafting and implementation)
- Created: 2026-10-23
- Discussion: TBD
- Supersedes: None
- Review: None (single-maintainer acceptance under `GOVERNANCE.md`); automated
  review and CI only
- Roadmap: milestone M11 of [proposal 0003](0003-geno-0.6-language-upgrade.md)

## Summary

The everyday operations a model writes from Python, TypeScript, Rust or Kotlin
habit work in Geno, or fail with a fix that names the Geno spelling:

```geno
if word in seen then ...                 // membership: List, String, Map, Set
let all = evens + odds                   // list concatenation
let total = xs.sum()                     // also sum(xs)
let top = xs.max().unwrap_or(0)          // max of a list is an Option
let lo = min(a, b)                       // the pair form, like max
let count = counts.get(word, 0) + 1      // a map lookup with a default
let half = n.to_float() / 2.0
let total = xs.fold(0, fn(acc, x) -> acc + x)  // two arguments: no names needed
for i in range(10) do ...
if c.is_digit() then ...
```

## Motivation

Pillar P1 of proposal 0003 is first-try correctness of generated code, and
design principle 1 says a difference from the languages models learned from is
a defect unless it buys safety. A probe of 212 everyday idioms
(`geno/tests/test_familiar_idioms.py` keeps the list) found 113 that 0.6 rejects.
None of the rejections buys safety:

- **Missing operations.** `xs.sum()`, `xs.max()`, `xs.is_empty()`,
  `xs.index_of(x)`, `s.reverse()`, `s.lines()`, `c.is_digit()`, `min(a, b)`
  (`max(a, b)` is a builtin; `min` needed `import Math`), `range(n)`.
- **Missing operators.** `x in xs` does not parse; `xs + ys` is a type error.
  Both are among the most common forms in Python-trained output.
- **Two-argument method calls had to name their arguments.** The rule that a
  call with three or more arguments names them counted the receiver, so
  `xs.fold(0, f)`, `s.replace("a", "b")`, `xs.slice(1, 3)` and
  `m.insert(k, v)` were rejected, though the order of two arguments after a
  receiver is the same in every language that has these methods.
- **Conversions without a method form.** `int_to_float(n)` exists but
  `n.to_float()` does not.
- **Other languages' names.** `s.chars()`, `ord(c)`, `xs.skip(2)`,
  `m.items()`, `m.has(k)`, `opt.flat_map(f)` have a Geno equivalent under a
  different name, and the error did not say so. Worse, the edit-distance
  suggestion sometimes named a different operation: `c.is_upper()` got a fix
  to `to_upper`, and `m.set(k, v)` one to `get`.

## Guide-Level Design

Membership is written `x in c` and `x not in c`. It tests an element of a
`List`, `Set` or `Array`, a substring of a `String`, or a key of a `Map`:

```geno
if c in "aeiou" then ...
if key not in counts then ...
```

`+` concatenates two lists of the same element type, as it concatenates two
strings.

A method call names its arguments when it writes three or more of them, the
same rule as a function call; the receiver before the dot is not one of them.
A free call is unchanged: `substring(s, 0, 3)` still names its arguments.

New operations:

| Operation | Notes |
| --- | --- |
| `min(a, b)` | Builtin, like `max(a, b)` |
| `xs.sum()`, `sum(xs)` | `T: Num`; `0` (or `0.0`) for an empty list |
| `xs.min()`, `xs.max()` | `Option[T]`, `None` for an empty list; also `min(xs)`, `max(xs)` |
| `xs.is_empty()` | Also on `String`, `Map` and `Set`, and as `is_empty(x)` |
| `xs.index_of(x)` | `Option[Int]`, like `String.index_of` |
| `xs.count(pred)` | How many elements satisfy `pred` |
| `xs.unique()` | First occurrences, in order; `T: Hash` |
| `s.reverse()` | Also `reverse(s)` |
| `s.lines()`, `s.words()` | Split at line breaks; at runs of whitespace |
| `s.is_digit()`, `is_alpha`, `is_alphanumeric`, `is_whitespace`, `is_upper`, `is_lower` | True when `s` is non-empty and every character is an ASCII digit, letter, ... |
| `range(n)` | `range(0, n)` |
| `m.get(key, default)` | The value, or `default`; `m.get(key)` stays an `Option` |
| `n.to_float()`, `f.to_int()` | `int_to_float`, `float_to_int` as methods |

Using another language's name for one of Geno's operations is an error whose
fix renames it, chosen for the receiver: `s.chars()` becomes `s.to_chars()`,
`m.has(k)` becomes `m.contains_key(k)`, `opt.flat_map(f)` becomes
`opt.and_then(f)`.

## Reference Design

### Membership

`in` and `not in` are comparison operators: same precedence as `==`, not
chainable, result `Bool`. `x not in c` is `not (x in c)`. The checker resolves
each use to the builtin that implements it and records it on the expression;
every backend evaluates that builtin with `(c, x)`:

| Container | Element | Builtin |
| --- | --- | --- |
| `List[T]` | `T` | `contains` |
| `Array[T]` | `T` | `array_contains` when it exists, else `contains` on the elements |
| `Set[T]` | `T` | `set_contains` |
| `String` | `String` | `string_contains` |
| `Map[K, V]`, `MutableMap[K, V]` | `K` | `map_contains_key`, `mutable_map_contains_key` |

Any other container type is E300 naming the supported ones. `for x in xs`
headers and comprehension `for` clauses are unchanged: `in` after a loop
variable is the loop's.

### List concatenation

`List[T] + List[T]` is `List[T]`, unifying element types as a list literal
does, so `xs + []` and `[] + xs` type-check. The collection-size limit applies
as it does to `concat`. JavaScript lowers it to array concatenation, not
string `+`.

### Named arguments

A call must name its arguments when the callee has three or more required
parameters, or the call writes three or more arguments. For a method call,
neither count includes the receiver. The fix that names the arguments is
offered as before.

### Builtins

Each new builtin is declared once in `geno/builtin_manifest.py` and exists in
the interpreter, compiled Python and compiled JavaScript with the same output.
Method calls reach them through the existing rules: `string_reverse` is
`s.reverse()`, `list_max` is `xs.max()`, and a free call the first builtin
rejects (`max(xs)`, `reverse(s)`, `is_empty(m)`) resolves as its method call
(proposal 0010). `Int` and `Float` gain the method prefixes `int_` and
`float_`, so `int_to_float` is `n.to_float()`.

`m.get(key, default)` is `map_get_or`: a method call `get` with two arguments
on a `Map` calls it. `sum` of an empty `List[Float]` is `0.0` on every backend.
The character tests are ASCII so that every backend agrees.

### Fixes

Another language's name for an operation is fixed to the Geno name when the
Geno operation exists for the receiver. The table lives beside the existing
one in the checker; a name may map to several Geno names (`to_int` is
`float_to_int` on a `Float` and `parse_int` on a `String`), and the first one
the receiver has is used.

An edit-distance fix is attached only when it is a likely typo: never to the
same name, never by substitution alone in a name of four letters or fewer
(`set` is not a typo of `get`), and with at most one substitution at distance
two (`is_upper` is not a typo of `to_upper`). The "Did you mean" hint is
unchanged.

## Backend And Target Parity

The interpreter, compiled Python and compiled JavaScript evaluate membership
through the same builtins they already share. New builtins are covered by
builtin parity validation, and `conformance/v0.6` adds a case that runs on all
three. The self-hosted frontend's parity subset lists the new builtins.

## Compatibility And Migration

Additive: every program 0.6 accepted keeps its meaning. The named-argument
change accepts more programs. `in` was already a keyword, so no name changes
meaning. A program that defines a function named like a new builtin keeps
calling its own (proposal 0011).

## Security And Resource Limits

None of the new builtins is capability-gated. Builtins that build collections
(`unique`, `lines`, `words`, list `+`) check the collection limit.

## Testing And Validation

`geno/tests/test_familiar_idioms.py` holds the probe: every idiom in it must
type-check, and a subset runs with identical output on every backend.
Per-builtin tests cover empty inputs, `Float` sums, ASCII boundaries and the
`Hash` bound. `conformance/v0.6` adds `familiar-idioms`. The seeded-mistake
results gain the new foreign-name fixes.

## Rollout And Observability

Lands in the 0.6 development line with the rest of proposal 0003. The prompt
rules in `docs/llm-prompting.md` mention membership, list `+` and the
two-argument method rule.

## Alternatives

- **Python slicing (`xs[1:3]`, `s[::-1]`).** Common in model output, but a
  new expression form with negative and stepped bounds on every backend.
  Deferred; `slice` and `reverse` cover the uses.
- **Keep the receiver in the named-argument count.** Consistent with the free
  form, but rejects the most common two-argument methods for no safety gain:
  a swapped pair of arguments of different types is a type error either way.
- **Aliases instead of fixes (`chars` as a second name for `to_chars`).**
  Proposal 0010 removed duplicate names; a fix teaches the one name instead.
- **Unicode character tests.** Python and JavaScript disagree on what is a
  letter or digit beyond ASCII; ASCII keeps the backends identical.

## Unresolved Questions

- Whether `xs.reduce(f)` (no initial value, `Option[T]`) is worth adding;
  `fold` covers it, and JavaScript's `reduce(f, init)` order differs.
- Whether string slicing should get its own form before 1.0.
