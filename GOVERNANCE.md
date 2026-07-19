# Geno Governance

Geno currently operates as a lead-maintainer project. This document makes that
reality explicit while defining the review and succession practices required
for a durable programming-language project. It does not imply that roles are
staffed when they are not.

## Roles

The project lead sets product direction, appoints maintainers, and makes the
final decision when consensus cannot be reached. David Iach is the current
project lead and sole repository-wide code owner.

Maintainers are humans with sustained contributions and merge responsibility
for one or more subsystems. A maintainer is expected to review changes, uphold
compatibility and security policy, and participate in releases. The project
lead records appointments and removals publicly.

Reviewers are contributors trusted for technical review but without automatic
merge or release authority. Release owners and security responders are
temporary operational roles assigned for a specific release or incident.

No person may be counted as a backup release or security owner unless they have
the access and practical knowledge to perform that duty independently.

## Decision Classes

Routine fixes, tests, documentation, and implementation improvements follow the
normal pull-request process. They require green relevant checks and one human
approval from an owner of the affected subsystem.

Substantial changes require a public proposal before implementation. This
includes changes to syntax, semantics, the type system, effects or capabilities,
the stable embedding API, standard-library compatibility, package formats,
supported targets, or production security boundaries. The proposal process is
defined in `docs/proposals/README.md`.

Security-sensitive details may be discussed privately until a fix is available.
Any resulting compatibility or architectural decision receives a public
retrospective proposal or advisory once disclosure is safe.

## Acceptance And Disagreement

Maintainers should seek consensus and record material objections. For a
substantial proposal, acceptance requires the project lead plus at least one
independent technical review. While Geno has only one active maintainer, this
means an external reviewer must be identified; the author approving their own
proposal is not independent review.

When consensus is not possible, the project lead decides and records the
reasoning. Contributors may request reconsideration when they present new
technical evidence or user impact, not merely because a vote was lost.

## Conflicts Of Interest

Reviewers disclose financial, employment, or close personal interests that
could reasonably affect a decision. A conflicted reviewer may provide technical
input but should not be the independent approval for that decision.

## Maintainer Changes And Succession

New maintainers should demonstrate repeated, sound contributions; constructive
review; security awareness; and familiarity with the release gate. Inactive
maintainers may step down or be moved to emeritus status after a public notice.

The serious-beta bar requires at least two humans who can independently run a
release and at least two people able to receive private security reports. Until
that is true, the project should state its bus-factor risk plainly and avoid
claiming organizational production readiness.

If the project lead becomes unavailable, active maintainers may unanimously
appoint an interim lead. If no other maintainer exists, no contributor should
claim release authority solely from repository activity; the project remains
in maintenance-only status until ownership is resolved through the hosting
account's documented recovery process.

## Amendments

Governance changes use the substantial-proposal process and remain open for at
least 14 calendar days before acceptance, except for corrections that do not
alter decision rights.
