# Geno website

Two complete design directions for the Geno website, built from the same
facts, examples and posts. Pick one; the other can be deleted.

| Directory | Direction | Pages |
|---|---|---|
| `datasheet/` | **Datasheet.** Geno presented as a precision component datasheet: part number, pinout, rated limits, test conditions. Light and dark editions. | landing, `playground.html`, `blog/` (application notes) |
| `dialogue/` | **Dialogue.** A dark, five-act conversation between a model and the compiler, set in two typographic voices. | landing, `playground.html`, `journal/` |
| `index.html` | Chooser with live previews of both. | |
| `404.html` | Shared not-found page for GitHub Pages. | |

Everything is static HTML, CSS and JavaScript with no build-time
dependencies beyond Python's standard library.

## The playground runs the real compiler

`shared/js/runtime-worker.js` loads [Pyodide](https://pyodide.org) (CPython
compiled to WebAssembly) in a Web Worker, unpacks the `geno` package from
`shared/playground/geno.zip`, and calls the real CLI's `main()` for
`geno test -v`, `geno run`, `geno check` and `geno compile`. Nothing is
reimplemented in JavaScript, and nothing is sent to a server.

The bundle is built from `geno/` by `build.py` and is not committed, so it can
never drift from the toolchain it claims to be.

## Build and preview

```bash
python3 website/build.py --verify   # bundle + examples + blog; checks/tests every example
python3 -m http.server -d website 8000
# open http://localhost:8000/
```

`build.py` steps:

1. Zips `geno/` (without tests) into `shared/playground/geno.zip`.
2. Generates `shared/js/examples.js` from `shared/examples/*.geno`. With
   `--verify` each example must pass `geno check` and `geno test`.
3. Renders `shared/posts/*.html` into each direction's blog through its
   `_templates/`, writes an RSS feed, and refreshes the "latest posts" block
   between `<!-- blog:latest -->` markers on each landing page.

Generated blog pages are committed so the site works without a build.

### Self-hosting Pyodide

The runtime loads Pyodide 0.26.4 from `cdn.jsdelivr.net`. To serve it yourself,
copy the Pyodide distribution into `shared/playground/pyodide/` and add
`<meta name="geno-pyodide-base" content="../shared/playground/pyodide/">`
to the playground pages (or set `window.GENO_PYODIDE_BASE`).

## Deploy

`.github/workflows/website.yml` builds the site and deploys it to GitHub Pages.
It runs only when triggered manually:

1. Settings → Pages → Source: **GitHub Actions**.
2. Actions → **website** → Run workflow.

`404.html` links to `/geno-lang/...`, which matches a project site at
`https://davidiach.github.io/geno-lang/`. Update those links for a custom
domain.

## Editing content

| What | Where |
|---|---|
| Blog posts | `shared/posts/*.html`, front matter in the leading comment, then run `build.py` |
| Playground examples | `shared/examples/NN-name.geno`; titles in `EXAMPLE_TITLES` in `build.py` |
| Interactive demos | `shared/js/demos.js`; every printed message was captured from the 0.5.0-rc.1 CLI |
| Benchmark data | `BENCH` in `shared/js/demos.js`, from `benchmarks/RESULTS.md` |
| Landing copy | `datasheet/index.html`, `dialogue/index.html` |

## Placeholders to review

- Blog bylines and dates (posts are written from the changelog and docs, but the
  dates and author name are placeholders).
- `SITE_URL` in `build.py` (used in RSS links) and the `/geno-lang/` paths in
  `404.html`.
- Social preview images (`og:image`) are not set yet.
- Community links point to GitHub Discussions and Issues; add chat or social
  links if the project gets them.
