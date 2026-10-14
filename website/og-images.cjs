// Render a 1200x630 social card for each blog post into blog/og/<slug>.png.
//
//     node website/og-images.cjs
//
// Needs Node.js and the playwright package with Chromium (for example
// `npm install --no-save playwright && npx playwright install chromium`).
// The cards are committed; build.py points a post's og:image at its card when
// one exists and at og.png otherwise. Rerun this after changing a post's
// title, number, date, kicker or summary.

"use strict";
const { readFileSync, readdirSync, mkdirSync, mkdtempSync, writeFileSync, rmSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join } = require("node:path");
const { pathToFileURL } = require("node:url");
const { chromium } = require("playwright");

const SITE = __dirname;
const OUT = join(SITE, "blog", "og");

function frontMatter(text) {
  const m = text.match(/^\s*<!--([\s\S]*?)-->/);
  if (!m) throw new Error("post without front matter");
  const meta = {};
  for (const line of m[1].trim().split("\n")) {
    const i = line.indexOf(":");
    if (i > 0) meta[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return meta;
}

const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function card(meta) {
  const fonts = pathToFileURL(join(SITE, "assets", "fonts.css")).href;
  const mark = readFileSync(join(SITE, "shared", "brand", "geno-mark.svg"), "utf8")
    .replace(/<style>[\s\S]*?<\/style>/, "<style>.ink{fill:#0F172A}.accent{fill:#0D9488}</style>")
    .replace("<svg ", '<svg width="46" height="43" ');
  const no = String(meta.number || 0).padStart(3, "0");
  return `<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="${fonts}">
<style>
  * { box-sizing: border-box; margin: 0; }
  body { width: 1200px; height: 630px; background: #f3f5f4; color: #0f172a;
    background-image: linear-gradient(rgba(15,23,42,.045) 1px, transparent 1px),
      linear-gradient(90deg, rgba(15,23,42,.045) 1px, transparent 1px);
    background-size: 24px 24px; font-family: "Barlow", sans-serif;
    display: grid; grid-template-rows: auto 1fr auto; padding: 0 52px; }
  .top, .foot { display: flex; justify-content: space-between; align-items: center;
    font: 500 15px/1 "IBM Plex Mono", monospace; letter-spacing: .14em; color: #58667b; }
  .top { height: 62px; border-bottom: 3px solid #0f172a; margin: 0 -52px; padding: 0 52px; background: #f3f5f4; }
  .top b { color: #0f172a; font-weight: 600; }
  .body { display: flex; flex-direction: column; justify-content: center; gap: 26px; }
  .k { font: 600 17px/1 "IBM Plex Mono", monospace; letter-spacing: .16em; text-transform: uppercase; color: #0a7067; }
  h1 { font: 700 82px/.98 "Barlow Condensed", sans-serif; max-width: 1040px;
    display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
  p { font: 400 27px/1.38 "Barlow", sans-serif; color: #364353; max-width: 980px;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .foot { height: 84px; border-top: 1px solid #d1d9d6; }
  .foot span { display: flex; align-items: center; gap: 16px; }
  .foot b { font: 700 26px/1 "Barlow Condensed", sans-serif; letter-spacing: .14em; color: #0f172a; }
</style></head><body>
  <div class="top"><span><b>GENO</b> BLOG</span><span>NO. ${no} · ${esc(meta.date || "")}</span></div>
  <div class="body">
    <div class="k">${esc(meta.kicker || "Notes")}</div>
    <h1>${esc(meta.title)}</h1>
    <p>${esc(meta.summary || "")}</p>
  </div>
  <div class="foot"><span>${mark}<b>GENO</b></span><span>geno-lang.org/blog</span></div>
</body></html>`;
}

const posts = readdirSync(join(SITE, "shared", "posts"))
  .filter((f) => f.endsWith(".html"))
  .map((f) => frontMatter(readFileSync(join(SITE, "shared", "posts", f), "utf8")));

(async () => {
  mkdirSync(OUT, { recursive: true });
  // A file:// page, so the card can load the site's self-hosted fonts.
  const tmp = mkdtempSync(join(tmpdir(), "geno-og-"));
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1200, height: 630 } });
  for (const meta of posts) {
    const html = join(tmp, "card.html");
    writeFileSync(html, card(meta));
    await page.goto(pathToFileURL(html).href, { waitUntil: "load" });
    await page.evaluate(() => document.fonts.ready);
    const file = join(OUT, `${meta.slug}.png`);
    await page.screenshot({ path: file });
    console.log(`og       blog/og/${meta.slug}.png`);
  }
  await browser.close();
  rmSync(tmp, { recursive: true, force: true });
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
