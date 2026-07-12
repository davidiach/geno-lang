# Backend Runtime Contracts

Backend builtin behavior is derived from the builtin manifest and checked by
`scripts/validate_builtin_parity.py`.

The executable contract lives in `geno/backend_contract.py`. It joins three
views that used to be easy to drift independently:

- source/runtime builtin names and parameter metadata from the manifest-backed
  registry
- Python and JavaScript backend helper names
- target availability from `targets.toml`

Browser graphics and input builtins are the target-sensitive slice. They are
available to browser builds and unavailable to `python-cli`, `node-cli`, and
`python-hosted` target profiles. Direct interpreter mode keeps explicit
compatibility fallbacks so examples can still run without a browser host:
drawing calls are no-ops, screen size returns `800x600`, input predicates return
`false`, pointer coordinates return `0`, and text input returns `""`.

Interpreter builtin registration still happens inside `Interpreter.__init__`.
The extraction plan is to move slices from that initializer into shared
registry-backed tables. Until that split is complete, the backend contract and
parity validator are the guardrail: any browser-only builtin added to
`targets.toml` must also declare its interpreter fallback behavior, and any
manifest/backend naming drift fails the local and release gates.
