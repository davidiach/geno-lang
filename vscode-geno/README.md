# Geno for VS Code

> **Status: Experimental** — This extension tracks Geno's pre-1.0 preview status. See the [maturity matrix](https://github.com/davidiach/geno-lang/blob/main/docs/MATURITY.md) and [preview program](https://github.com/davidiach/geno-lang/blob/main/docs/preview-program.md).

Syntax highlighting and error diagnostics for the [Geno](https://github.com/davidiach/geno-lang) programming language.

## Features

- **Syntax highlighting** for `.geno` files (keywords, types, strings, comments, operators)
- **Error diagnostics** — runs `geno check` on save and shows errors inline
- **Code snippets** for common patterns (func, if, match, type, trait, impl, for, while, try)
- **Comment toggling** and bracket matching

## Requirements

- [Geno](https://github.com/davidiach/geno-lang) must be installed and available on your PATH (`pip install geno-lang`)

## Installation

> **Not yet published.** The extension is an unreleased development preview and is not on the
> VS Code Marketplace. The packaged `.vsix` includes the runtime LSP client.

Building and packaging requires Node.js 22–24 (VSCE 4 requires Node.js 22 or
newer). Package and install a `.vsix` locally:

```bash
cd vscode-geno
npm ci
npm run compile
npm run package
code --install-extension "geno-$(node -p "require('./package.json').version").vsix"
```

`npm run package` names the file after the version in `package.json`, which is
how `scripts/release-gate-vscode.sh` finds it too, so deriving it here keeps the
command correct across version bumps.
