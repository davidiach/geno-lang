# Proposal 0021: Typed JSON

- Status: Accepted
- Accepted: 2026-11-13 by David Iach (project lead)
- Authors: Claude Code (drafted and implemented at the project lead's request)
- Created: 2026-11-13
- Discussion: https://github.com/davidiach/geno-lang/issues/236
- Supersedes: None
- Review: The project lead accepted under the sole-maintainer provision in
  `GOVERNANCE.md`, following automated review of #263 and reproduction of the
  reported defects. No independent human technical review took place.
- Target: the 0.7 development line (`docs/spec/v0.7.md`, Draft)

## Acceptance Decision

On 2026-11-13, the project lead directed that #263's blockers be fixed,
covered by regression tests, and verified before merging after CI passes.
This accepts typed JSON for the 0.7 development line subject to those checks.

## Summary

Three builtins move JSON to and from a program's own types:

```geno
json_decode[T](text: String) -> Result[T, String]
json_encode(value: T) -> String
json_encode_pretty(value: T, indent: Int) -> String
```

```geno
type Role = Admin | Member
type User = User(name: String, age: Option[Int], roles: List[Role])

func load(body: String) -> Result[String, String]
    example "{}" -> Err("$: missing field \"name\"")
    let user: User = json_decode(body)?
    return Ok(json_encode(user))
end func
```

A value that does not fit is an `Err` that says where:
`$.roles[2]: expected one of "Admin", "Member", got "Owner"`.

## Motivation

JSON enters a Geno program only as the `JsonValue` tree `json_parse` returns.
Turning that into a `User` means matching `JsonObject(entries)`, searching a
list of pairs for each field, and checking each value's variant, and encoding
is the same in reverse. Hosted handlers and CLI tools are mostly this code,
and it is where models make mistakes: a forgotten field, a wrong variant, an
error message that does not say which value was wrong.

## Guide-Level Design

The type a call works on is the type the program already wrote.

- **Decoding.** Name the type in brackets, `json_decode[User](text)`, or let
  the context name it: an annotated `let`, a `return` in a function whose
  result type says it, or an argument for a parameter of that type.

  ```geno
  let user: User = json_decode(text)?
  return json_decode(text)                 // in a function -> Result[User, String]
  match json_decode[List[User]](text) with ...
  ```

- **Encoding.** `json_encode(value)` uses the value's type.

Nothing is declared on the type and nothing is imported.

## Reference Design

### 1. The mapping

| Geno type | JSON |
|---|---|
| `Int` | a number written as an integer |
| `Float` | any number |
| `String`, `Bool` | string, boolean |
| `Unit` | `null` |
| `List[T]`, `Array[T]`, `Vec[T]` | array |
| `Set[T]` | array (encoded in `set_to_list` order) |
| a tuple | array of exactly that length |
| `Option[T]` | the value, or `null` for `None` |
| `Map[String, V]` | object, keys in insertion order |
| `Result[T, E]` | `{"ok": value}` or `{"err": error}` |
| `JsonValue` | itself |
| a type with one constructor that has fields | object keyed by field name |
| a type whose constructors all have no fields | the constructor's name, a string |
| any other type | object with `"type"` holding the constructor's name, plus its fields |

`type` is a Geno keyword, so no field can be named `type`.

Decoding rules:

- `Int` accepts only a number written without a fraction or exponent. `1.0`
  is `expected Int, got Float`. `Float` accepts both.
- An object field the type does not have is ignored.
- A field that is missing is an error, unless its type is an `Option`, in
  which case it is `None`.
- A `Result` object has exactly one of `"ok"` and `"err"`.
- Generic and recursive types work: `Page[User]`, `Tree`.

Encoding writes a record's fields in declaration order and a `Float` as a
JSON number with a fraction (`2.0`). A `Float` that is not finite is a
runtime error.

### 2. Which type

`json_decode`'s type is, in order: the type written in brackets; otherwise
the type its context expects for the call or for the call followed by `?`.
A call whose type neither names is an error (E321) that shows both ways to
name it. `json_encode`'s type is the type of its argument.

The three builtins can only be called directly. As a function value
(`map(texts, json_decode)`) there is no call to carry the type, and that is an
error (E332) that suggests a lambda.

`json_decode[T]` is the only use of a bracketed type argument on a call.
The bracket is a type argument when the name resolves to the builtin.
An index into a shadowing collection keeps its usual meaning, including when
the indexed value is callable.

### 3. Types with no JSON form

Rejected by the checker, at the call, as **E332**, naming the component:

```text
json_decode cannot decode this type: (Int) -> Int has no JSON form: a function
is not data (in field 'run' of Handler)
```

They are: function types, `Async`, a `Map` whose keys are not `String`,
`MutableMap`, and a type that is not known where the call is written (a type
parameter of the enclosing generic function).

### 4. Errors at run time

`json_decode` returns `Err(message)`. For text that is not JSON the message
is `json_parse`'s. Otherwise it is `<path>: <what>`, with the path from `$`:

```text
$.items[2].age: expected Int, got String
$: missing field "name"
$.roles[0]: expected one of "Admin", "Member", got "Owner"
$.contact.type: expected one of "Email", "Phone", got "Fax"
$.pair: expected an array of 2 items, got 3
$.outcome: expected an object with "ok" or "err"
```

A key that is not a plain identifier is written `$["a b"]`. These messages
are identical on every backend.

### 5. How it is built

The checker turns the call's type into a **codec** (`geno/json_shape.py`): a
small description of the type's JSON form, with each concrete user type
described once and referenced by key. The codec is the one
place the mapping is decided.

Each backend passes the codec to its runtime, which walks a value and the
codec together:

- Decoding is `json_parse` followed by a walk of the `JsonValue` tree.
  Encoding is the reverse walk followed by `json_stringify`. Parsing, the
  nesting and size limits and the number rules are therefore the ones those
  builtins already have, on every backend.
- The interpreter and compiled Python share one walk; JavaScript has its
  own, written to match message for message.
- A compiled program that uses typed JSON registers each type's constructors
  by defining module and type, so a type reached through another module can be
  built even if its constructor's name is also used elsewhere. A program that
  does not use typed JSON emits none of this.

## Backend And Target Parity

The mapping, the messages and the limits are the same on the interpreter,
compiled Python and compiled JavaScript. `geno/tests/test_typed_json.py` runs
a table of every kind of type and mismatch on all three.

Text that is not valid JSON produces `json_parse`'s error, which the hosts
word differently today; that is unchanged.

The self-hosted frontend does not parse `json_decode[T]`; see
`selfhost/SCOPE.md`.

## Compatibility And Migration

Additive. Three new builtin names: a program that defines its own
`json_decode`, `json_encode` or `json_encode_pretty` keeps working, as a
program's definition shadows a builtin. One new diagnostic code, E332. No
frozen conformance case is affected.

## Security And Resource Limits

Pure: no capability and no effect. Decoding is bounded by `json_parse`'s
nesting limit (128) and size limits. The typed decoder also bounds its own
nesting and the extra values it creates for option wrappers or missing fields.
Encoding stops with a runtime error at the same JSON nesting depth, so a
deeply recursive value cannot exhaust the host's stack. The encoding walk
checks a lower bound on output size before expanding shared or nested values.
Serialization enforces the exact output bound. Codec construction is also
bounded, and type expansions that exceed the checker budget receive E332.

Decoding builds only the types the program declared. The codec specifies the
defining type and the constructors that JSON text can select.

## Testing And Validation

- `geno/tests/test_typed_json.py`: a table of every kind of type decoded and
  every mismatch with its exact message, on each backend; every kind of type
  encoded; the ways a decode's type is named; each rejected type with its
  message; the nesting limit; a type reached only through another module;
  and that an ordinary program carries none of it.
- `conformance/v0.7`: `typed-json` on all three targets and
  `diagnostic-json-form` for E332.

## Rollout And Observability

Ships in the 0.7 development line. Nothing is gated.

## Alternatives

- **Keep hand-written decoders.** What users do today; verbose and
  error-prone.
- **A `Json` trait users implement.** Explicit, but every type needs
  boilerplate that the type definition already implies.
- **Decode by the runtime shape of the value, with no type.** Cannot tell an
  `Int` from a `Float`, a tuple from a list, or which constructor an object
  is.
- **Explicit type arguments on every generic call.** A larger language
  change; `json_decode[T]` is the one call that needs it.
- **A configurable discriminator or field renaming.** Useful for foreign
  formats, and a way to get them wrong; `JsonValue` remains for those.

## Unresolved Questions

1. **Foreign JSON shapes**: renamed fields, a different discriminator key,
   untagged unions. Use `JsonValue` for these today.
2. **`Int` from `1.0`.** Rejected, so that an `Int` never silently loses a
   fraction and the backends agree. Some producers write whole numbers that
   way.
3. **`Option[Option[T]]`**: `null` decodes to the outer `None`.
4. **`MutableMap`** and maps with non-`String` keys have no JSON form.
5. **Typed TOML and CSV decoding.** Out of scope.
6. **The self-hosted frontend** does not parse `json_decode[T]`.

## Implementation Plan

One pull request: the codec; the builtins and the checker's rules; the
interpreter and compiled Python walk; the JavaScript walk; then this
proposal, the spec text, `spec.json`, the guides and the conformance cases.
