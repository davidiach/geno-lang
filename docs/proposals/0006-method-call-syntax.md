# Proposal 0006: Method-Call Syntax

- Status: Draft
- Authors: David Iach (direction), Claude Code (drafting and implementation)
- Created: 2026-10-23
- Discussion: TBD
- Supersedes: None
- Review: None (single-maintainer acceptance under `GOVERNANCE.md`); automated
  review and CI only
- Roadmap: milestone M3 of [proposal 0003](0003-geno-0.6-language-upgrade.md)

## Summary

`receiver.name(args)` calls a function with the receiver as its first
argument, choosing the function by the receiver's type. It covers user
functions, trait methods, generic functions, and the existing builtins, so
`xs.map(f).filter(g).length()`, `s.trim().to_upper()`, `opt.unwrap_or(0)` and
`v.push(x)` all work without renaming anything.

## Motivation

Geno 0.5 rejects `xs.map(f)` with "Unknown field 'map' on type List[Int]",
then "Cannot call non-function type: Any". Every language a model learns from
writes calls on a value this way, so the rejection is a reliable first-try
failure. Without method syntax, operations on a value read inside-out
(`length(filter(map(xs, f), g))`), and type-specific builtins need prefixes
(`option_map`, `vec_push`, `mutable_map_set`) that a model has to memorize.

## Guide-Level Design

```geno
let total = scores
    .filter(fn(s: Int) -> s >= 60)
    .map(fn(s: Int) -> s * 2)
    .fold(initial: 0, reducer: fn(acc: Int, s: Int) -> acc + s)

let name = " Ada ".trim().to_upper()
let first = maybe_first.unwrap_or(0)
```

`x.f(a)` means "call the `f` that takes an `x` first": a function the program
defines or imports, a trait method, or a builtin. The receiver's type decides
which, so `opt.map(f)` and `xs.map(f)` call different functions.

An unknown method is reported against the receiver's type, with a suggestion:

```text
Type List[Int] has no method 'lenght'. Did you mean 'length'?
```

The named-argument rule still applies to the arguments after the receiver:

```text
Method 'replace' takes 2 arguments after the receiver; name them:
.replace(old: ..., new: ...)
```

## Reference Design

For a call `r.m(args)` where `r` is not a module name (`Lib.f(x)` keeps its
meaning), with `R` the type of `r`:

1. If `R` is a user-defined type with a field `m`, the call calls that field,
   as in 0.5.
2. Otherwise the checker chooses a function `F`, trying in order:
   1. the trait method `m`, when `R` implements a trait declaring it, or `R`
      is a type parameter bounded by such a trait;
   2. a top-level function `m` the program defines or imports, unless two
      imports export it, whose first parameter accepts `R`;
   3. the builtin `<prefix>_m` for `R`'s prefix: `list`, `option`, `result`,
      `map`, `mutable_map`, `vec`, `array`, `set`, or `string`;
   4. the builtin `m` whose first parameter accepts `R` (`length` accepts a
      list, string, or array).
3. The call is then exactly `F(r, args)`: arguments are checked and
   instantiated as for that call, and the receiver counts as `F`'s first
   argument. When any other argument is named, the receiver is passed by
   `F`'s first parameter name. Under the named-argument rule, a function of
   three or more parameters needs the arguments after the receiver named.
4. If no function qualifies, the call is rejected (E307) with the closest
   method name the receiver type has.

Local variables are not methods: `let trim = ...` does not change what
`s.trim()` calls.

## Backend And Target Parity

The checker rewrites the call into the ordinary call it denotes, so the
interpreter and both compilers see `F(r, args)` and need no changes. Generic
instantiation, JavaScript specialization, effects, and capability checks apply
to the rewritten call as to any other.

## Compatibility And Migration

Additive: every accepted call `r.m(args)` in 0.5 named a field and keeps its
meaning. Calls that 0.5 rejected may now type-check.

## Security And Resource Limits

None. A method call is the ordinary call it denotes, so capability-gated
builtins stay gated.

## Testing And Validation

`geno/tests/test_method_calls.py` covers the rewrite target for each rule,
fields over functions, module members, chains across the three backends,
generic and bounded functions as methods, suggestions, single reporting inside
lambdas, the named-argument message, argument numbering, and imports that
export the same name.

## Alternatives

- **Pure UFCS** (`x.f(a)` always means `f(x, a)` by lexical lookup). Simpler,
  but `opt.map(f)` and `xs.map(f)` could not both work, and the prefixed
  builtins would stay prefixed.
- **Methods declared inside types.** Would split one function namespace into
  two and add syntax; a function whose first parameter is the type serves the
  same purpose.
