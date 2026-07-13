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

> **Not yet published.** The extension is an unreleased development preview: it is not on the
> VS Code Marketplace, and the packaged `.vsix` currently ships without its runtime dependency,
> so the LSP client cannot start from a packaged install (diagnostics silently fall back to the
> regex checker). Until packaging is fixed, run the extension from source for full functionality.

Package and install a `.vsix` locally (LSP limitation above applies):

```bash
cd vscode-geno
npm ci
npm run compile
npm run package
code --install-extension geno-0.4.0.vsix
```
