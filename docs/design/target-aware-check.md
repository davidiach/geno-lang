# Target-aware check behavior contract

This contract defines the boundary between Geno's target-agnostic language
check and checks that name an execution target.

1. `geno check --target TARGET`, `geno.api.check(..., target=TARGET)`, and
   manifest-targeted project checks first run the ordinary parser and
   typechecker, then validate the checked AST with the target's canonical
   compiler backend in memory.
2. `python-cli` and `python-hosted` use Python backend validation; `node-cli`
   and `browser` use JavaScript backend validation. Browser artifacts are still
   produced only by `geno build` because raw JavaScript compilation does not
   install the browser bootstrap or HTML wrapper.
3. A successful target-aware check guarantees that source-level lowering
   constraints are satisfied, including backend-safe identifiers, record
   fields, integer literals, and project namespaces. Validation does not write
   artifacts, execute generated code, or grant capabilities.
4. A project with multiple manifest targets is checked against every declared
   target. An explicit check target selects one target, but the manifest is
   still parsed and target names are validated so typos fail closed.
5. A check with no explicit target and no manifest target remains a permissive,
   target-agnostic language check. It does not promise that either compiler
   backend can lower the program. This preserves the existing embedding and
   interpreter-oriented behavior.
6. `geno compile --target python|js` keeps its backend selector. If a manifest
   declares compatible execution targets, compilation checks those profiles;
   otherwise it uses the legacy defaults (`python-cli` or `node-cli`). An
   explicit `--profile` may select a compatible execution profile after the
   manifest has been validated. Browser output continues to require
   `geno build`.
7. Expected target or backend validation failures are ordinary diagnostics:
   commands exit nonzero without a Python traceback and do not write the
   requested output artifact. Capability and sandbox policy remain unchanged.
