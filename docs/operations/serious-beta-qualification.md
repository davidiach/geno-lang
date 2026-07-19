# Serious Beta Qualification

“Serious beta” is an evidence threshold, not a marketing synonym for feature
richness. Geno may use that label only when every blocking criterion below has
current, reviewable evidence tied to the candidate commit.

## Blocking Criteria

### Compatibility And Conformance

- The current language specification and machine-readable specification agree.
- The current and immediately preceding minor conformance corpora pass on every
  supported production backend.
- Intentional breaks have accepted proposals, migration notes, and versioned
  fixtures.
- Stable and experimental surfaces are listed without contradiction.

### Correctness And Scale

- The canonical release gate passes on every supported Python version.
- Linux, macOS, and Windows each run an appropriate supported-platform suite.
- Backend parity, property, and differential tests are green.
- Published scale scenarios cover large source files, multi-module projects,
  compile latency, runtime latency, and memory-sensitive workloads.
- Performance claims include machine details, raw results, and regression
  thresholds; a one-off local timing is not qualification evidence.

### Security

- The production execution boundary has a current threat model and an
  independent review whose critical/high findings are resolved or disclosed.
- At least four consecutive scheduled deep-fuzz runs are green for the candidate
  release line, with failures retained as regression inputs.
- Capability, sandbox, resource-limit, and hosted deployment tests are green.
- Supported security versions, private reporting, response targets, and incident
  ownership are documented.

### Release And Supply Chain

- Wheel and source artifacts are built once, validated, smoke-tested, and then
  published without rebuilding.
- Release artifacts have verifiable provenance tied to the source commit and
  workflow identity.
- Dependency locks, vulnerability audits, rollback instructions, and
  post-publish verification are current.
- A release evidence record names the release owner and independent reviewer.

### Governance And Support

- At least two humans can independently cut and recover a release.
- At least two people can receive and coordinate private security reports.
- Subsystem ownership, proposal decisions, compatibility policy, and succession
  are public.
- Preview-user feedback has named owners and response expectations.

### Product Evidence

- The primary user claim is supported by a reproducible public evaluation that
  records exact model or tool versions, raw artifacts, failures, uncertainty,
  and the evaluated commit.
- Reference applications exercise the advertised product lanes and pass the
  release gate from installed artifacts.
- Known limitations and non-goals are prominent enough to prevent unsafe use.

## Evidence Record

For a candidate commit, create a release-attached evidence record containing:

- commit and version
- release-gate workflow URL
- conformance result JSON
- scheduled fuzz workflow URLs
- scale/performance report
- artifact hashes and provenance verification
- security review and residual-risk links
- release owner, reviewer, and support owner
- accepted breaking-change proposals and migrations

Evidence expires when a relevant boundary changes. A sandbox rewrite, backend
replacement, packaging redesign, or material compatibility-policy change needs
fresh evidence rather than inheriting an older qualification.

## Current Status

The presence of this checklist does not qualify Geno. In particular, a
single-maintainer project cannot satisfy the independent release and security
ownership criteria, and internal testing does not substitute for independent
security review or public product evidence.

If a blocking criterion regresses, the label returns to preview until the
criterion is restored. Release owners must not waive blockers informally.
