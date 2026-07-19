# Geno Proposal Process

Proposals make consequential language and platform decisions reviewable before
their implementation becomes expensive. A proposal is a design record, not a
substitute for code, tests, or release evidence.

## When A Proposal Is Required

Open a proposal for changes to:

- language syntax or semantics
- parsing, typing, exhaustiveness, or evaluation rules
- effects, capabilities, sandbox, or host trust boundaries
- a stable public API or standard-library contract
- package, lockfile, module-resolution, or generated-artifact formats
- supported targets or backend guarantees
- compatibility, release, security, or governance policy

Routine fixes and implementation refactors that preserve documented behavior do
not need proposals.

## Lifecycle

1. Start with a GitHub Discussion or issue describing the problem and use case.
2. Copy `0000-template.md` to `NNNN-short-title.md`. Until a number is assigned,
   use `0000` in the pull request.
3. Open the proposal pull request separately from the implementation whenever
   practical.
4. Gather review. Breaking language, security-boundary, and governance changes
   remain open for at least 14 calendar days; other substantial proposals remain
   open for at least 7 days.
5. The project lead records one of: Accepted, Rejected, Withdrawn, or Superseded.
6. After acceptance, assign the permanent number, merge the design record, and
   implement it through ordinary reviewed pull requests.

Proposal status values are Draft, Accepted, Rejected, Withdrawn, and Superseded.
Accepted proposals are commitments to direction, not guarantees that a feature
ships in a particular release. Rejected and superseded proposals remain in the
repository so future discussions retain their context.

## Review Standard

A proposal must describe observable behavior, alternatives, compatibility and
migration impact, security implications, backend parity, testing, rollout, and
unresolved questions. Language changes must say which specification and frozen
conformance cases change. Claims based on benchmarks must identify the corpus,
models or machines, raw artifacts, and statistical method.

Acceptance follows `GOVERNANCE.md`. The implementation author cannot provide
the independent technical review required for a substantial proposal.

## Amendments

Material changes after acceptance use a new proposal that supersedes or amends
the old one. Editorial clarifications may be made by normal pull request when
they do not change behavior, scope, or decision rights.
