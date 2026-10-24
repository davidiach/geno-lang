# Proposal 0010: A Coherent Standard Library, and `geno migrate`

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
- Roadmap: milestone M7 of [proposal 0003](0003-geno-0.6-language-upgrade.md)

## Summary

Every operation on a value is reachable the same way, by the name a programmer
expects, without import surprises:

```geno
import List

let n = length("text")            // still the builtin after `import List`
let doubled = map(maybe, fn(x) -> x * 2)   // option_map, as maybe.map(...)
let ages = {"ada": 36}
print(ages.length())              // every collection has length()
print(ages.contains_key("ada"))
print(ages.keys())
print("hello".index_of("l"))      // Some(2), not 2 or -1
print([3, 1, 2].sort())           // no comparator needed
```

Eight builtins that duplicated another or wrapped a one-liner are removed, with
an error naming the replacement, and `geno migrate` rewrites 0.5 source
mechanically wherever the rewrite preserves behavior.

## Motivation

- `import List` bound `List.length`, which takes only a list, in place of the
  builtin `length`, so `length("abc")` stopped type-checking (found during M1).
  `import Option` likewise made `map(xs, f)` on a list a type error.
- `opt.map(f)` worked (proposal 0006) but `map(opt, f)` did not, although both
  spell the same operation and models write both.
- Sizes were `length` for lists, strings and arrays, `set_size` for sets,
  `mutable_map_size` for mutable maps, and nothing for maps. `Map` had no way
  to test for, remove or list keys.
- `string_index_of` returned `-1` for "not found", a sentinel the type checker
  cannot see.
- `sort` required a comparator, so `xs.sort()` failed.
- `repeat_string`/`string_repeat`, `contains_substring`/`string_contains` and
  `sort_strings`/`sort` were duplicates; `square`, `is_positive`, `is_sorted`
  (only for `List[Int]`), `is_permutation` and `is_numeric_string` were
  one-liners that occupied names programs want.
- The 0.6 breaks need a mechanical upgrade path.

## Reference Design

### Standard-library imports

An unqualified import of a standard-library module does not bind a name that
is an always-available builtin; the builtin keeps the name, and the module's
function stays reachable qualified (`List.length(xs)`). Standard-library
functions of such names forward to the builtin, which accepts everything they
do, so no accepted program changes meaning. The type checker, interpreter and
both compilers share one rule (`geno/stdlib_scope.py`). Modules a program
defines itself are unaffected.

The embedding API (`geno.run`, `geno.check`) resolves an imported standard
module from `geno/std` when the caller does not supply it, as `geno run` and
`geno check` do. It previously ignored such an import.

### Free calls resolve like method calls

When a call `name(x, args)` names a top-level function or builtin whose first
parameter cannot accept `x`, and the method call `x.name(args)` would call a
different function (proposal 0006, section 7.5.1), the call is that function.
`map(opt, f)` calls `option_map`, `length(m)` calls `map_size`. The rule is
consulted only where the call would otherwise be rejected, so it changes the
meaning of no accepted program; a call no function accepts reports the
original mismatch.

### One size method

`length` is the size of every collection: `List`, `String`, `Array`, `Vec`
(`vec_length`), `Map` (`map_size`), `Set` (`set_size`) and `MutableMap`
(`mutable_map_size`), as a method and, through the rule above, as a free call.

### `Map`

| Builtin | Type | Method |
| --- | --- | --- |
| `map_size` | `(Map[K, V]) -> Int` | `m.length()` |
| `map_contains_key` | `(Map[K, V], K) -> Bool` | `m.contains_key(k)` |
| `map_remove` | `(Map[K, V], K) -> Map[K, V]` | `m.remove(k)` |
| `map_keys` | `(Map[K, V]) -> List[K]` | `m.keys()` |
| `map_values` | `(Map[K, V]) -> List[V]` | `m.values()` |

`map_remove` returns a new map; maps are values. Keys and values come in
insertion order.

### No sentinels

`string_index_of` and `string_last_index_of` (`s.index_of(t)`,
`s.last_index_of(t)`, `String.index_of`) return `Option[Int]`.

### Sorting

`sort(xs)` (`xs.sort()`) sorts by the natural order `sort_by` already uses for
keys; `sort(xs, comparator)` is unchanged.

### Removed builtins

Calling one reports E302 with its replacement:

| Removed | Write |
| --- | --- |
| `square(x)` | `x * x` |
| `is_positive(n)` | `n > 0` |
| `is_sorted(xs)` | `xs == xs.sort()` |
| `is_permutation(a, b)` | `a.sort() == b.sort()` |
| `is_numeric_string(s)` | `parse_int(s).is_some()` |
| `repeat_string(s, n)` | `s.repeat(n)` |
| `contains_substring(s, t)` | `s.contains(t)` |
| `sort_strings(xs)` | `xs.sort()` |

A program may now define functions of these names.

### `geno migrate`

`geno migrate [path] [--check | --diff]` rewrites the `.geno` files under
`path` in place and lists every construct it left for a person. Each rewrite
preserves the 0.5 program's behavior and works on tokens, so it runs on source
the 0.6 parser rejects; running it again changes nothing.

| 0.5 | Rewritten to |
| --- | --- |
| `throw "text"` | `panic("text")` |
| `with fs, throw` | `with fs` |
| `fs_read_text(p)` (also `fs_write_text`, `http_fetch`, `http_post`) | `fs_read_text(p).unwrap()` |
| `string_index_of(s, t)` (also `last_index_of`, `String.` forms) | `string_index_of(s, t).unwrap_or(-1)` |
| `repeat_string`, `contains_substring`, `sort_strings` | `string_repeat`, `string_contains`, `sort` |
| `square(x)`, `is_sorted(xs)` with a simple argument | `(x * x)`, `(xs == sort(xs))` |
| `is_positive(e)`, `is_permutation(a, b)`, `is_numeric_string(s)` | `(e > 0)`, `(sort(a) == sort(b))`, `is_some(parse_int(s))` |

Reported for a person: `try`/`catch`, `throw` of anything but a string, a
removed builtin passed as a value or with a complex argument. A call whose
result the code already treats as a `Result` or `Option` (`match`, an annotated
`let`, `return` from a function returning one) is left alone, so the tool is
safe on code already written for 0.6. `--check` exits 1 when anything would
change or needs a person.

## Backend And Target Parity

The new builtins exist in the interpreter, compiled Python and compiled
JavaScript, with the same output on all three (`test_coherent_stdlib.py`).
Name resolution happens in the checker and in each backend's import binding,
which share `geno/stdlib_scope.py`.

## Compatibility And Migration

Breaking, under the pre-1.0 policy, in the 0.6 series: the eight removed
builtins and the `Option` result of `index_of`. Each failing program fails to
check with a message naming the fix; `geno migrate` performs it. The import
and free-call rules change no accepted program. The reference applications,
examples and benchmark reference solutions are migrated in the same change.

## Security And Resource Limits

None of the new builtins is capability-gated. `map_keys` and `map_values`
check the collection limit like `map_entries`. `geno migrate` reads and writes
only the files it is pointed at and runs no program.

## Testing And Validation

`geno/tests/test_coherent_stdlib.py` covers standard-library imports (CLI
project lanes and the embedding API), free-call resolution and that it keeps
both accepted programs and reported errors, the collection API, `index_of`,
`sort`, the removed-builtin messages, and `geno migrate` (each rule, the
report, idempotence, the 0.6 guards, the command's three modes).
`conformance/v0.6` adds `coherent-stdlib` and `diagnostic-removed-builtin`.

## Alternatives

- **Remove the prefixed builtins (`string_trim`, `list_map`) instead.** They
  are what method calls resolve to, and qualified standard-library functions
  forward to them; removing them would move the duplication, not end it.
- **Overloading by declared signature sets.** More general, but needs a new
  declaration form; resolving a rejected free call as its method call reuses
  the rule proposal 0006 already specifies.
- **A type-directed `geno migrate`** that type-checks before rewriting. It
  could migrate more `try`/`catch` shapes but cannot run on source the 0.6
  parser rejects, which is the source it exists for.
