# Geno Preview Program

> **Status: Preview (v0.4.2)** — Geno is available for early evaluation, but
> the structured external beta program has not produced public participant
> metrics or a retrospective yet.

## Overview

The Geno preview program is the pre-beta path for external developers to build
real applications using Geno and report friction in the language, tooling, and
documentation before v1.0.

## Onboarding

### Prerequisites

- Python 3.10-3.13 installed
- Familiarity with at least one statically typed language

### Setup

For a published release:

```bash
pip install geno-lang
geno --version
```

For an unpublished checkout, clone the repository and run
`pip install -e .` from its root.

### First Steps

1. **Create a project** from a template:

   ```bash
   geno init my-app --template cli    # CLI app
   geno init my-app --template web    # Browser app
   geno init my-app --template api    # Multi-module API
   geno init my-app --template lib    # Reusable library
   ```

2. **Run it**:

   ```bash
   cd my-app
   geno run Main.geno
   ```

3. **Run tests** (example-based):

   ```bash
   geno test .
   ```

4. **Type-check**:

   ```bash
   geno check .
   ```

5. **Build for deployment**:

   ```bash
   geno build Main.geno           # Browser app → dist/
   geno compile Main.geno -o app.py  # CLI → Python
   ```

### Resources

- Language spec: `spec.json` (machine-readable)
- LLM prompting guide: `docs/llm-prompting.md`
- Deployment guides: `docs/deploy/`
- Examples: `examples/`
- Package search: `geno search <query>` (the public catalog starts empty)

## Tracking

### Metrics per Developer

| Metric | How to Measure |
|--------|---------------|
| Time to first successful build | Timestamp from `geno init` to first `geno run` with no errors |
| Template used | cli / web / api / lib |
| Bugs filed | GitHub issue count per developer |
| Bug triage time | Time from issue creation to first response |
| Deployed app | Did they produce a working deployable artifact? |
| Geno core patches needed | Did they require changes to the Geno compiler/runtime? |

### Bug Triage Goal

Preview bugs should be triaged promptly:

1. Label with `preview-feedback`
2. Assign severity (P0-P3)
3. Acknowledge in issue comments
4. Fix or document workaround

## Feedback Template

Preview users should use this template when filing issues:

```markdown
**What I tried:**
[Description of what you were trying to build/do]

**What happened:**
[Error message, unexpected behavior, or confusion]

**What I expected:**
[What should have happened]

**Template used:** cli / web / api / lib
**Geno version:** (output of `geno --version`)
**OS:** macOS / Linux / Windows
```

## App Ideas for Preview Users

Suggested projects that exercise different language features:

1. **Todo CLI** (cli template) — CRUD operations, file persistence
2. **Calculator** (web template) — UI events, state management
3. **Markdown previewer** (web template) — String processing, rendering
4. **JSON formatter** (cli template) — Parsing, pretty-printing
5. **Quiz game** (web template) — State machine, scoring

## Retrospective

Before promoting the preview to a public beta, produce a retrospective document
covering:

1. **What worked well** — features, docs, or tooling that developers praised
2. **Pain points** — ranked list of friction points
3. **Bugs found** — categorized by severity and component
4. **Missing features** — what developers asked for that doesn't exist
5. **Documentation gaps** — where developers got stuck
6. **Prioritized findings for v1.0** — ordered list of must-fix items

## Timeline

| Phase | Duration | Activities |
|-------|----------|-----------|
| Recruitment | 1 week | Identify and invite developers |
| Onboarding | 1 week | Setup, first project, initial feedback |
| Building | 2 weeks | Developers build apps, file bugs |
| Retrospective | 1 week | Collect feedback, write findings |
