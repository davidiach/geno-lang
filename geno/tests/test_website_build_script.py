"""Tests for website/build.py and its Markdown renderer, website/md_render.py."""

from __future__ import annotations

import html
import importlib.util
import posixpath
import re
import shutil
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "website"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


md: Any = _load("md_render", SITE / "md_render.py")
build: Any = _load("website_build_test", SITE / "build.py")


def _html(text: str, **kwargs: Any) -> str:
    return str(md.render(text, **kwargs).html)


class TestMarkdownBlocks:
    def test_first_h1_is_the_title_and_leaves_the_body(self) -> None:
        doc = md.render("# Language Tour\n\nIntro text.\n\n## Variables and Types\n")
        assert doc.title == "Language Tour"
        assert doc.description == "Intro text."
        assert "<h1" not in doc.html
        assert '<h2 id="variables-and-types">Variables and Types</h2>' in doc.html

    def test_heading_ids_follow_github_and_dedupe(self) -> None:
        doc = md.render(
            "# T\n\n## `geno.run(source) -> RunResult`\n\n## Notes\n\n## Notes\n"
        )
        assert [h.id for h in doc.headings] == [
            "genorunsource---runresult",
            "notes",
            "notes-1",
        ]

    def test_setext_heading(self) -> None:
        assert '<h2 id="setup">Setup</h2>' in _html("# T\n\nSetup\n-----\n")

    def test_fence_language_and_escaping(self) -> None:
        out = _html("```geno\nfunc f() -> Int\nend func\n```\n")
        assert out == (
            '<pre tabindex="0"><code data-lang="geno">func f() -&gt; Int\nend func</code></pre>'
        )
        assert '<code data-lang="sh">' in _html("```bash\npip install geno-lang\n```\n")
        assert "<code>" in _html("```ebnf\nrule = x ;\n```\n")

    def test_unlabelled_fence_is_detected_as_geno_or_shell(self) -> None:
        assert 'data-lang="geno"' in _html("```\nfunc g() -> Int\nend func\n```\n")
        assert 'data-lang="sh"' in _html("```\n$ geno test app.geno\n```\n")
        assert '<pre tabindex="0"><code>plain\ntext</code></pre>' in _html(
            "```\nplain\ntext\n```\n"
        )

    def test_markdown_inside_fences_is_literal(self) -> None:
        out = _html("```\n# not a heading\n| a | b |\n|---|---|\n```\n")
        assert "<h2" not in out
        assert "<table" not in out

    def test_table_alignment_and_escaped_pipes(self) -> None:
        out = _html("| Field | Type |\n|---|--:|\n| `t` | `float \\| None` |\n")
        assert '<th scope="col" style="text-align:right">Type</th>' in out
        assert "<code>float | None</code>" in out
        assert out.startswith('<div class="tbl-scroll" tabindex="0" role="region"')

    def test_tight_and_nested_lists(self) -> None:
        out = _html("- one\n- two\n  - inner\n- three\n")
        assert out == (
            "<ul>\n<li>one</li>\n<li>two\n<ul>\n<li>inner</li>\n</ul></li>\n"
            "<li>three</li>\n</ul>"
        )

    def test_loose_ordered_list_keeps_paragraphs(self) -> None:
        out = _html("3. first\n\n   more\n4. second\n")
        assert out.startswith('<ol start="3">')
        assert "<li><p>first</p>\n<p>more</p></li>" in out

    def test_list_item_with_fenced_code(self) -> None:
        out = _html(
            "1. Install:\n\n   ```bash\n   pip install geno-lang\n   ```\n2. Run\n"
        )
        assert (
            '<pre tabindex="0"><code data-lang="sh">pip install geno-lang</code></pre>'
            in out
        )
        assert out.count("<li>") == 2

    def test_task_items(self) -> None:
        out = _html("- [ ] todo\n- [x] done\n")
        assert '<ul class="tasks">' in out
        assert '<li class="task"><input type="checkbox" disabled> todo</li>' in out
        assert "disabled checked> done" in out

    def test_numbered_line_does_not_interrupt_a_paragraph(self) -> None:
        assert _html("Released in\n2026. Then more.\n") == (
            "<p>Released in\n2026. Then more.</p>"
        )

    def test_blockquote_with_lazy_continuation(self) -> None:
        assert _html("> **Status:** draft\nstill quoted\n") == (
            "<blockquote>\n<p><strong>Status:</strong> draft\nstill quoted</p>\n</blockquote>"
        )


class TestMarkdownInline:
    def test_code_spans_are_literal(self) -> None:
        assert _html("Use `**x**` and `<b>`.") == (
            "<p>Use <code>**x**</code> and <code>&lt;b&gt;</code>.</p>"
        )

    def test_emphasis_strong_and_strike(self) -> None:
        assert _html("**bold\nacross** *em* _also_ ~~gone~~ snake_case_name") == (
            "<p><strong>bold\nacross</strong> <em>em</em> <em>also</em> "
            "<del>gone</del> snake_case_name</p>"
        )

    def test_links_are_mapped_and_labels_rendered(self) -> None:
        out = _html(
            "See [the `tour`](guide/tour.md#types).",
            link=lambda url: url.replace(".md", ".html"),
        )
        assert (
            out
            == '<p>See <a href="guide/tour.html#types">the <code>tour</code></a>.</p>'
        )

    def test_reference_bare_and_angle_links(self) -> None:
        out = _html(
            "[release][rc] and <https://a.example/x> and https://b.example/y.\n\n"
            "[rc]: https://example.com/rc\n"
        )
        assert '<a href="https://example.com/rc" rel="noopener">release</a>' in out
        assert '<a href="https://a.example/x" rel="noopener">' in out
        assert (
            '<a href="https://b.example/y" rel="noopener">https://b.example/y</a>.'
            in out
        )
        assert "[rc]:" not in out

    def test_raw_html_and_script_urls_are_neutralised(self) -> None:
        out = _html('<script>alert(1)</script> [x](javascript:alert(1)) a "quote"')
        assert "<script>" not in out
        assert "&lt;script&gt;" in out
        assert 'href="#"' in out

    def test_backslash_escapes(self) -> None:
        assert _html(r"\*not em\* and \[not link\]") == "<p>*not em* and [not link]</p>"


class TestDocLinks:
    def test_relative_links_map_to_hosted_pages_or_github(self) -> None:
        pages = build.doc_pages()
        link = build.doc_link(
            "docs/guide/getting-started.md",
            pages["docs/guide/getting-started.md"],
            pages,
        )
        assert link("language-tour.md#types") == "language-tour.html#types"
        assert link("../reference/capabilities.md") == "../reference/capabilities.html"
        assert link("../INDEX.md") == "../"
        assert link("../../CHANGELOG.md") == "../changelog.html"
        assert link("../../SECURITY.md") == f"{build.GITHUB}/blob/main/SECURITY.md"
        assert link("../../examples/") == f"{build.GITHUB}/tree/main/examples"
        assert link("#local") == "#local"
        assert (
            link("https://pypi.org/project/geno-lang/")
            == "https://pypi.org/project/geno-lang/"
        )
        assert (
            link(f"{build.GITHUB}/blob/main/docs/MATURITY.md#status")
            == "../MATURITY.html#status"
        )

    def test_every_docs_markdown_file_is_hosted(self) -> None:
        pages = build.doc_pages()
        sources = {
            p.relative_to(ROOT).as_posix() for p in (ROOT / "docs").rglob("*.md")
        }
        assert sources <= set(pages)
        assert pages["docs/INDEX.md"] == "docs/index.html"
        assert pages["CHANGELOG.md"] == "docs/changelog.html"


class TestBuildDocs:
    @pytest.fixture
    def built(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        shutil.copytree(SITE / "_templates", tmp_path / "_templates")
        monkeypatch.setattr(build, "SITE", tmp_path)
        build.build_docs()
        return tmp_path

    def test_pages_render_without_leftover_markdown(self, built: Path) -> None:
        for page in build.doc_pages().values():
            text = (built / page).read_text()
            assert "{{" not in text, page
            body = text[text.index("<article") : text.index("</article>")]
            prose = re.sub(
                r"<pre[^>]*>.*?</pre>|<code>.*?</code>", "", body, flags=re.S
            )
            assert "\x00" not in prose, page
            assert "```" not in prose, page
            assert "](" not in html.unescape(re.sub(r"<[^>]+>", "", prose)), page

    def test_internal_links_and_anchors_resolve(self, built: Path) -> None:
        ids: dict[Path, set[str]] = {}

        def anchors(path: Path) -> set[str]:
            if path not in ids:
                ids[path] = set(re.findall(r'\bid="([^"]+)"', path.read_text()))
            return ids[path]

        broken = []
        for page in build.doc_pages().values():
            path = built / page
            for href in re.findall(r'<a href="([^"]+)"', path.read_text()):
                href = html.unescape(href)
                if re.match(r"^[a-z]+:|^//", href):
                    continue
                target, _, frag = href.partition("#")
                if not target:
                    dest = path
                else:
                    dest = (path.parent / target).resolve()
                    if target.endswith("/"):
                        dest = dest / "index.html"
                    # Links into the rest of the site (blog, playground) are
                    # checked elsewhere; the docs build only owns docs/.
                    if not dest.is_relative_to(built / "docs"):
                        continue
                if not dest.exists():
                    broken.append(f"{page}: {href}")
                elif frag and frag not in anchors(dest):
                    broken.append(f"{page}: {href} (no #{frag})")
        assert broken == []


class TestFeed:
    def test_posts_sharing_a_date_keep_their_number_order(self) -> None:
        def post(n: int) -> Any:
            return build.Post(
                slug=f"p{n}",
                title="t",
                date=date(2026, 10, 14),
                author="a",
                summary="s",
                number=n,
            )

        assert build.published_at(post(7)) > build.published_at(post(1))
        feed = build.rss([post(2), post(1)])
        assert "<pubDate>Wed, 14 Oct 2026 12:02:00 +0000</pubDate>" in feed
        assert 'rel="self"' in feed


class TestSitePagesLinkToHostedDocs:
    def test_hand_written_docs_links_point_at_generated_pages(self) -> None:
        hosted = {"docs/"} | set(build.doc_pages().values())
        pages = [
            SITE / "index.html",
            SITE / "playground.html",
            *sorted((SITE / "blog").glob("*.html")),
        ]
        missing = []
        for page in pages:
            base = page.parent.relative_to(SITE).as_posix()
            for href in re.findall(r'href="([^"#]*docs/[^"#]*)', page.read_text()):
                if re.match(r"^[a-z]+:", href):
                    continue
                target = posixpath.normpath(posixpath.join(base, href))
                if href.endswith("/"):
                    target += "/"
                if target not in hosted:
                    missing.append(f"{page.relative_to(SITE)}: {href}")
        assert missing == []
