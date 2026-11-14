# Proposal 0025: Extern Functions

- Status: Draft
- Authors: Claude Code (drafted and implemented at the project lead's request)
- Created: 2026-11-14
- Discussion: https://github.com/davidiach/geno-lang/issues/240
- Supersedes: None
- Review: None yet. No independent human technical review has taken place.
- Target: the 0.7 development line (`docs/spec/v0.7.md`, Draft)

## Summary

An `extern func` gives a host function a Geno signature. A program can then
call it like any other function, on the interpreter, compiled Python and
compiled JavaScript:

```geno
extern func host_sqrt(x: Float) -> Float with ffi
    python "math:sqrt"
    js "Math.sqrt"
end func

extern func sha256_hex(text: String) -> String with ffi
    python "geno_host_helpers:sha256_hex"
    js "./host_helpers.js#sha256Hex"
end func
```

The trust boundary is explicit three times: in the declaration (`with ffi`),
in every caller's effects (`ffi` propagates like `fs`), and at run time (every
call needs the new `ffi` capability). The surfaces that promise a sandbox refuse
`ffi`.

## Motivation

The builtin manifest is everything a Geno program can reach. Hashing, base64,
URL parsing, a database client or a company's own library each have to become
a builtin, and every builtin is a release. An extern lets a program reach the
host for the cases a builtin does not cover. Its types are checked, its
results validated, and it cannot run without a grant.

## Guide-Level Design

Write the signature, `with ffi`, and the host function for each target:

```geno
extern func url_quote(text: String) -> String with ffi
    python "urllib.parse:quote"
    js "encodeURIComponent"
end func

@untested("calls the host")
func search_url(query: String) -> String with ffi
    return "https://example.com/search?q=" + url_quote(query)
end func
```

`geno test` does not grant `ffi`, so a function that reaches an extern is
marked `@untested`, as a function that reads files usually is.

Then grant the capability where the program runs:

```bash
geno run --unsafe --cap ffi,print app.geno
geno compile app.geno -o app.py && python3 app.py --cap ffi,print
geno compile app.geno --target js -o app.js && node app.js --cap ffi,print
```

Without the grant, the first call fails with
`Capability denied: 'url_quote' requires the 'ffi' capability`. The default
`geno run`, `geno run --json` and `geno serve` refuse to grant it.

## Reference Design

The normative text is section 9.11.2 of `docs/spec/v0.7.md`. In short:

**Syntax.** `["export"] "extern" "func" name "(" params ")" "->" type "with"
"ffi"`, then one or two bindings, `python "module:attribute"` and
`js "module#export"` or `js "Global.path"`, then `end func [name]`. `extern` is
contextual: it is a declaration word only directly before `func` at the top
level, and `python` and `js` only inside an extern. An extern has no body,
examples, contracts, default values or type parameters, and it needs no
`@untested`. A malformed binding is a parse error that shows the right form.

**Types.** Parameters and the result are built from `Int`, `Float`, `String`,
`Bool`, `Unit`, `List[T]`, tuples, `Option[T]`, `Map[String, V]` and
`JsonValue`. Any other type is the new diagnostic **E333**, naming the
parameter and the component:
`Extern function 'f' cannot take parameter 'u': User cannot cross the host
boundary: ...`. The checker turns each type into a *shape* in the format typed
JSON uses (proposal 0021), restricted to these kinds (`geno/extern.py`). Each
runtime converts with the shape.

**Effect and capability.** `ffi` is a new effect and a new capability. An
extern declares exactly `with ffi`. Callers infer or declare it like any
other effect. Every call checks the capability first. The binding's module is
loaded on the first allowed call and never before, so a program that is not
granted `ffi` imports nothing.

**Conversion.** Arguments are converted to fresh host values: `Option` becomes
the value or `None`/`null`, a `Map` a `dict` or a plain object, a tuple a
Python `tuple` or a JS array, and a `JsonValue` the host's plain JSON value.
Results are validated against the declared type: `Int` must be an `int` that
is not a `bool` (Python), or a safe integer (JS); `Float` accepts any number;
lists and tuples accept a `list` or a `tuple` (Python) or an array (JS); maps
accept a `dict` with `str` keys, or a plain object or a `Map` with string keys.
A mismatch, a host exception, a binding that does not load or is not
callable, and a backend with no binding are each a panic that names the
extern, for example
`Extern function 'f' returned a value that is not List[Int]: $[1]: expected
Int, got str`. Paths use typed JSON's notation, and a value nested more than
320 levels deep is rejected.

**Targets.** With `--target`, the checker rejects every use of an extern on a
target whose profile never grants `ffi` (`browser`, whose pages get a fixed
grant, and `python-hosted`, since `geno serve` refuses it), and on the others
every use of an extern with no binding for the target's runtime (Python for
`python-cli`, JS for `node-cli`). `targets.toml` lists `ffi` among the
capabilities of `python-cli` and `node-cli` only. The unavailability travels with the function's
signature, so a use through an import, a module namespace or a local alias is
rejected too. `geno compile` checks against the target it compiles for, so
it rejects such a use as well. Only an untargeted check (as for
`geno run --unsafe`) lets one through, and the call then panics.

**Lanes.**

| Surface | `ffi` |
| --- | --- |
| `geno run` (process-isolated) | refused before launch |
| `geno run --json` (embedding API) | refused |
| `geno run --unsafe --cap ffi` | granted; the interpreter calls the Python binding |
| compiled Python or Node run with `--cap ffi` | granted |
| `RunConfig(capabilities={..., "ffi"})` | rejected unless `allow_ffi=True` |
| `geno serve` / `create_handler` | refuses to start with `ffi` allowed |
| `geno test`, `geno repl` | not granted (they take no `--cap`) |
| browser builds | not granted |

## Backend And Target Parity

The interpreter and compiled Python share one implementation
(`geno/_runtime_support.py`, reached through typed JSON's value adapter).
Compiled JavaScript has its own (`geno/_js_runtime_support.js`), written to
match it message for message. Host values themselves differ: JavaScript cannot
tell `3.0` from `3`, so a JS host's `3.0` is an `Int` where Python's is not,
and a JS `JsonValue` result number is a `JsonInt` when it is a safe integer.
Host exception names differ (`ValueError` and `Error`).

The self-hosted frontend does not parse `extern`; see `selfhost/SCOPE.md`.

## Compatibility And Migration

Additive. `extern`, `python` and `js` stay valid identifiers. One new effect
and capability name, `ffi`. One new diagnostic code, E333. `RunConfig` gains
`allow_ffi`, defaulting to `False`. No frozen conformance case is affected.

## Security And Resource Limits

An extern runs host code with the full authority of the process, outside the
step limit, the cooperative timeout, the output limit and every other
capability. That is the point of the feature, so it is fail-closed
everywhere:

- No surface grants `ffi` by default.
- The surfaces that promise isolation refuse it outright: the isolated
  `geno run` lane, `geno run --json` and the hosted server.
- The embedding API needs a second, explicit `allow_ffi=True`, since
  `api.run` is documented as an in-process sandbox.
- The capability is checked before the binding's module is imported, so a
  denied program loads no host code.
- Results are copied into Geno values and validated, so a host cannot hand a
  Geno program a value of the wrong type or a cyclic structure. The
  interpreter applies its collection limit to the result.

Granting `ffi` means trusting every extern the program and its dependencies
declare. `SECURITY.md` and `docs/reference/capabilities.md` say so.

## Testing And Validation

- `geno/tests/test_extern.py`: parsing and each parse error; E333 for each
  rejected type; effects; target-aware rejection, including through an
  import, a namespace and an alias; denial without the capability on every
  backend; every kind of type converted both ways on every backend; every
  failure message on every backend; laziness of the import; and each lane's
  refusal.
- `conformance/v0.7`: `extern-functions` on all three targets with a standard
  library binding, and `diagnostic-extern-type` for E333.
- `geno/tests/security_corpus`: denial without `ffi` in the embedding API
  and the isolated `geno run` lane, and the isolated lane's refusal of
  `--cap ffi`. The `--json`, compiled Python and Node lanes are covered by
  `geno/tests/test_extern.py`.

## Rollout And Observability

Ships in the 0.7 development line, marked Experimental.

## Alternatives

- **Keep growing the builtin manifest.** Does not scale, and every addition is
  a release.
- **Host callbacks in the embedding API** (`RunConfig.host_callbacks`). These
  exist, but only override existing builtins and only for embedders.
- **Untyped externs** taking and returning `JsonValue`. Simpler, but every
  call site would decode by hand, and the checker would know nothing.
- **Inferring `with ffi`** on the declaration. Writing it keeps the boundary
  visible where the extern is declared.
- **Async externs**, for JavaScript promises. Out of scope; see below.

## Unresolved Questions

1. **Async host functions.** A JS binding that returns a promise is a type
   mismatch today. An `async extern` could await it.
2. **Records and enums across the boundary.** They could use typed JSON's
   object form; left out to keep the first version small.
3. **Bytes.** Geno has no bytes type; a host function that needs one can take
   a `String` or a `List[Int]`.
4. **Browser builds.** A page grants a fixed set of capabilities that does not
   include `ffi`, so the `browser` target rejects externs. Allowing global-path
   bindings there would need a way for a page to opt in.
5. **The self-hosted frontend** does not parse `extern`.

## Implementation Plan

One pull request: the AST, parser and formatter support; the checker's rules
and E333; the shared Python runtime and the interpreter's use of it; the
JavaScript runtime; both compilers; the lanes' refusals; then this proposal,
the spec text, `spec.json`, the security and capability references, the
guides, the conformance cases and the security corpus.
