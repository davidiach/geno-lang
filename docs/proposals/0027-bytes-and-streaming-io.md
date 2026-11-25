# Proposal 0027: First-Class Bytes and Scoped Streaming I/O

- Status: Accepted
- Accepted: 2026-11-25 by David Iach (project lead)
- Authors: Antigravity (drafted and implemented at the project lead's request)
- Created: 2026-11-25
- Discussion: Geno 0.7 general-purpose readiness roadmap (#287)
- Supersedes: None
- Review: No independent human technical review took place. The project lead
  accepted under the sole-maintainer provision in `GOVERNANCE.md`. Review
  received: local validation, automated PR code review on #287 whose findings
  were fixed there, and GitHub CI on #287. These are not independent approval.
- Target: the 0.7 development line (`docs/spec/v0.7.md`, Draft)

## Acceptance Decision

On 2026-11-25 the project lead, David Iach, approved executing the Geno 0.7
general-purpose readiness roadmap including first-class `Bytes`, binary I/O, and
scoped streaming I/O as implemented on #287. This records acceptance for the
0.7 development line.

## Summary

Adds a first-class immutable `Bytes` value type and scoped streaming I/O
builtins across all three backends (interpreter, compiled Python, and compiled
JavaScript):

- `Bytes` represents an immutable sequence of unsigned bytes (`0..255`),
  backed by `bytes` in Python and `Uint8Array` in JavaScript, bounded by
  `max_collection_size`.
- Supports indexing (`b[i] -> Int`), slicing (`b[a:b] -> Bytes`), concatenation
  (`+`), `length(b)` / method syntax, structural equality, lexicographic
  ordering (`Ord`), hashing (`Map`/`Set`), property-check generation/shrinking,
  and `extern func` marshaling.
- Pure conversion and manipulation builtins: `bytes_from_list`,
  `bytes_to_list`, `bytes_from_utf8`, `bytes_to_utf8`, `bytes_from_hex`,
  `bytes_to_hex`, `bytes_from_base64`, `bytes_to_base64`, `bytes_length`,
  `bytes_slice`, and `bytes_concat`.
- Binary and scoped streaming I/O builtins: `fs_read_bytes`, `fs_write_bytes`,
  `fs_each_line`, `fs_each_chunk` (`fs`), `http_fetch_bytes` (`http`), and
  `stdin_each_line` (`stdin`). Streaming callbacks have effect-polymorphic
  types `(Item) -> Result[Bool, String]` that propagate callback effects to the
  caller, close underlying handles in `finally`, stop early on `Ok(false)`,
  propagate `Err(msg)`, and bound individual lines/chunks by
  `max_collection_size` while streaming past total file sizes above that cap.

## Compatibility and Security

- Purely additive: no existing syntax or valid 0.6 program changes.
- All binary/streaming I/O builtins remain fail-closed behind `fs`, `http`, or
  `stdin` capabilities, enforce isolated-run root containment and write quotas,
  and bind `max_collection_size` per interpreter instance.
