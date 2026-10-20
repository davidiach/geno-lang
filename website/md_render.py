"""A small Markdown renderer for the website's docs pages.

It covers the subset the repository's docs use: ATX and setext headings,
paragraphs, fenced code, GFM pipe tables, nested lists with task items, block
quotes, thematic breaks, code spans, emphasis, strikethrough, and inline,
reference, angle-bracket and bare links. Raw HTML in the source is escaped,
never passed through. Like build.py it needs only the standard library.

    doc = render(text, link=lambda url: url)
    doc.title, doc.description, doc.html, doc.headings
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Callable

LinkMap = Callable[[str], str]

# Fence languages the site highlighter knows (shared/js/highlight.js).
LANGS = {
    "geno": "geno",
    "sh": "sh",
    "bash": "sh",
    "shell": "sh",
    "console": "sh",
    "python": "python",
    "py": "python",
    "js": "js",
    "javascript": "js",
    "toml": "toml",
}
GENO_HINT = re.compile(
    r"\bend (?:func|if|for|while|match|type|trait|impl|try)\b"
    r"|^(?:func|type|trait|impl|import|export) ",
    re.M,
)

FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})\s*([^`]*)$")
ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
HR = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
QUOTE = re.compile(r"^ {0,3}> ?(.*)$")
LIST = re.compile(r"^( {0,3})([-*+]|\d{1,9}[.)])(?:([ \t]+)(.*))?$")
TABLE_DELIM = re.compile(
    r"^ {0,3}\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$"
)
REF_DEF = re.compile(
    r"^ {0,3}\[([^\]]+)\]:[ \t]*<?(\S+?)>?(?:[ \t]+[\"'(].*[\"')])?[ \t]*$"
)
TASK = re.compile(r"^\[([ xX])\][ \t]+")


@dataclass
class Heading:
    level: int
    text: str
    id: str


@dataclass
class Doc:
    title: str = ""
    description: str = ""
    html: str = ""
    headings: list[Heading] = field(default_factory=list)


def slugify(text: str) -> str:
    """GitHub's heading anchors: lowercase, drop punctuation, spaces to hyphens."""
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


def plain(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


class _Renderer:
    def __init__(self, link: LinkMap) -> None:
        self.link = link
        self.refs: dict[str, str] = {}
        self.slugs: dict[str, int] = {}
        self.headings: list[Heading] = []
        self.title = ""
        self.description = ""

    # ------------------------------------------------------------- blocks
    def blocks(self, lines: list[str]) -> list[str]:
        out: list[str] = []
        i, n = 0, len(lines)
        while i < n:
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            m = FENCE.match(line)
            if m:
                i = self.fence(lines, i, m, out)
                continue
            m = ATX.match(line)
            if m:
                self.heading(len(m.group(1)), m.group(2) or "", out)
                i += 1
                continue
            if HR.match(line):
                out.append("<hr>")
                i += 1
                continue
            if QUOTE.match(line):
                i = self.quote(lines, i, out)
                continue
            if self.table_starts(lines, i):
                i = self.table(lines, i, out)
                continue
            if LIST.match(line):
                i = self.list_block(lines, i, out)
                continue
            i = self.paragraph(lines, i, out)
        return out

    def starts_block(self, lines: list[str], i: int) -> bool:
        line = lines[i]
        if not line.strip():
            return True
        if FENCE.match(line) or ATX.match(line) or HR.match(line) or QUOTE.match(line):
            return True
        m = LIST.match(line)
        # As in CommonMark, only an ordered list that starts at 1 can interrupt
        # a paragraph, so "2026. Something" stays text.
        if m and (m.group(4) or "").strip() and m.group(2)[:-1] in ("", "1"):
            return True
        return self.table_starts(lines, i)

    def table_starts(self, lines: list[str], i: int) -> bool:
        return (
            "|" in lines[i]
            and i + 1 < len(lines)
            and "-" in lines[i + 1]
            and bool(TABLE_DELIM.match(lines[i + 1]))
        )

    def fence(self, lines: list[str], i: int, m: re.Match[str], out: list[str]) -> int:
        indent, mark, info = len(m.group(1)), m.group(2), m.group(3).strip()
        body: list[str] = []
        i += 1
        while i < len(lines):
            line = lines[i]
            close = re.match(r"^ {0,3}(`{3,}|~{3,})[ \t]*$", line)
            if (
                close
                and close.group(1)[0] == mark[0]
                and len(close.group(1)) >= len(mark)
            ):
                i += 1
                break
            strip = len(line) - len(line.lstrip(" "))
            body.append(line[min(indent, strip) :])
            i += 1
        code = "\n".join(body)
        word = info.split()[0].lower() if info else ""
        lang = LANGS.get(word)
        if not word:
            if GENO_HINT.search(code):
                lang = "geno"
            elif re.match(r"^\$ ", code):
                lang = "sh"
        attr = f' data-lang="{lang}"' if lang else ""
        # Focusable, so a listing wider than the page can be scrolled from the keyboard.
        out.append(
            f'<pre tabindex="0"><code{attr}>{html.escape(code, quote=False)}</code></pre>'
        )
        return i

    def heading(self, level: int, text: str, out: list[str]) -> None:
        inner = self.inline(text.strip())
        label = plain(inner)
        if level == 1 and not self.title:
            self.title = label
            return
        base = slugify(label) or "section"
        count = self.slugs.get(base, 0)
        self.slugs[base] = count + 1
        hid = base if count == 0 else f"{base}-{count}"
        level = max(2, level)
        self.headings.append(Heading(level, label, hid))
        out.append(f'<h{level} id="{hid}">{inner}</h{level}>')

    def quote(self, lines: list[str], i: int, out: list[str]) -> int:
        body: list[str] = []
        while i < len(lines):
            m = QUOTE.match(lines[i])
            if m:
                body.append(m.group(1))
            elif (
                lines[i].strip()
                and body
                and body[-1].strip()
                and not self.starts_block(lines, i)
            ):
                body.append(lines[i].lstrip())  # lazy continuation
            else:
                break
            i += 1
        out.append("<blockquote>\n" + "\n".join(self.blocks(body)) + "\n</blockquote>")
        return i

    def table(self, lines: list[str], i: int, out: list[str]) -> int:
        head = self.cells(lines[i])
        aligns = []
        for cell in self.cells(lines[i + 1]):
            left, right = cell.startswith(":"), cell.endswith(":")
            aligns.append(
                "center"
                if left and right
                else "right"
                if right
                else "left"
                if left
                else ""
            )
        i += 2
        rows = []
        while i < len(lines) and lines[i].strip() and "|" in lines[i]:
            rows.append(self.cells(lines[i]))
            i += 1

        def row(cells: list[str], tag: str) -> str:
            parts = []
            for k in range(len(head)):
                text = cells[k] if k < len(cells) else ""
                align = aligns[k] if k < len(aligns) else ""
                style = f' style="text-align:{align}"' if align else ""
                scope = ' scope="col"' if tag == "th" else ""
                parts.append(f"<{tag}{scope}{style}>{self.inline(text)}</{tag}>")
            return "<tr>" + "".join(parts) + "</tr>"

        body = "\n".join(row(r, "td") for r in rows)
        out.append(
            '<div class="tbl-scroll" tabindex="0" role="region" aria-label="Table">'
            f"<table>\n<thead>{row(head, 'th')}</thead>\n<tbody>\n{body}\n</tbody>\n</table></div>"
        )
        return i

    @staticmethod
    def cells(line: str) -> list[str]:
        s = line.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|") and not s.endswith("\\|"):
            s = s[:-1]
        return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]

    def list_block(self, lines: list[str], i: int, out: list[str]) -> int:
        first = LIST.match(lines[i])
        assert first
        ordered = first.group(2)[-1] in ".)"
        kind = first.group(2)[-1] if ordered else first.group(2)
        start = int(first.group(2)[:-1]) if ordered else 1
        items: list[list[str]] = []
        loose = False
        content = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            if not line.strip():
                j = i + 1
                while j < n and not lines[j].strip():
                    j += 1
                if not items or j >= n:
                    break
                nxt = lines[j]
                ind = len(nxt) - len(nxt.lstrip(" "))
                mm = LIST.match(nxt)
                if ind >= content:
                    items[-1].extend([""] * (j - i))
                    i = j
                    continue
                if mm and self.same_list(mm, ordered, kind) and ind < content:
                    loose = True
                    i = j
                    continue
                break
            mm = LIST.match(line)
            ind = len(line) - len(line.lstrip(" "))
            if (
                mm
                and (not items or ind < content)
                and self.same_list(mm, ordered, kind)
            ):
                rest = mm.group(4) or ""
                gap = len(mm.group(3) or " ")
                if gap > 4 or not rest.strip():
                    gap = 1
                content = len(mm.group(1)) + len(mm.group(2)) + gap
                items.append([rest])
                i += 1
                continue
            if mm and ind < content:
                break  # a different kind of list starts here
            if items and ind >= content:
                items[-1].append(line[content:])
                i += 1
                continue
            if (
                items
                and items[-1]
                and items[-1][-1].strip()
                and not self.starts_block(lines, i)
            ):
                items[-1].append(line.lstrip())  # lazy continuation
                i += 1
                continue
            break
        for item in items:
            while item and not item[-1].strip():
                item.pop()
            # A blank line between two blocks of one item also makes the list loose.
            loose = loose or self.has_block_gap(item)
        tag = "ol" if ordered else "ul"
        attr = f' start="{start}"' if ordered and start != 1 else ""
        lis = []
        has_tasks = False
        for item in items:
            task = ""
            if item and TASK.match(item[0]):
                tm = TASK.match(item[0])
                assert tm
                checked = " checked" if tm.group(1) in "xX" else ""
                task = f'<input type="checkbox" disabled{checked}> '
                item[0] = item[0][tm.end() :]
                has_tasks = True
            parts = self.blocks(item)
            if not loose:
                parts = [re.sub(r"^<p>(.*)</p>$", r"\1", p, flags=re.S) for p in parts]
            cls = ' class="task"' if task else ""
            lis.append(f"<li{cls}>{task}" + "\n".join(parts) + "</li>")
        cls = ' class="tasks"' if has_tasks else ""
        out.append(f"<{tag}{attr}{cls}>\n" + "\n".join(lis) + f"\n</{tag}>")
        return i

    @staticmethod
    def has_block_gap(item: list[str]) -> bool:
        """True when a blank line separates two paragraphs of an item (outside fences)."""
        in_fence = False
        seen_text = False
        gap = False
        for s in item:
            if FENCE.match(s):
                in_fence = not in_fence
                seen_text = True
                continue
            if in_fence:
                continue
            if not s.strip():
                gap = seen_text
                continue
            if gap and not LIST.match(s):
                return True
            seen_text = True
            gap = False
        return False

    @staticmethod
    def same_list(m: re.Match[str], ordered: bool, kind: str) -> bool:
        marker = m.group(2)
        if ordered:
            return marker[-1] in ".)" and marker[-1] == kind
        return marker == kind

    def paragraph(self, lines: list[str], i: int, out: list[str]) -> int:
        text: list[str] = []
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                break
            if text and SETEXT.match(line):
                level = 1 if line.strip()[0] == "=" else 2
                self.heading(level, " ".join(t.strip() for t in text), out)
                return i + 1
            if text and self.starts_block(lines, i):
                break
            if not text and REF_DEF.match(line):
                i += 1
                continue
            text.append(line)
            i += 1
        if not text:
            return i
        # Keep trailing spaces until inline(): two or more end a hard break.
        joined = "\n".join(t.lstrip() for t in text).rstrip()
        inner = self.inline(joined)
        if not self.description:
            self.description = " ".join(plain(inner).split())
        out.append(f"<p>{inner}</p>")
        return i

    # ------------------------------------------------------------- inline
    def inline(self, text: str, slots: list[str] | None = None) -> str:
        """Render inline Markdown. Nested calls (link labels) share ``slots``
        with their caller, which restores every placeholder once at the end."""
        top = slots is None
        buf: list[str] = [] if slots is None else slots

        def keep(fragment: str) -> str:
            buf.append(fragment)
            return f"\x00{len(buf) - 1}\x00"

        def resolve(fragment: str) -> str:
            while "\x00" in fragment:
                fragment = re.sub(
                    r"\x00(\d+)\x00", lambda m: buf[int(m.group(1))], fragment
                )
            return fragment

        def attr_text(fragment: str) -> str:
            # Placeholders hold markup; inside an attribute only their text may
            # appear, and the caller escapes it with quotes.
            return plain(resolve(fragment))

        # Code spans first: nothing inside them is Markdown.
        text = re.sub(
            r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)",
            lambda m: keep(
                "<code>" + html.escape(_code_text(m.group(2)), quote=False) + "</code>"
            ),
            text,
            flags=re.S,
        )
        text = re.sub(
            r"<(https?://[^\s<>\x00]+)>",
            lambda m: keep(
                self.anchor(m.group(1), html.escape(m.group(1), quote=False))
            ),
            text,
        )
        text = re.sub(
            r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~])",
            lambda m: keep(html.escape(m.group(1))),
            text,
        )

        def link(m: re.Match[str]) -> str:
            label, url = m.group(2), attr_text(m.group(3))
            if m.group(1):
                src = self.link(url)
                if re.match(r"^\s*(?:javascript|vbscript):", src, re.I):
                    src = ""
                alt = attr_text(self.inline(label, buf))
                return keep(
                    f'<img src="{html.escape(src)}" alt="{html.escape(alt)}" loading="lazy">'
                )
            return keep(self.anchor(url, self.inline(label, buf)))

        bracket = r"\[((?:[^\[\]]|\[[^\[\]]*\])*)\]"
        text = re.sub(
            r"(!?)" + bracket + r"\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)",
            link,
            text,
        )

        def ref(m: re.Match[str], label: str, key: str) -> str:
            url = self.refs.get(key.strip().lower())
            if url is None:
                return str(m.group(0))
            return keep(self.anchor(url, self.inline(label, buf)))

        # [text][key], [text][] and the shortcut [key]
        text = re.sub(
            bracket + r"\[([^\]]*)\]",
            lambda m: ref(m, m.group(1), m.group(2) or m.group(1)),
            text,
        )
        text = re.sub(
            bracket + r"(?!\(|\[)", lambda m: ref(m, m.group(1), m.group(1)), text
        )
        text = re.sub(
            r"(?<![\w\"'=/])(https?://[^\s<>\x00]*[^\s<>\x00.,:;\"')\]])",
            lambda m: keep(
                self.anchor(m.group(1), html.escape(m.group(1), quote=False))
            ),
            text,
        )

        text = html.escape(text, quote=False)
        text = re.sub(
            r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", r"<strong>\2</strong>", text, flags=re.S
        )
        text = re.sub(
            r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])",
            r"<em>\1</em>",
            text,
            flags=re.S,
        )
        text = re.sub(
            r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])", r"<em>\1</em>", text, flags=re.S
        )
        text = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<del>\1</del>", text, flags=re.S)
        text = re.sub(r"(?: {2,}|\\)\n", "<br>\n", text)

        return resolve(text) if top else text

    def anchor(self, url: str, label: str) -> str:
        href = self.link(url)
        if re.match(r"^\s*(?:javascript|vbscript|data):", href, re.I):
            href = "#"
        external = href.startswith(("http://", "https://"))
        rel = ' rel="noopener"' if external else ""
        return f'<a href="{html.escape(href)}"{rel}>{label}</a>'


def _code_text(s: str) -> str:
    s = s.replace("\n", " ")
    if s.startswith(" ") and s.endswith(" ") and s.strip():
        s = s[1:-1]
    return s


def render(text: str, link: LinkMap | None = None) -> Doc:
    """Render Markdown ``text``. ``link`` maps each link target (for rewriting)."""
    r = _Renderer(link or (lambda url: url))
    lines = text.expandtabs(4).replace("\r\n", "\n").split("\n")
    in_fence = False
    for line in lines:
        if FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            m = REF_DEF.match(line)
            if m:
                r.refs[m.group(1).strip().lower()] = m.group(2)
    body = "\n".join(r.blocks(lines))
    description = r.description
    if len(description) > 180:
        description = description[:177].rsplit(" ", 1)[0] + "…"
    return Doc(title=r.title, description=description, html=body, headings=r.headings)
