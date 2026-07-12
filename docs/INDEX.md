# Geno Documentation

## Getting Started

- [Installation & First Program](guide/getting-started.md) -- install Geno, write and run your first program
- [Language Tour](guide/language-tour.md) -- types, functions, pattern matching, pipelines, traits
- [Your First App](guide/first-app.md) -- build a JSON config reader step by step
- [Todo App Tutorial](guide/tutorial-todo-app.md) -- build a complete todo app with ADTs and Result types

## Reference

- [Capability Reference](reference/capabilities.md) -- what `--cap print`, `--cap fs`, etc. unlock
- [Common Pitfalls](reference/common-pitfalls.md) -- the top 10 mistakes and how to fix them
- [Embedding API](reference/embedding-api.md) -- use Geno as a Python library
- [LLM Prompting Guide](llm-prompting.md) -- system prompts and common LLM mistakes
- [Language Specification (v0.2)](spec/v0.2.md) -- formal syntax and semantics
- [Supported Targets](SUPPORTED_TARGETS.md) -- compilation targets and builtin availability
- [Benchmark Results](benchmark/results.md) -- published Geno-vs-Python results or current publication status

## Deployment

- [CLI Apps (Python / Node.js)](deploy/cli.md) -- compile and package CLI programs
- [Browser Apps](deploy/browser.md) -- build HTML apps with `geno build`
- [Hosted Runtime](deploy/hosted.md) -- run `geno serve` as an HTTP API

## Runtime and Security

- [Execution Surface](runtime/execution-surface.md) -- API entry points and builtin families
- [Backend Runtime Contracts](runtime/backend-contracts.md) -- manifest-derived backend and target behavior checks
- [Security Review Notes](security/focused-review-2026-03.md) -- dated focused security review
- [Capability Reference](reference/capabilities.md) -- capability model and target availability

## Research and Benchmarks

- [Benchmark Quality Criteria](benchmark/quality_criteria.md) -- benchmark problem standards
- [Benchmark Validation Contract](benchmark/validation-contract.md) -- corpus validation rules
- [Benchmark Results](../benchmarks/RESULTS.md) -- current or historical performance results
- [LLM Prompting Guide](llm-prompting.md) -- guidance for generating Geno code with LLMs

## Project and Contributing

- [Maturity Matrix](MATURITY.md) -- what's stable, beta, experimental, or research
- [Preview Program](preview-program.md) -- onboarding, prerequisites, current status
- [Product Scope](SCOPE.md) -- target lanes, success criteria, non-goals
- [Reference Apps](REFERENCE_APPS.md) -- CI-validated example applications
- [Changelog](../CHANGELOG.md) -- release history
- [Release Runbook](operations/release-runbook.md) -- release process and rollback plan
- [Monitoring & Support](operations/monitoring-and-support.md) -- production surfaces and incident expectations
- [Local CI](operations/local-ci.md) -- running local validation checks
- [Tiered Testing Design](design/tiered-testing.md) -- example clause tier system
- [Shared IR ADR](adr-shared-ir.md) -- architecture decision record
