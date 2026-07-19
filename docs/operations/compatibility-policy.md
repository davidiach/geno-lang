# Compatibility Policy

This policy defines the source and behavior compatibility promised by Geno's
pre-1.0 releases. It is intentionally narrower than a 1.0 stability guarantee:
Geno may still improve its language design, but accepted programs must not break
casually or without migration evidence.

## Compatibility Surfaces

Geno tracks compatibility separately for:

- source syntax and static semantics
- observable interpreter, Python backend, and JavaScript backend behavior
- stable diagnostic codes (diagnostic wording is not stable)
- the documented Python embedding API
- standard-library names and signatures
- project manifests, lockfiles, and generated artifact formats

A component marked experimental or research in the maturity matrix is outside
the compatibility promise unless a frozen conformance case explicitly covers
it. Security boundaries may fail closed even when that rejects a program that
previously ran.

## Release Rules

Patch releases must preserve documented source behavior and frozen conformance
cases. A patch may tighten a security boundary without deprecation when the old
behavior permits a capability bypass, sandbox escape, unsafe host access, or
resource-limit evasion. The release notes must identify that exception.

Before 1.0, a minor release may make an intentional breaking change only when:

1. an accepted language or public-API proposal explains the motivation and
   alternatives;
2. the compatibility impact is called out explicitly;
3. the changelog includes a migration example;
4. affected frozen cases are retained under their original version, and new
   expectations are added under the new version; and
5. the release owner records the change in release evidence.

Once a surface is documented as stable, removal normally requires a deprecation
in at least one preceding minor release. Longer periods may be chosen for widely
used APIs. Deprecation warnings must name the replacement and intended removal
version when known.

## Frozen Conformance Corpus

`conformance/v0.4/manifest.toml` is the first frozen compatibility baseline. It
contains valid programs with exact stdout contracts and invalid programs with
stable diagnostic-code contracts. The runner checks positive cases before
executing them and compares behavior across every declared backend.

Run it from the repository root:

```bash
python scripts/run_conformance.py --all-retained --target all --require-node
```

Node.js is optional for local single-backend work. Release and scheduled
evidence must use `--require-node`; silently omitting a declared production
backend is not a passing all-target run.

When v0.5 adds a new corpus, the v0.4 corpus remains in the repository. Release
qualification runs both the current corpus and at least the immediately
preceding minor corpus. Cases may be corrected only when the original fixture
was internally inconsistent; such corrections require a proposal and a
changelog entry.

## What Users Can Rely On

Within a compatible release line, a conforming program should continue to:

- parse and type-check with the same success or stable diagnostic category;
- produce the same observable value/output on its declared target;
- require no additional capabilities for the same operation; and
- preserve documented project and embedding behavior.

Performance, exact diagnostic prose, generated source formatting, and internal
implementation details are not compatibility surfaces unless a separate
published contract says otherwise.

## Reporting A Regression

Compatibility regressions are bugs. Reports should include the last working
Geno version, first failing version, target, minimal source, and observed
diagnostic or output. A confirmed regression should gain a frozen test before
the issue is closed.
