# Deploying Geno Browser Applications

## Overview

Geno browser apps use the `init/update/render` lifecycle and compile to
JavaScript + HTML for deployment to any static host.

## Build

### Directory output (recommended)

```bash
geno build Main.geno
```

Creates a `dist/` directory containing:

- `index.html` — entry page referencing `app.js`
- `app.js` — compiled application code

### Custom output directory

```bash
geno build Main.geno -o build/
```

### Single-file output

```bash
geno build Main.geno --single-file
```

Creates a self-contained HTML file with JavaScript inlined.

## Dev Server

For local development with live-reload:

```bash
geno dev Main.geno
```

Opens at http://localhost:3000. The page auto-reloads on file changes.

Options:

```bash
geno dev Main.geno --port 3000 --width 1024 --height 768 --title "My App"
```

## Deployment Targets

### GitHub Pages

```yaml
name: Deploy
on:
  push:
    branches: [main]
jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      pages: write
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install geno-lang
      - run: geno build Main.geno -o dist/
      - uses: actions/upload-pages-artifact@v3
        with:
          path: dist/
      - uses: actions/deploy-pages@v4
```

### Netlify

```bash
geno build Main.geno -o dist/
netlify deploy --prod --dir=dist
```

Or set up automatic deploys with a `netlify.toml`:

```toml
[build]
  command = "pip install geno-lang && geno build Main.geno -o dist/"
  publish = "dist/"
```

### Vercel

```bash
geno build Main.geno -o dist/
vercel --prod dist/
```

### S3 / CloudFront

```bash
geno build Main.geno -o dist/
aws s3 sync dist/ s3://my-bucket/ --delete
```

### Any static host

The `dist/` directory contains only static files. Upload it to any web
server or CDN that serves HTML/JS.

## Migrating from `--single-file`

Earlier versions of Geno produced a single HTML file by default. The
current default is `dist/` directory output, which is preferred because:

- Separate `app.js` enables browser caching and CDN delivery
- Optional source maps (`app.js.map`) enable debugging in DevTools
- Faster incremental rebuilds (only changed assets are regenerated)

If your CI or deployment scripts assume a single file, update them:

```diff
-geno build Main.geno -o app.html
+geno build Main.geno -o dist/
```

Use `--single-file` only when you need a truly self-contained artifact
(e.g., embedding in an iframe, email attachment, or offline distribution).

## Source Maps

Source maps (`app.js.map`) are generated only when requested. They map the
compiled JavaScript back to original `.geno` source files, enabling
debugging in browser DevTools.

```bash
geno build Main.geno -o dist/ --source-map
```

Keep source maps out of production builds unless you intentionally want to
publish the original Geno source for debugging.
