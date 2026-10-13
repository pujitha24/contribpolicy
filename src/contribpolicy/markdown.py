"""A small, forgiving Markdown splitter.

It does not render Markdown. It cuts a document into *units* (sentences,
list items, checkbox lines, table rows, code lines and headings), each with
the 1-based source line where it starts, the heading path above it, and the
sentence that introduced it (``lead``) when it is a list item or a code line.

The extractor works on these units, which is what lets every rule carry a
line number and a short quote as evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["Unit", "clean_inline", "parse", "split_sentences"]


@dataclass(frozen=True)
class Unit:
    text: str
    line: int
    kind: str  # prose | item | checkbox | table | code | heading
    headings: tuple[str, ...] = ()
    lead: str = ""
    checked: bool | None = None
    depth: int = 0  # list nesting depth for items


_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")  # any indent: fences inside list items count too
_ATX = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_LIST = re.compile(r"^(\s*)(?:[-*+]|\d{1,3}[.)])\s+(.*)$")
_CHECK = re.compile(r"^\[( |x|X)\]\s*(.*)$")
_TABLE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_HR = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_SETEXT = re.compile(r"^\s{0,3}(=+|-+)\s*$")
_QUOTE = re.compile(r"^\s{0,3}(?:>\s?)+")
_ALERT = re.compile(r"^\s*\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$", re.I)
_FRONT = re.compile(r"^(---|\+\+\+)\s*$")

_IMG = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^()]|\([^)]*\))*\)")
_REFLINK = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
_AUTOLINK = re.compile(r"<(https?://[^>]+)>")
_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_STRONG = re.compile(r"(\*\*|__)(\S(?:.{0,300}?\S)?)\1", re.S)
_EM = re.compile(r"(?<![\w*])([*_])(?=\S)(.+?)(?<=\S)\1(?![\w*])")
_WS = re.compile(r"\s+")

_ABBREV = {"e.g", "i.e", "etc", "vs", "cf", "incl", "approx", "no", "nr", "fig", "eg", "ie"}
_SENT_END = re.compile(r"[.!?][\"'”’)\]*_`]*\s+(?=[\"“'(\[*_`]*[A-Z0-9])")


def _keep_lines(rx: re.Pattern[str], repl: str, s: str) -> str:
    """re.sub that keeps the number of newlines, so line numbers stay correct."""

    def fn(m: re.Match[str]) -> str:
        out = m.expand(repl)
        return out + "\n" * (m.group(0).count("\n") - out.count("\n"))

    return rx.sub(fn, s)


def clean_inline(s: str) -> str:
    """Strip inline Markdown/HTML but keep code spans' backticks.

    Works on multi-line text; the number of newlines is preserved.
    """
    s = s.replace("<!--", " ").replace("-->", " ")
    s = _keep_lines(_IMG, r"\1", s)
    s = _keep_lines(_LINK, r"\1", s)
    s = _keep_lines(_REFLINK, r"\1", s)
    s = _keep_lines(_AUTOLINK, r"\1", s)
    s = _keep_lines(_HTML_TAG, " ", s)
    s = _keep_lines(_STRONG, r"\2", s)
    s = _keep_lines(_EM, r"\2", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    return s


def split_sentences(text: str) -> list[tuple[int, str]]:
    """Return (offset, sentence) pairs. Abbreviations like e.g. do not split."""
    out: list[tuple[int, str]] = []
    start = 0
    for m in _SENT_END.finditer(text):
        end = m.start() + 1
        word = re.search(r"([A-Za-z.]+)\.$", text[start:end])
        if word and word.group(1).lower().rstrip(".") in _ABBREV:
            continue
        piece = text[start : m.end()]
        if piece.strip():
            out.append((start + (len(piece) - len(piece.lstrip())), piece.strip()))
        start = m.end()
    tail = text[start:]
    if tail.strip():
        out.append((start + (len(tail) - len(tail.lstrip())), tail.strip()))
    return out


class _Block:
    __slots__ = ("checked", "depth", "kind", "lines")

    def __init__(self, kind: str, depth: int = 0, checked: bool | None = None):
        self.kind = kind
        self.lines: list[tuple[int, str]] = []
        self.checked = checked
        self.depth = depth


def parse(text: str) -> list[Unit]:
    """Split a Markdown document into units with line numbers."""
    lines = text.splitlines()
    units: list[Unit] = []
    headings: list[tuple[int, str]] = []
    block: _Block | None = None
    in_fence: str | None = None
    fence_lead = ""
    list_lead = ""
    last_sentence = ""
    prev_kind = ""  # kind of the last flushed block

    def heads() -> tuple[str, ...]:
        return tuple(h for _, h in headings)

    def flush() -> None:
        nonlocal block, list_lead, last_sentence, prev_kind
        if block is None:
            return
        b, block = block, None
        raws = [(ln, raw.strip()) for ln, raw in b.lines if raw.strip()]
        if not raws:
            return
        with_nl = clean_inline("\n".join(r for _, r in raws))
        line_nos = [ln for ln, _ in raws]
        joined = with_nl.replace("\n", " ")
        if not joined.strip():
            return
        if b.kind == "prose":
            list_lead = ""
        lead = list_lead if b.kind in ("item", "checkbox") else ""
        sentences = [(off, _WS.sub(" ", t).strip()) for off, t in split_sentences(joined)]
        for off, sent in sentences:
            idx = min(with_nl.count("\n", 0, off), len(line_nos) - 1)
            units.append(
                Unit(
                    text=sent,
                    line=line_nos[idx],
                    kind=b.kind,
                    headings=heads(),
                    lead=lead,
                    checked=b.checked,
                    depth=b.depth,
                )
            )
        if b.kind == "prose":
            last_sentence = sentences[-1][1] if sentences else ""
            if last_sentence.endswith(":"):
                list_lead = last_sentence
            else:
                list_lead = ""
        elif b.kind in ("item", "checkbox"):
            # An item ending with ':' introduces its nested items.
            if sentences and sentences[-1][1].endswith(":") and not list_lead:
                list_lead = sentences[-1][1]
            last_sentence = sentences[-1][1] if sentences else last_sentence
        prev_kind = b.kind

    i = 0
    # Skip YAML/TOML front matter.
    if lines and _FRONT.match(lines[0]):
        for j in range(1, min(len(lines), 200)):
            if lines[j].strip() == lines[0].strip():
                i = j + 1
                break

    while i < len(lines):
        raw = lines[i]
        ln = i + 1
        if in_fence is None:
            # Blockquotes and GitHub alerts: read the quoted text as ordinary Markdown.
            raw = _QUOTE.sub("", raw)
            raw = _ALERT.sub("", raw)
        if in_fence is not None:
            if raw.strip().startswith(in_fence):
                in_fence = None
            elif raw.strip():
                units.append(Unit(text=raw.strip(), line=ln, kind="code", headings=heads(), lead=fence_lead))
            i += 1
            continue
        fm = _FENCE.match(raw)
        if fm:
            flush()
            in_fence = fm.group(1)[0] * 3
            fence_lead = last_sentence
            i += 1
            continue
        if not raw.strip():
            if block is not None and block.kind == "prose":
                flush()
            elif block is not None and block.kind in ("item", "checkbox"):
                # A blank line may separate list items; the item ends here.
                flush()
            i += 1
            continue
        am = _ATX.match(raw)
        if am:
            flush()
            level = len(am.group(1))
            title = _WS.sub(" ", clean_inline(am.group(2))).strip()
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, title))
            units.append(Unit(text=title, line=ln, kind="heading", headings=heads()))
            list_lead = ""
            last_sentence = title
            i += 1
            continue
        # Setext heading: a single prose line followed by === or ---.
        if block is not None and block.kind == "prose" and len(block.lines) == 1 and _SETEXT.match(raw):
            level = 1 if raw.strip().startswith("=") else 2
            title = _WS.sub(" ", clean_inline(block.lines[0][1])).strip()
            hl = block.lines[0][0]
            block = None
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, title))
            units.append(Unit(text=title, line=hl, kind="heading", headings=heads()))
            list_lead = ""
            last_sentence = title
            i += 1
            continue
        if _HR.match(raw):
            flush()
            i += 1
            continue
        if _TABLE.match(raw):
            flush()
            if not _TABLE_SEP.match(raw):
                cells = [c.strip() for c in raw.strip().strip("|").split("|")]
                row = " | ".join(c for c in (clean_inline(x).strip() for x in cells) if c)
                if row:
                    units.append(Unit(text=_WS.sub(" ", row), line=ln, kind="table", headings=heads()))
            i += 1
            continue
        lm = _LIST.match(raw)
        if lm:
            flush()
            if prev_kind not in ("item", "checkbox") and last_sentence.endswith(":"):
                list_lead = last_sentence
            depth = len(lm.group(1).expandtabs(4)) // 2
            body = lm.group(2)
            cm = _CHECK.match(body)
            if cm:
                block = _Block("checkbox", depth, checked=cm.group(1) in "xX")
                block.lines.append((ln, cm.group(2)))
            else:
                block = _Block("item", depth)
                block.lines.append((ln, body))
            i += 1
            continue
        if block is None:
            block = _Block("prose")
        block.lines.append((ln, raw))
        i += 1
    flush()
    return units
