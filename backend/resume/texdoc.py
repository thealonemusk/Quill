"""
LaTeX resume as a frozen document plus a handful of editable text spans.

This is the piece that makes tailoring safe. The previous attempt at this
feature (`backend/ai/resume_tailor.py`, deleted in a680b93) asked the model to
emit an entire `.tex` document as a JSON string; the output could not be trusted
to compile and there was no compiler to check it with.

Here the model never sees or writes LaTeX. The source is kept verbatim and only
*spans* of plain prose inside it are replaced, so:

  * the preamble, document class, custom macros and section scaffolding are
    untouched by construction — the PDF keeps the author's exact design;
  * rendering with no edits returns the input byte for byte;
  * a span is only offered for rewriting when it is plain text. A bullet holding
    maths or nested commands can still be reordered or dropped, just not reworded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set

# Characters that must be escaped when plain text goes back into LaTeX.
# Order matters: the backslash has to be replaced first or it would corrupt the
# replacements made for the other characters.
_ESCAPES: Sequence[tuple[str, str]] = (
    ("\\", r"\textbackslash{}"),
    ("{", r"\{"),
    ("}", r"\}"),
    ("&", r"\&"),
    ("%", r"\%"),
    ("$", r"\$"),
    ("#", r"\#"),
    ("_", r"\_"),
    ("~", r"\textasciitilde{}"),
    ("^", r"\textasciicircum{}"),
)

# The only backslash sequences a span may contain and still count as plain text.
_ALLOWED_ESCAPE_RE = re.compile(r"\\[%&$#_{}]")

# Anything else beginning with a backslash means the span carries markup.
_ANY_COMMAND_RE = re.compile(r"\\[A-Za-z@]+|\\\\")

_SECTION_RE = re.compile(r"\\(?:section|subsection)\*?\{([^{}]*)\}")

# Entry headings: the company/project line a bullet belongs under. Covers the
# common resume-template spellings plus a bare \textbf{...} lead-in.
_ENTRY_MACRO_RE = re.compile(
    r"\\(?:role|resumeSubheading|resumeProjectHeading|resumeSubItem|cventry|entry|textbf)\s*\{"
)

_ITEM_RE = re.compile(r"\\item\b")

# Macro-style bullets: \resumeItem{...}, \cvitem{...}. The editable part is the
# final brace group.
_MACRO_ITEM_RE = re.compile(r"\\(resumeItem|cvitem|achievement)\s*\{")


# Letter pairs TeX fuses into a single ligature glyph. XeTeX writes that glyph to
# the PDF as U+FB00-FB04, so extracting the text yields "traﬃc" and an ATS
# searching for "traffic" finds nothing.
#
# The usual `f{}f` idiom does NOT help here: XeTeX shapes through empty groups,
# so the ligature still forms. A zero-width kern does break the shaping run —
# measured 10/10 words extracting as plain ASCII versus 6/10 with `{}`.
_LIGATURE_PAIRS = ("ffi", "ffl", "ff", "fi", "fl")
_LIGATURE_BREAK = "\\kern0pt "


def break_ligatures(text: str) -> str:
    """Split ligature-forming letter runs so the PDF stays greppable."""
    out = text
    for pair in _LIGATURE_PAIRS:
        out = out.replace(pair, pair[0] + _LIGATURE_BREAK + pair[1:])
    return out


_ESCAPE_MAP = dict(_ESCAPES)
_ESCAPE_RE = re.compile("|".join(re.escape(ch) for ch, _ in _ESCAPES))


def escape(text: str) -> str:
    """
    Plain text -> LaTeX-safe text, with ligatures defused.

    One pass over the input. Replacing character by character re-escaped the
    braces of `\\textbackslash{}` itself and printed `\\textbackslash\\{\\}`.
    """
    return break_ligatures(_ESCAPE_RE.sub(lambda m: _ESCAPE_MAP[m.group(0)], text))


# Injected just before \begin{document}. Handles the frozen parts of the
# document, which `escape()` never sees.
HARDENING = r"""
%% --- ATS hardening (injected by Quill) ---------------------------------
%% Suppress OpenType ligatures so extracted text stays plain ASCII, and stop
%% hyphenation splitting a keyword across two lines.
\usepackage{fontspec}
\defaultfontfeatures{RawFeature={-liga;-clig;-rlig;-dlig;-hlig}}
\hyphenpenalty=10000
\exhyphenpenalty=10000
\sloppy
%% ---------------------------------------------------------------------------
"""


# Injected immediately after \documentclass, before anything else can run.
#
# Resume templates are written for pdfLaTeX and commonly `\input{glyphtounicode}`
# then set `\pdfgentounicode=1` to get a proper ToUnicode map. Those are pdfTeX
# primitives; Tectonic runs XeTeX, where they are undefined and the file will not
# compile at all. Declaring no-op equivalents lets the author's file build
# unchanged — XeTeX writes its own ToUnicode map, so nothing is lost.
ENGINE_COMPAT = r"""
%% --- engine compatibility (injected by Quill) --------------------------
\makeatletter
\ifdefined\pdfglyphtounicode\else\def\pdfglyphtounicode#1#2{}\fi
\ifdefined\pdfgentounicode\else\newcount\pdfgentounicode\fi
\ifdefined\pdfcompresslevel\else\newcount\pdfcompresslevel\fi
\ifdefined\pdfobjcompresslevel\else\newcount\pdfobjcompresslevel\fi
\makeatother
%% ---------------------------------------------------------------------------
"""

_DOCUMENTCLASS_RE = re.compile(r"\\documentclass\s*(\[[^\]]*\])?\s*\{[^{}]*\}")


def engine_compat(source: str) -> str:
    """Make a pdfLaTeX-targeted preamble survive XeTeX. Idempotent."""
    if "engine compatibility (injected by Quill)" in source:
        return source
    m = _DOCUMENTCLASS_RE.search(source)
    if not m:
        return ENGINE_COMPAT + source
    return source[: m.end()] + "\n" + ENGINE_COMPAT + source[m.end():]


def harden(source: str) -> str:
    """
    Make a document ready to compile under Tectonic and to be read by an ATS.

    Engine compatibility is applied here rather than left to the caller: the
    author's file will not compile at all without it, and a separate step is a
    step someone forgets.

    Idempotent, so it is safe to call on every recompile of the trim loop.
    """
    source = engine_compat(source)
    if "ATS hardening (injected by Quill)" in source:
        return source
    marker = r"\begin{document}"
    if marker not in source:
        return source
    return source.replace(marker, HARDENING + marker, 1)


# What a ligature glyph should have been, for auditing a compiled PDF.
LIGATURE_GLYPHS = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl",
    "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "ft", "ﬆ": "st",
}


def normalise_ligatures(text: str) -> str:
    """Undo ligature glyphs in text extracted from a PDF, for keyword matching."""
    for glyph, plain in LIGATURE_GLYPHS.items():
        text = text.replace(glyph, plain)
    return text


def unescape(tex: str) -> str:
    """LaTeX-safe text -> plain text, for the escape-only subset."""
    out = tex
    out = out.replace(r"\textbackslash{}", "\\")
    out = out.replace(r"\textasciitilde{}", "~").replace(r"\textasciicircum{}", "^")
    for raw, rep in _ESCAPES:
        if rep.startswith("\\") and len(rep) == 2:
            out = out.replace(rep, raw)
    # Ligature breaks and empty groups are typesetting artefacts, not part of the
    # sentence the model should see.
    out = out.replace(_LIGATURE_BREAK, "").replace("\\kern0pt", "")
    return out.replace("{}", "")


def is_plain(tex: str) -> bool:
    """True when a span holds prose only, so it is safe to reword."""
    if "$" in tex:                       # inline maths, e.g. $O(\log n)$
        return False
    # "~" is a tie and "\," "\'" "\ " are spacing and accents: none survives a
    # trip through plain text, so a span holding one is not rewordable.
    if "~" in tex:
        return False
    stripped = _ALLOWED_ESCAPE_RE.sub("", tex)
    return "\\" not in stripped


@dataclass
class Slot:
    """One editable run of prose inside the document."""

    id: str
    kind: str                # bullet | summary | skills
    section: str             # nearest preceding \section
    entry: str               # nearest preceding company/project heading
    text: str                # plain-text form, what the model sees
    raw: str                 # the original LaTeX span
    start: int               # span of the text itself
    end: int
    outer_start: int         # span including \item, so the bullet can be dropped
    outer_end: int
    editable: bool

    @property
    def droppable(self) -> bool:
        return self.kind == "bullet"


@dataclass
class SkillLine:
    """
    One `\\textbf{Label:} a, b, c \\\\` row of a skills section.

    Items are raw LaTeX, moved as whole substrings and never re-escaped, so a
    reorder cannot change what an item says — only where it sits.
    """

    label: str
    start: int               # span of the item list, after the label
    end: int
    items: List[str]         # raw items, in source order
    trailing: str            # separator left after the last item ("," or "")

    def render(self, order: Sequence[int]) -> str:
        return ", ".join(self.items[i] for i in order) + self.trailing


@dataclass
class TexDoc:
    source: str
    slots: List[Slot] = field(default_factory=list)
    skills: List[SkillLine] = field(default_factory=list)
    # Summary openings the author has written as true of themselves, from a
    # `% Quill-headlines: A | B | C` comment. The first is the one in use.
    headlines: List[str] = field(default_factory=list)

    def slot(self, slot_id: str) -> Optional[Slot]:
        return next((s for s in self.slots if s.id == slot_id), None)

    @property
    def editable_slots(self) -> List[Slot]:
        return [s for s in self.slots if s.editable]

    def bullet_groups(self) -> List[List[Slot]]:
        """
        Bullets that may trade places: the droppable bullets of one entry, in
        source order, with nothing but whitespace between them. A comment or a
        command between two bullets means they are not one plain list, and the
        group is left alone rather than risk moving markup.
        """
        groups: Dict[tuple, List[Slot]] = {}
        for slot in sorted(self.slots, key=lambda s: s.outer_start):
            if slot.droppable:
                groups.setdefault((slot.section, slot.entry), []).append(slot)
        out = []
        for members in groups.values():
            gaps = (
                self.source[a.outer_end:b.outer_start]
                for a, b in zip(members, members[1:])
            )
            # A bullet holding a nested list cuts off at the inner \item, so
            # its span carries a bare \begin{itemize}; moving it breaks the
            # document. Such an entry keeps its order.
            nested = any(
                re.search(r"\\(?:begin|end)\s*\{",
                          self.source[s.outer_start:s.outer_end])
                for s in members
            )
            if len(members) > 1 and not nested and all(not g.strip() for g in gaps):
                out.append(members)
        return out

    def _slot_body(self, slot: Slot, edits: Dict[str, str]) -> str:
        """A bullet's whole outer span, with its edit applied."""
        text = escape(edits[slot.id]) if slot.id in edits and slot.editable else slot.raw
        return (
            self.source[slot.outer_start:slot.start]
            + text
            + self.source[slot.end:slot.outer_end]
        )

    def render(
        self,
        edits: Optional[Dict[str, str]] = None,
        drop: Optional[Iterable[str]] = None,
        order: Optional[Sequence[str]] = None,
        skill_order: Optional[Dict[int, Sequence[int]]] = None,
    ) -> str:
        """
        Rebuild the document.

        `edits` maps slot id -> new plain text; `drop` removes whole bullets;
        `order` lists slot ids most-important first, and bullets within each
        entry are laid out in that order; `skill_order` maps a skills line to a
        permutation of its items. With none of them, the result is the original
        source byte for byte.
        """
        edits = edits or {}
        dropped: Set[str] = set(drop or ())
        skill_order = skill_order or {}

        # (start, end, replacement), non-overlapping. Splicing by offset is what
        # guarantees the round trip.
        spans: List[tuple[int, int, str]] = []
        grouped: Set[str] = set()

        if order is not None:
            rank = {sid: i for i, sid in enumerate(order)}
            for members in self.bullet_groups():
                kept = sorted(
                    (s for s in members if s.id not in dropped),
                    key=lambda s: (rank.get(s.id, len(rank)), s.outer_start),
                )
                # Position i of the entry gets the i-th most relevant bullet;
                # positions left over once dropped bullets are gone go empty.
                for i, position in enumerate(members):
                    body = self._slot_body(kept[i], edits) if i < len(kept) else ""
                    spans.append((position.outer_start, position.outer_end, body))
                    grouped.add(position.id)

        for slot in self.slots:
            if slot.id in grouped:
                continue
            if slot.id in dropped and slot.droppable:
                spans.append((slot.outer_start, slot.outer_end, ""))
            elif slot.id in edits and slot.editable:
                spans.append((slot.start, slot.end, escape(edits[slot.id])))

        for index, perm in skill_order.items():
            if 0 <= index < len(self.skills):
                line = self.skills[index]
                spans.append((line.start, line.end, line.render(perm)))

        pieces: List[str] = []
        cursor = 0
        for start, end, text in sorted(spans):
            if start < cursor:
                # Overlapping spans would splice the same source twice and
                # print duplicated text into a resume sent to an employer.
                raise ValueError(f"overlapping edit spans at offset {start}")
            pieces.append(self.source[cursor:start])
            pieces.append(text)
            cursor = end
        pieces.append(self.source[cursor:])
        return "".join(pieces)


def _context_at(pos: int, marks: List[tuple[int, str]], after: int = -1) -> str:
    """
    The nearest heading at or before `pos`.

    `after` scopes the search, so an entry heading cannot leak across a section
    boundary — otherwise the Achievements bullets inherit "National Institute of
    Technology" from the Education block above them.
    """
    found = ""
    for at, label in marks:
        if at > pos:
            break
        if at >= after:
            found = label
    return found


# Commands that decorate an entry label rather than name it.
_LABEL_NOISE_RE = re.compile(
    r"\\(?:textbf|textit|emph|underline|texttt|small|large|href|url|textsc)\s*"
)


def label_text(tex: str) -> str:
    """
    Readable name from a heading argument.

    Project headings nest several commands deep —
    `\\textbf{\\href{url}{\\underline{ThrottleX}}} $-$ API Rate Limiting Service` —
    so the URL is dropped and the remaining prose kept.
    """
    out = re.sub(r"\\href\s*\{[^{}]*\}", "", tex)          # drop the URL argument
    out = _LABEL_NOISE_RE.sub(" ", out)
    out = re.sub(r"\\[A-Za-z@]+\s*", " ", out)             # any other command
    out = out.replace("$-$", "-").replace("$", " ")
    out = out.replace("{", " ").replace("}", " ")
    out = re.sub(r"\s+", " ", out).strip(" -")
    return out[:80]


def _find_entries(source: str, in_body) -> List[tuple[int, str]]:
    """Company/project headings, read with brace matching rather than a regex."""
    entries: List[tuple[int, str]] = []
    for m in _ENTRY_MACRO_RE.finditer(source):
        if not in_body(m.start()):
            continue
        open_idx = m.end() - 1
        close_idx = _matching_brace(source, open_idx)
        if close_idx < 0:
            continue
        label = label_text(source[open_idx + 1:close_idx])
        if label:
            entries.append((m.start(), label))
    entries.sort()
    return entries


def _matching_brace(src: str, open_idx: int) -> int:
    """Index just past the brace group opening at `open_idx`, or -1."""
    depth = 0
    i = open_idx
    while i < len(src):
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


_COMMENT_LINE_RE = re.compile(r"(?m)^[ \t]*%[^\n]*\n?")


def _trim_trailing_comments(src: str, start: int, end: int) -> int:
    """Pull `end` back past any whole comment lines at the tail of the span."""
    while end > start:
        tail = src[start:end]
        stripped = tail.rstrip()
        if stripped != tail:                      # drop trailing whitespace first
            end = start + len(stripped)
            continue
        line_start = src.rfind("\n", start, end) + 1
        if line_start <= start:
            break
        line = src[line_start:end]
        if _COMMENT_LINE_RE.fullmatch(line) or line.lstrip().startswith("%"):
            end = line_start
            continue
        break
    return end


def _body_end(src: str, start: int) -> int:
    """
    Where a `\\item` body stops: at the next \\item, the end of the list, or a
    following structural command.
    """
    stops = []
    for pattern in (r"\\item\b", r"\\end\s*\{", r"\\section\b", r"\\subsection\b",
                    r"\\resumeSubheading\b", r"\\role\b", r"\\textbf\b"):
        m = re.search(pattern, src[start:])
        if m:
            stops.append(start + m.start())
    return min(stops) if stops else len(src)


def body_span(source: str) -> tuple[int, int]:
    """
    Where the document's content lives.

    Everything before `\\begin{document}` is preamble, and resume templates
    define their bullet macros there — `\\newcommand{\\resumeItem}[1]{\\item...}`
    contains a literal `\\item`. Scanning the whole file would offer those macro
    definitions up as droppable bullets, and trimming one to save space would
    destroy the document.
    """
    start_marker = "\\begin{document}"
    end_marker = "\\end{document}"
    start = source.find(start_marker)
    if start < 0:
        return 0, len(source)
    start += len(start_marker)
    end = source.find(end_marker, start)
    return start, (end if end >= 0 else len(source))


def parse(source: str) -> TexDoc:
    """Locate the editable prose spans in a LaTeX resume."""
    body_start, body_end = body_span(source)

    def in_body(pos: int) -> bool:
        return body_start <= pos < body_end

    sections = [
        (m.start(), m.group(1).strip())
        for m in _SECTION_RE.finditer(source)
        if in_body(m.start())
    ]
    entries = _find_entries(source, in_body)

    slots: List[Slot] = []
    seen_spans: Set[tuple[int, int]] = set()

    def add(kind: str, outer_start: int, outer_end: int, start: int, end: int) -> None:
        raw = source[start:end]
        if not raw.strip():
            return
        if (start, end) in seen_spans:
            return
        seen_spans.add((start, end))
        # Trailing comment lines must stay outside the span. A slot that runs to
        # the next \section swallows the "% ---- Experience ----" divider above
        # it, and escaping that on re-injection turns `%` into `\%` — printing
        # the comment onto the page as if it were content.
        end = _trim_trailing_comments(source, start, end)
        raw = source[start:end]
        # Keep surrounding whitespace out of the editable span so re-rendering
        # preserves the file's indentation exactly.
        lead = len(raw) - len(raw.lstrip())
        trail = len(raw) - len(raw.rstrip())
        start, end = start + lead, end - trail
        raw = source[start:end]
        if not raw.strip():
            seen_spans.discard((start, end))
            return
        section_at = max((at for at, _ in sections if at <= start), default=-1)
        slots.append(
            Slot(
                id=f"{kind[0]}{len(slots):02d}",
                kind=kind,
                section=_context_at(start, sections),
                entry=_context_at(start, entries, after=section_at),
                text=unescape(raw),
                raw=raw,
                start=start,
                end=end,
                outer_start=outer_start,
                outer_end=outer_end,
                editable=is_plain(raw),
            )
        )

    # Macro bullets first. They are more specific than a bare \item, and the
    # macro expands to one, so claiming their span stops the same bullet being
    # captured twice.
    macro_spans: List[tuple[int, int]] = []
    for m in _MACRO_ITEM_RE.finditer(source):
        if not in_body(m.start()):
            continue
        open_idx = m.end() - 1
        close_idx = _matching_brace(source, open_idx)
        if close_idx < 0:
            continue
        macro_spans.append((m.start(), close_idx + 1))
        add("bullet", m.start(), close_idx + 1, open_idx + 1, close_idx)

    # Plain \item bullets, skipping any span a macro already claimed.
    for m in _ITEM_RE.finditer(source):
        if not in_body(m.start()):
            continue
        if any(a <= m.start() < b for a, b in macro_spans):
            continue
        text_start = m.end()
        text_end = _body_end(source, text_start)
        add("bullet", m.start(), text_end, text_start, text_end)

    # The prose paragraph under a Summary/Objective/Profile section. Leading
    # size commands (`\small`, `\vspace{..}`) belong to the layout, not the
    # sentence — skipping them is what keeps the summary rewritable, and the
    # summary is where transferable framing is allowed to happen.
    for at, title in sections:
        if not re.match(r"(summary|objective|profile|about)", title, re.I):
            continue
        after = source.index("}", at) + 1
        stop = _body_end(source, after)
        # No \A here: `pattern.match(s, pos)` already anchors at pos, whereas \A
        # would anchor to the start of the whole file and never match.
        prose = re.compile(r"(?:\s|\\[A-Za-z@]+\s*(?:\{[^{}]*\})?|%[^\n]*\n)*")
        lead = prose.match(source, after, stop)
        text_start = lead.end() if lead else after
        add("summary", after, stop, text_start, stop)

    slots.sort(key=lambda s: s.start)
    for i, slot in enumerate(slots):
        slot.id = f"{slot.kind[0]}{i:02d}"
    # A skills row written as an \item is already a bullet slot; two edits
    # over one span cannot both be spliced, so the bullet keeps it.
    skills = [
        line for line in _find_skill_lines(source, sections, body_end)
        if not any(s.outer_start < line.end and line.start < s.outer_end for s in slots)
    ]
    return TexDoc(
        source=source,
        slots=slots,
        skills=skills,
        headlines=_find_headlines(source),
    )


_HEADLINES_RE = re.compile(r"(?m)^[ \t]*%[ \t]*Quill-headlines:[ \t]*(.+)$")
_SKILL_LABEL_RE = re.compile(r"(?m)^[ \t]*\\textbf\s*\{")
_LINE_BREAK_RE = re.compile(r"\\\\|\n")


def _find_headlines(source: str) -> List[str]:
    m = _HEADLINES_RE.search(source)
    if not m:
        return []
    return [h.strip() for h in m.group(1).split("|") if h.strip()]


def _split_items(region: str) -> List[str]:
    """Split on commas outside brackets: `AWS (EC2, S3)` is one item."""
    items, depth, current = [], 0, []
    for ch in region:
        if ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
        if ch == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    items.append("".join(current).strip())
    return items


def _find_skill_lines(
    source: str, sections: List[tuple[int, str]], body_end: int
) -> List[SkillLine]:
    """
    The `\\textbf{Label:} items` rows under a Skills section. A row whose items
    do not rejoin to exactly the original text is skipped, so a reorder can
    never rewrite a line it did not fully understand.
    """
    out: List[SkillLine] = []
    for i, (at, title) in enumerate(sections):
        if not re.search(r"skill", title, re.I):
            continue
        stop = sections[i + 1][0] if i + 1 < len(sections) else body_end
        for m in _SKILL_LABEL_RE.finditer(source, at, stop):
            open_idx = m.end() - 1
            close_idx = _matching_brace(source, open_idx)
            if close_idx < 0 or close_idx >= stop:
                continue
            brk = _LINE_BREAK_RE.search(source, close_idx + 1, stop)
            region_end = brk.start() if brk else stop
            region = source[close_idx + 1:region_end]
            lead = len(region) - len(region.lstrip())
            text = region.strip()
            if not text or "%" in text.replace("\\%", ""):
                continue
            items = _split_items(text)
            trailing = ""
            if items and items[-1] == "":
                items.pop()
                trailing = ","
            if len(items) < 2 or any(not it for it in items):
                continue
            if ", ".join(items) + trailing != text:
                continue
            start = close_idx + 1 + lead
            out.append(SkillLine(
                label=label_text(source[open_idx + 1:close_idx]).rstrip(":").strip(),
                start=start,
                end=start + len(text),
                items=items,
                trailing=trailing,
            ))
    return out


def parse_file(path) -> TexDoc:
    from pathlib import Path

    return parse(Path(path).read_text(encoding="utf-8"))


def _plain(fragment: str) -> str:
    body = re.sub(r"(?m)^\s*%.*$", " ", fragment)
    body = re.sub(r"\\href\s*\{[^{}]*\}", " ", body)                # URLs are not claims
    body = re.sub(r"\\[A-Za-z@]+\s*(\[[^\]]*\])?", " ", body)
    body = body.replace("{", " ").replace("}", " ").replace("$", " ").replace("\\", " ")
    return re.sub(r"\s+", " ", body).strip()


def entry_context(doc: TexDoc, slot: Slot) -> str:
    """
    What a bullet may draw names and technologies from: its entry's heading
    (a project heading carries its stack — "Java 17, Spring Boot, MySQL") and
    its sibling bullets. Not the rest of the resume: that is how a Paytm bullet
    was allowed to say "at Denr Financial Services".
    """
    if slot.kind != "bullet":
        return prose_text(doc)
    members = [s for s in doc.slots if (s.section, s.entry) == (slot.section, slot.entry)]
    first = min(s.outer_start for s in members)
    last = max(s.outer_end for s in members)
    # The heading sits between the previous slot and this entry's first bullet.
    before = [s.outer_end for s in doc.slots if s.outer_end <= first]
    start = max(before) if before else body_span(doc.source)[0]
    return _plain(doc.source[start:last])


def prose_text(doc: TexDoc) -> str:
    """
    The resume's sentences and skills — what the summary may summarise. Not
    the preamble or the header: those hold `0.97\\textwidth` and the phone
    number, whose digits once let 40% become 64% or 97%.
    """
    parts = [s.text for s in doc.slots]
    parts += [unescape(item) for line in doc.skills for item in line.items]
    return "\n".join(parts)


def plain_text(doc: TexDoc) -> str:
    """Everything the document says, for keyword matching and the guard."""
    body = doc.source
    body = re.sub(r"(?m)^\s*%.*$", " ", body)          # comments
    body = re.sub(r"\\[A-Za-z@]+\s*(\[[^\]]*\])?", " ", body)  # commands
    body = body.replace("{", " ").replace("}", " ").replace("$", " ")
    body = body.replace("\\", " ")
    return re.sub(r"\s+", " ", body).strip()
