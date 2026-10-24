# Proposal 0008: If Expressions, Or-Patterns, Tuple Fields, Map Literals

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
- Roadmap: milestone M5 of [proposal 0003](0003-geno-0.6-language-upgrade.md)

## Summary

Four expression forms that every comparable language has and Geno 0.5
rejects with a parse or type error:

```geno
let parity = if n % 2 == 0 then "even" else "odd" end if
match color with
    | Red | Orange -> return "warm"
    | Green | Blue -> return "cool"
end match
let name = person.0
let ages = {"ada": 36, "alan": 41}
```

## Motivation

- `if` is a statement only, so choosing a value takes a mutable variable or a
  `match` on `true`/`false`.
- Arms that share a body must be repeated.
- Tuples can only be taken apart with `let (a, b): (A, B) = t` or a `match`.
- `{}` is documented in the 0.5 language tour as an empty map but does not
  parse, and there is no literal for a map with entries.

## Reference Design

### If expression

```ebnf
if_expr = "if" expr "then" expr ("else" "if" expr "then" expr)* "else" expr "end" "if" ;
```

In expression position, `if` starts an if expression. The `else` branch is
required. It denotes `match c with | true -> a | false -> b end match`: the
condition must be `Bool`, and the branches have a common type as the arms of a
match expression do (an `Int` branch beside a `Float` branch widens). Errors
say "If condition must be Bool" and "If branch type mismatch".

### Or-patterns

```ebnf
match_arm = "|" pattern ("|" pattern)* ("when" expr)? "->" body ;
```

An arm matches when any alternative matches. Every alternative must bind the
same variable names (a parse error otherwise). The arm behaves as one arm per
alternative with the same guard and body, so exhaustiveness counts each
alternative and each binding takes the type its alternative gives it.

### Tuple fields

`t.0`, `t.1`, ... name a tuple's elements, as does `t[0]` with a literal
index. The index must be an integer literal within the tuple's arity. A
nested field needs parentheses: `(t.0).1`, because `t.0.1` lexes as a float.

### Map literals

```ebnf
map_lit = "{" (expr ":" expr ("," expr ":" expr)* ","?)? "}" ;
```

`{k1: v1, k2: v2}` is a `Map` whose key type and value type are the common
types of its keys and values; `{}` is an empty map whose types come from
context. Keys must be hashable. Two literal keys that are equal are rejected.
Entries keep source order, as maps preserve insertion order.

## Backend And Target Parity

If expressions and or-patterns are parsed into existing forms (a match on a
Bool; repeated arms), and tuple fields into index access, so every backend
and tool already handles them; the interpreter gained tuple indexing. Map
literals are a new expression that each backend builds with a runtime helper
(`_map_literal` in Python, `_mapLiteral` in JavaScript) a program cannot
redefine, with the same collection limits as `map_from_list`.

## Compatibility And Migration

Additive. `{` and `t.0` were parse errors; `if` in expression position was a
parse error; `|` after an arm's pattern was a parse error.

## Testing And Validation

`geno/tests/test_expression_ergonomics.py` covers each form's parse, errors,
and three-backend parity.

## Alternatives

- **Block-bodied if expressions** (statements before the value). Deferred;
  single-expression branches cover choosing a value, which is the common case.
- **Tuple fields only by pattern matching.** Simpler, but `pair.0` is what
  models write from Rust, Swift, and Kotlin habit.
