# geno-form

A canvas-based form builder demo. Renders form UIs using the browser
target's graphics primitives -- no DOM manipulation, no HTML form elements.

## What this demonstrates

- **Type-driven field layout** -- Geno type definitions (`InputKind`,
  `FieldDef`) determine which fields appear and how they render.
- **Constraint-driven validation** -- `requires`-style constraints on
  field definitions become runtime validators (`min_length`, `max_length`,
  `min_value`, `max_value`, `required`).
- **Example-driven placeholders** -- `example` clause values serve as
  placeholder text inside rendered fields.

This is a showcase of Geno's type system and the browser target's canvas
rendering pipeline. It does **not** generate real HTML `<form>` elements.

## Usage

```bash
# Type check
geno check --target browser examples/apps/geno-form

# Run self-tests (22 example clauses on pure functions)
geno test examples/apps/geno-form

# Build to HTML
geno build examples/apps/geno-form -o form.html
```

## Architecture

- **Types** -- Core ADTs: `InputKind`, `FieldDef`, `FieldValue`, `FieldError`,
  `FormState`
- **Fields** -- Constructor functions mapping input kinds to `FieldDef` values
- **Validate** -- Validates `FieldValue` against `FieldDef` constraints
- **Render** -- Canvas drawing: text fields, checkboxes, buttons, success state
- **Forms** -- Example form definitions (registration, contact)
- **Main** -- init/update/render lifecycle, tab switching, submit handling

## Why canvas, not HTML forms?

The Geno browser target compiles to a canvas-based rendering loop
(`init`/`update`/`render`), the same architecture used by `geno-dash`.
Real HTML form generation would require a DOM compilation target, which
does not exist yet.

For real HTML forms today, compile to JavaScript with
`geno compile --target js` and integrate the output with a web framework.
