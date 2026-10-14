# Geno website

The Geno website: a landing page set as a component datasheet, a full-size
playground and a blog. Everything is static HTML, CSS and JavaScript with no
build-time dependencies beyond Python's standard library.

| Path | What |
|---|---|
| `index.html` | Landing page. Figure 1 replays a model writing a function, failing an example, repairing it and shipping it. |
| `playground.html` | Full-size playground (the same component as the landing page's section). |
| `blog/` | Generated from `shared/posts/` by `build.py`, with an RSS feed. |
| `404.html` | Not-found page for GitHub Pages. |
| `assets/` | Site stylesheet and page script. |
| `shared/` | Playground runtime, demos, highlighter, examples, posts, brand marks. |
| `_templates/` | Blog templates used by `build.py`. |

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
3. Renders `shared/posts/*.html` into `blog/` through `_templates/`, writes an
   RSS feed, and refreshes the "latest posts" block between
   `<!-- blog:latest -->` markers on the landing page.

Generated blog pages are committed so the site works without a build.

### Self-hosting Pyodide

The runtime loads Pyodide 0.26.4 from `cdn.jsdelivr.net`. To serve it yourself,
copy the Pyodide distribution into `shared/playground/pyodide/` and add
`<meta name="geno-pyodide-base" content="shared/playground/pyodide/">`
to the pages that host a playground (or set `window.GENO_PYODIDE_BASE`).
`geno-bundle-url` and `geno-stdlib-url` override the bundle and Python
standard-library archive locations the same way.

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
| Hero replay | `assets/replay.js`; every printed line was captured from the 0.5.0 CLI |
| Interactive demos | `shared/js/demos.js`; same rule |
| Benchmark data | `BENCH` in `shared/js/demos.js`, from `benchmarks/RESULTS.md` |
| Landing copy | `index.html` |

## Placeholders to review

- Blog posts are bylined "The Geno project" and dated 2026-10-14, the launch
  date. Change the byline or dates in each post's front matter if you prefer.
- `SITE_URL` in `build.py` (used in RSS links), the `og:image` URLs, and the
  `/geno-lang/` paths in `404.html`.
- Community links point to GitHub Discussions and Issues; add chat or social
  links if the project gets them.
- The version (0.5.0) is written into the pages by hand; search for it
  when the next release ships.

An earlier second design direction ("Dialogue") was retired in favour of this
one; it is in the git history at commit `a28c457`.

The social card `og.png` is a 1200×630 screenshot of an HTML mock of
Figure 1's failing step; regenerate it if the replay's program changes.
