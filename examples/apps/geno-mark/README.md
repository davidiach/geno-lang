# geno-mark

A Markdown-to-HTML converter written in Geno, demonstrating how the language scales to non-trivial, multi-module applications.

## What it does

`geno-mark` parses a subset of Markdown and produces styled HTML output with:

- Headings (H1--H6) with anchor IDs
- Bold, italic, and inline code formatting
- Fenced code blocks with language-class annotations
- Bullet and numbered lists
- Blockquotes
- Thematic breaks (horizontal rules)
- Hyperlinks
- Auto-generated table of contents
- Embedded CSS for standalone pages

## Usage

```bash
# For clean raw HTML output, prefer compiled execution.

# Compile to Python, then run with sample markdown
geno compile . -o mark.py
python3 mark.py --cap env,print

# Convert a file
python3 mark.py --cap env,print,fs -- readme.md

# Compile to JavaScript, then run
geno compile . --target js -o mark.js
node mark.js --cap env,print

# HTML fragment only (no page wrapper)
node mark.js --cap env,print,fs -- readme.md --fragment
```

`geno run --cap ...` also works for quick local smoke checks, but it auto-switches to interpreter mode and prints strings in a quoted debug format rather than raw HTML.

## Module structure

| Module | Lines | Purpose |
|---|---|---|
| `Types.geno` | ~16 | Block ADT, ConvertResult record, OutputMode enum |
| `Parser.geno` | ~370 | Line classification, code-fence state machine, adjacent-block merging |
| `Inline.geno` | ~183 | HTML escaping, bold/italic/code/link processing, string utilities |
| `Renderer.geno` | ~197 | Block-to-HTML dispatch, TOC generation, page wrapper with CSS |
| `Main.geno` | ~118 | CLI entry point, capability usage, sample markdown |

**Total: ~884 lines across 5 modules.**

## Geno features demonstrated

- **Algebraic data types**: `Block` with 8 variants models the markdown AST
- **Pattern matching**: `match` dispatches block rendering, merging, and line classification
- **Example clauses**: 40+ functions have `example` clauses serving as inline tests
- **Capabilities**: uses `env` (CLI args), `print` (console output), and `fs` (file I/O)
- **Imports**: 5 modules with a clear dependency graph (Types -> Parser -> Inline -> Renderer -> Main)
- **Higher-order functions**: `map`, `filter`, `fold` for list processing throughout
- **Mutable state**: `var` bindings with `continue` in the code-fence parser loop
- **String builtins**: `string_index_of`, `substring`, `replace`, `starts_with`, `split`, `join`
- **Result/Option types**: `parse_int` returns `Option[Int]`, checked with `is_some`
- **Recursive processing**: inline formatters find-and-replace recursively until no matches remain
- **Pipeline composition**: rendering chains escape -> code -> links -> bold -> italic
