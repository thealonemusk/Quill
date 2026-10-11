"""
Tailor a LaTeX resume to one job description and compile it to PDF.

The sequence, and why each step is where it is:

  1. parse    — split the .tex into a frozen document and editable prose spans
  2. analyse  — turn the JD into requirements (deterministic + optional LLM)
  3. rank     — score every bullet against those requirements, deterministically
  4. rewrite  — the model rewords bullets; it sees plain text and returns plain text
  5. guard    — reject any rewrite introducing a fact the master does not contain
  5b. arrange — no model needed: lead each entry with its most relevant bullet,
                front-load asked-for skills within each line, pick the closest
                author-written headline. Roles and projects never move.
  6. render   — splice accepted rewrites back in, escape, harden against ligatures
  7. compile  — Tectonic
  8. fit      — while it spills past one page, drop the lowest-ranked bullet and rebuild
  9. report   — before/after keyword coverage, what changed, what was cut

Steps 3 and 5 are the ones that make the output trustworthy: the model never
decides what is true or what matters, only how a sentence reads.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

from backend.resume import compile as rc
from backend.resume import guard as guard_mod
from backend.resume import jd as jd_mod
from backend.resume import llm, match, texdoc

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RESUMES_DIR = PROJECT_ROOT / "resumes"
TAILORED_DIR = RESUMES_DIR / "tailored"
DEFAULT_MASTER = RESUMES_DIR / "master_resume.tex"

MAX_TRIM_ROUNDS = 12
MAX_REWRITE_GROWTH = 1.15      # the prompt's own "within 15%", enforced
_JOB_ID_RE = re.compile(r"^[0-9A-Za-z-]{8,64}$")

SYSTEM = (
    "You are a resume editor. You rewrite a candidate's existing bullet points so they "
    "speak the vocabulary of a specific job description. You never invent experience. "
    "Everything you write must be supported by the bullet you were given. Answer with JSON only."
)

REWRITE_TEMPLATE = """Rewrite this candidate's resume bullets for the job below.

=== THE JOB ===
Title: {job_title}
Company: {company}
Seniority: {seniority}
Domain: {domain}
Required: {required}
Preferred: {preferred}
Responsibilities: {responsibilities}
The employer's own keywords: {keywords}

=== THE CANDIDATE'S BULLETS (these are the only facts you may use) ===
{bullets}

=== WHAT TO DO ===
For each bullet, decide whether re-wording it would make its relevance to this job clearer.
Return only the bullets you actually changed.

HARD RULES — a violation gets the rewrite discarded automatically:
- Never name a technology, tool, company, product or certification that is not already in
  that candidate's bullets. If the job wants Kafka and the bullet says MQTT, you write about
  MQTT; you may describe it as event-driven messaging, but you may not write "Kafka".
- Never change a number, and never add one. "45 production changes" stays 45.
- Never claim seniority, team size, or duration that is not stated.
- Keep each rewrite within 15% of the original length — longer rewrites are discarded
  automatically. This resume must stay one page.
- Never append a clause that states something the bullet does not ("— ensuring reliability",
  "collaborating with distributed teams", "establishing testing frameworks"). Reorder and
  re-word what is there; do not add to it.

WHAT GOOD LOOKS LIKE:
- Lead with the outcome, then the method.
- Use the employer's phrasing wherever it genuinely describes what the candidate did.
  If they say "distributed systems" and the bullet describes a device fleet across regions,
  that phrasing is fair. If they say "Kubernetes" and the bullet never mentions it, it is not.
- Surface work that is already implied but unnamed. Debugging a memory leak IS debugging and
  performance tuning; say so if the job asks for it.
- Keep concrete metrics; they are the most persuasive part of any bullet.

{summary_rule}

Return JSON:
{{
  "rewrites": {{"<slot id>": "<rewritten sentence>"}},
  "notes": "<one sentence on the angle you took>"
}}"""

SUMMARY_STRICT = (
    "The summary bullet (if present) follows the same rules as every other bullet."
)
SUMMARY_TRANSFERABLE = (
    "For the summary bullet only, you may name the candidate's closest genuine equivalent to a "
    "requirement they do not have, provided you frame it as what they did do and never claim the "
    "missing thing itself. Example: \"event-driven messaging at fleet scale (MQTT/MQTTS)\" is "
    "acceptable against a Kafka requirement; \"Kafka experience\" is not."
)


@dataclass
class TailorResult:
    ok: bool
    pdf_path: str = ""
    tex_path: str = ""
    report_path: str = ""
    report: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "pdf_path": self.pdf_path,
            "tex_path": self.tex_path,
            "report_path": self.report_path,
            "report": self.report,
            "error": self.error,
        }


def master_path(configured: str = "") -> Path:
    if configured:
        p = Path(configured)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        if p.is_file():
            return p
    if DEFAULT_MASTER.is_file():
        return DEFAULT_MASTER
    candidates = sorted(RESUMES_DIR.glob("*.tex"))
    return candidates[0] if candidates else DEFAULT_MASTER


def has_master(configured: str = "") -> bool:
    return master_path(configured).is_file()


def _slug(text: str) -> str:
    out = re.sub(r"[^\w\s-]", "", text or "").strip()
    return re.sub(r"[\s-]+", "_", out)[:40] or "job"


def output_dir(company: str, job_id: str) -> Path:
    return TAILORED_DIR / f"{_slug(company)}_{(job_id or 'adhoc')[:8]}"


def tailored_pdf(job_id: str) -> Optional[Path]:
    """
    The tailored PDF produced for this job, or None.

    A PDF older than the master .tex is ignored: it was built from a resume
    that has since been edited, and would send an employer the old version.
    """
    # The id goes into a glob: "*" would match every job's folder.
    if not job_id or not _JOB_ID_RE.match(job_id) or not TAILORED_DIR.is_dir():
        return None
    pdf = next(TAILORED_DIR.glob(f"*_{job_id[:8]}/resume.pdf"), None)
    if pdf is None:
        return None
    master = master_path()
    if master.is_file() and pdf.stat().st_mtime < master.stat().st_mtime:
        logger.info(f"Ignoring stale tailored resume {pdf} — the master .tex is newer")
        return None
    return pdf


def tailored_upload(job_id: str, filename: str = "") -> Optional[Path]:
    """
    The tailored PDF under the name an employer should see.

    Every tailored file is called `resume.pdf`, and an upload carries its file
    name, so a copy is kept beside it under the default resume's name. It stays
    inside `resumes/tailored/<sub>/` — a PDF directly in `resumes/` would be
    picked up by `ApplicantProfile.resume_file()` as the default for every job.
    """
    pdf = tailored_pdf(job_id)
    if pdf is None:
        return None
    name = Path(filename).stem + ".pdf" if filename else pdf.name
    if name == pdf.name:
        return pdf
    # A subfolder of its own: beside resume.pdf sit base.pdf (the untailored
    # master) and the trim loop's staging file, and a default resume named
    # either would have been "found" there and uploaded as the tailored one.
    named = pdf.parent / "upload" / name
    try:
        named.parent.mkdir(exist_ok=True)
        if not named.is_file() or named.stat().st_mtime < pdf.stat().st_mtime:
            shutil.copyfile(pdf, named)
    except OSError as exc:
        # Called inside the apply loop: a locked file must not stop the run.
        # The tailored content still goes out, under its own file name.
        logger.warning(f"Could not name tailored copy {named}: {exc}; uploading {pdf.name}")
        return pdf
    return named


def _bullet_block(slots: Sequence, ranks: Dict[str, float]) -> str:
    lines = []
    for slot in slots:
        where = " / ".join(x for x in (slot.section, slot.entry) if x)
        lines.append(f"[{slot.id}] ({where}) relevance={ranks.get(slot.id, 0)}\n{slot.text}")
    return "\n\n".join(lines)


def _bullet_order(doc: texdoc.TexDoc, spec: jd_mod.JobSpec) -> List[str]:
    """
    Slot ids most relevant first; ties keep the author's order. Only bullets
    inside one entry ever trade places — roles and projects stay where the
    author put them, because a recruiter reads the history as a timeline.

    Only evidence for this job moves a bullet. `rank_slots` also rewards any
    bullet carrying a number, which is right for choosing what to trim but
    wrong here: it lifted a project's metric line above the line saying what
    the project is, for a job that asked for neither.
    """
    score = match.rank_slots(
        doc.slots, spec.all_required(), spec.preferred, reward_numbers=False
    )
    return [s.id for s in sorted(doc.slots, key=lambda s: (-score.get(s.id, 0.0), s.start))]


def _skill_order(
    doc: texdoc.TexDoc, spec: jd_mod.JobSpec, jd_text: str
) -> Dict[int, List[int]]:
    """
    Within each skills line, the items this job asks for move to the front.
    Nothing is removed: every listed skill is true, and each one is a term a
    recruiter's search can still hit.
    """
    required = [match.normalise(t) for t in spec.all_required()]
    preferred = [match.normalise(t) for t in spec.preferred]
    jd_blob = match.normalise(jd_text)
    out: Dict[int, List[int]] = {}
    for index, line in enumerate(doc.skills):
        scores = []
        for item in line.items:
            blob = match.normalise(texdoc.label_text(item) or item)
            score = 3.0 * sum(1 for t in required if match.contains(blob, t))
            score += 1.0 * sum(1 for t in preferred if match.contains(blob, t))
            # The item named anywhere in the posting, e.g. "Linux" in prose.
            name = match.normalise(re.sub(r"\s*\(.*\)", "", texdoc.label_text(item) or item))
            if name and match.contains(jd_blob, name):
                score += 1.0
            scores.append(score)
        perm = sorted(range(len(line.items)), key=lambda i: (-scores[i], i))
        if perm != list(range(len(line.items))):
            out[index] = perm
    return out


def _headline_edit(
    doc: texdoc.TexDoc, spec: jd_mod.JobSpec, current: str
) -> Optional[tuple[str, str]]:
    """
    Swap the summary's opening for the author-written headline closest to this
    role. The options come from a `% tempoapply-headlines:` comment in the
    master — the author states what is true, the code only chooses. Returns
    (old, new) or None when the current opening is already the best fit.
    """
    if len(doc.headlines) < 2 or not current.startswith(doc.headlines[0]):
        return None
    title = match.normalise(spec.job_title)
    terms = [match.normalise(t) for t in spec.all_required()]

    def score(headline: str) -> float:
        blob = match.normalise(headline)
        words = {w for w in re.findall(r"[a-z][a-z+#.-]*", blob) if len(w) > 2}
        hits = sum(2.0 for w in words if match.contains(title, w))
        return hits + sum(1.0 for t in terms if match.contains(blob, t))

    best = max(doc.headlines, key=lambda h: (score(h), h == doc.headlines[0]))
    if best == doc.headlines[0] or score(best) <= score(doc.headlines[0]):
        return None
    return doc.headlines[0], best


def _first_glance(
    doc: texdoc.TexDoc,
    edits: Dict[str, str],
    dropped: Sequence[str],
    order: Optional[Sequence[str]],
    skill_order: Dict[int, Sequence[int]],
) -> str:
    """
    What a recruiter reads in the first few seconds: the summary, the top two
    bullets of each entry, and the first four items of each skills line.
    Coverage measured here is what reordering actually moves.
    """
    rank = {sid: i for i, sid in enumerate(order or [s.id for s in doc.slots])}
    parts: List[str] = []
    for slot in doc.slots:
        if slot.kind == "summary":
            parts.append(edits.get(slot.id, slot.text))
    entries: Dict[tuple, List[texdoc.Slot]] = {}
    for slot in doc.slots:
        if slot.droppable and slot.id not in dropped:
            entries.setdefault((slot.section, slot.entry), []).append(slot)
    reorderable = {s.id for g in doc.bullet_groups() for s in g} if order else set()
    for members in entries.values():
        if all(s.id in reorderable for s in members):
            members = sorted(members, key=lambda s: rank.get(s.id, len(rank)))
        parts.extend(edits.get(s.id, s.text) for s in members[:2])
    for index, line in enumerate(doc.skills):
        perm = skill_order.get(index, range(len(line.items)))
        parts.extend(texdoc.unescape(line.items[i]) for i in list(perm)[:4])
    return "\n".join(parts)


def _propose_rewrites(
    doc: texdoc.TexDoc,
    spec: jd_mod.JobSpec,
    ranks: Dict[str, float],
    transferable: bool,
    use_llm: bool = True,
) -> tuple[Dict[str, str], str]:
    """Ask the model for reworded bullets. Returns (rewrites, notes)."""
    editable = doc.editable_slots
    if not editable:
        return {}, "no editable bullets found"
    if not use_llm:
        return {}, "rewording disabled for this run"
    if not llm.is_available():
        # Without a key the run still reorders, compiles, audits, trims to one
        # page and reports the keyword gap — only the wording is untouched.
        return {}, "no API key — reordered, compiled and trimmed, but not reworded"

    prompt = REWRITE_TEMPLATE.format(
        job_title=spec.job_title or "(not given)",
        company=spec.company or "(not given)",
        seniority=spec.seniority or "(not given)",
        domain=spec.domain or "(not given)",
        required=", ".join(spec.all_required()[:20]) or "(none extracted)",
        preferred=", ".join(spec.preferred[:12]) or "(none)",
        responsibilities="; ".join(spec.responsibilities[:8]) or "(none)",
        keywords=", ".join(spec.ats_keywords[:20]) or "(none)",
        bullets=_bullet_block(editable, ranks),
        summary_rule=SUMMARY_TRANSFERABLE if transferable else SUMMARY_STRICT,
    )
    try:
        data = llm.ask_json(SYSTEM, prompt)
    except Exception as exc:  # noqa: BLE001 - tailoring still works without rewrites
        logger.warning(f"Rewrite call failed: {exc}")
        return {}, f"rewrite unavailable ({exc})"

    raw = data.get("rewrites") or {}
    if not isinstance(raw, dict):        # a list here used to crash the job's tailoring
        return {}, f"rewrite reply was malformed ({type(raw).__name__}, expected an object)"
    valid_ids = {s.id for s in editable}
    rewrites = {
        str(k): " ".join(str(v).split())
        for k, v in raw.items()
        if str(k) in valid_ids and str(v).strip()
    }
    return rewrites, str(data.get("notes") or "")


def _fit_to_one_page(
    doc: texdoc.TexDoc,
    edits: Dict[str, str],
    ranks: Dict[str, float],
    out_pdf: Path,
    tectonic: str,
    max_pages: int,
    order: Optional[Sequence[str]] = None,
    skill_order: Optional[Dict[int, Sequence[int]]] = None,
) -> tuple[rc.Audit, List[str], str]:
    """
    Compile, and while the document spills over `max_pages`, drop the least
    relevant bullet and rebuild. Returns (audit, dropped slot ids, final source).
    """
    dropped: List[str] = []
    # Lowest relevance first — that is the order sacrifices are made in.
    droppable = sorted(
        (s for s in doc.slots if s.droppable),
        key=lambda s: (ranks.get(s.id, 0.0), -s.start),
    )

    # Emptying an itemize environment is a LaTeX error ("perhaps a missing
    # \item"), so every role and project must keep at least one bullet. Losing a
    # job's last line would also read as a gap in the work history.
    groups: Dict[tuple, List[str]] = {}
    for slot in doc.slots:
        if slot.droppable:
            groups.setdefault((slot.section, slot.entry), []).append(slot.id)

    def can_drop(slot_id: str) -> bool:
        for members in groups.values():
            if slot_id in members:
                remaining = [m for m in members if m not in dropped]
                return len(remaining) > 1
        return True

    # Re-inject *every* editable bullet, not only the reworded ones. Unchanged
    # text is spliced back identically except that `escape()` breaks its
    # ligatures, which is what stops "traffic" reaching the PDF as "traﬃc".
    all_edits = {slot.id: edits.get(slot.id, slot.text) for slot in doc.editable_slots}

    def build() -> str:
        return texdoc.harden(doc.render(
            edits=all_edits, drop=dropped, order=order, skill_order=skill_order
        ))

    for _ in range(MAX_TRIM_ROUNDS + 1):
        source = build()
        audit = rc.compile_tex(source, out_pdf, tectonic=tectonic)
        if audit.pages <= max_pages:
            return audit, dropped, source

        nxt = next(
            (s.id for s in droppable if s.id not in dropped and can_drop(s.id)),
            None,
        )
        if nxt is None:
            logger.warning("Nothing left to drop; resume still exceeds the page limit")
            return audit, dropped, source
        logger.info(f"{audit.pages} pages — dropping lowest-relevance bullet {nxt}")
        dropped.append(nxt)

    source = build()
    audit = rc.compile_tex(source, out_pdf, tectonic=tectonic)
    return audit, dropped, source


def tailor(
    jd_text: str,
    *,
    job_title: str = "",
    company: str = "",
    job_id: str = "",
    master: str = "",
    tectonic: str = "",
    transferable: bool = True,
    max_pages: int = 1,
    use_llm: bool = True,
) -> TailorResult:
    """Produce a job-specific PDF from the master LaTeX resume."""
    src_path = master_path(master)
    if not src_path.is_file():
        return TailorResult(ok=False, error=f"No master resume. Expected a .tex at {DEFAULT_MASTER}")
    if not rc.is_available(tectonic):
        return TailorResult(ok=False, error="Tectonic is not installed (tools/tectonic.exe)")

    doc = texdoc.parse_file(src_path)
    if not doc.slots:
        return TailorResult(
            ok=False,
            error="No editable bullets found in the .tex — the parser did not recognise its macros",
        )

    spec = jd_mod.analyse(jd_text, title=job_title, company=company, use_llm=use_llm)
    required = spec.all_required()
    ranks = match.rank_slots(doc.slots, required, spec.preferred)

    rewrites, notes = _propose_rewrites(doc, spec, ranks, transferable, use_llm=use_llm)

    master_plain = texdoc.plain_text(doc)
    # The headline options live in a comment, which plain_text() strips; they
    # are the author's own statements, so they count as master content.
    gate = guard_mod.Guard(
        " ".join([master_plain, *doc.headlines]),
        jd_text=jd_text,
        master_figures_text="\n".join([texdoc.prose_text(doc), *doc.headlines]),
    )
    # Each bullet is checked against itself (figures) and its own entry (names,
    # technologies); the summary against the whole resume.
    scopes = {
        s.id: (s.text, texdoc.entry_context(doc, s))
        for s in doc.editable_slots
        if s.kind == "bullet"
    }
    accepted, rejected = gate.filter(rewrites, scopes)

    # The length rule was only ever in the prompt. Every rewrite from the free
    # OpenRouter models broke it (122-160% of the original), the extra length
    # was where unsupported claims went ("collaborating asynchronously in
    # distributed team environments"), and it pushed the page to two and cost
    # three bullets. Enforced here, it is a gate like the guard.
    for slot_id in list(accepted):
        slot = doc.slot(slot_id)
        if slot is None:
            continue
        limit = max(len(slot.text) * MAX_REWRITE_GROWTH, len(slot.text) + 12)
        if len(accepted[slot_id]) > limit:
            rejected.append({
                "slot": slot_id,
                "text": accepted.pop(slot_id),
                "reason": f"longer than allowed ({len(rewrites[slot_id])} chars; limit {int(limit)})",
            })
    if rejected:
        for item in rejected:
            logger.info(f"Guard rejected {item['slot']}: {item['reason']}")

    model_rewrites = len(accepted)

    # Everything below needs no model: it only moves text the author wrote.
    order = _bullet_order(doc, spec)
    skill_order = _skill_order(doc, spec, jd_text)
    headline = None
    summary = next((s for s in doc.slots if s.kind == "summary" and s.editable), None)
    if summary and summary.id not in accepted:
        headline = _headline_edit(doc, spec, summary.text)
        if headline:
            text = headline[1] + summary.text[len(headline[0]):]
            # The options are the author's words, but every sentence reaching
            # the PDF passes the same gate — there is no second road in.
            verdict = gate.check(text)
            if verdict.ok:
                accepted[summary.id] = text
            else:
                logger.info(f"Guard rejected headline: {verdict.reason()}")
                rejected.append({"slot": summary.id, "text": text, "reason": verdict.reason()})
                headline = None

    out_dir = output_dir(company or spec.company, job_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "resume.pdf"
    # The trim loop writes a PDF every round. Built in place, a failure on
    # round three would leave round two's two-page PDF as resume.pdf — and
    # tailored_pdf() would upload it. It only takes that name once finished.
    staging = out_dir / "resume.building.pdf"

    arranged = True
    try:
        audit, dropped, final_source = _fit_to_one_page(
            doc, accepted, ranks, staging, tectonic, max_pages,
            order=order, skill_order=skill_order,
        )
    except (rc.CompileError, ValueError) as exc:
        # A reorder only moves whole bullets and whole skill items, so this
        # should not happen — but a resume shape the parser misjudged must cost
        # the arrangement, not the whole application.
        logger.warning(f"Arranged resume failed to compile ({exc}); retrying in source order")
        arranged = False
        order, skill_order = None, {}
        try:
            audit, dropped, final_source = _fit_to_one_page(
                doc, accepted, ranks, staging, tectonic, max_pages
            )
        except (rc.CompileError, ValueError) as exc2:
            staging.unlink(missing_ok=True)
            return TailorResult(ok=False, error=f"LaTeX failed: {exc2}")
    # Out of bullets to trim and still too long: a two-page resume is not a
    # tailored version of a one-page one. Fail, and the default goes out.
    if audit.pages > max_pages:
        staging.unlink(missing_ok=True)
        return TailorResult(
            ok=False,
            error=f"Tailored resume runs to {audit.pages} pages (limit {max_pages}); using the default",
        )
    try:
        staging.replace(pdf_path)
    except OSError as exc:  # Windows refuses while a viewer holds the old PDF
        staging.unlink(missing_ok=True)
        return TailorResult(ok=False, error=f"Could not replace {pdf_path} (is it open?): {exc}")

    tex_path = out_dir / "resume.tex"
    tex_path.write_text(final_source, encoding="utf-8")

    # The comparison the user actually cares about, measured on extracted PDF
    # text rather than on the source, because that is what an ATS reads.
    try:
        base_edits = {slot.id: slot.text for slot in doc.editable_slots}
        base_audit = rc.compile_tex(
            texdoc.harden(doc.render(edits=base_edits)), out_dir / "base.pdf", tectonic=tectonic
        )
        before_text = base_audit.text
    except rc.CompileError:
        before_text = master_plain

    glance_before = _first_glance(doc, {}, [], None, {})
    glance_after = _first_glance(doc, accepted, dropped, order, skill_order)

    reordered = []
    rank = {sid: i for i, sid in enumerate(order or [s.id for s in doc.slots])}
    for members in (doc.bullet_groups() if order else []):
        kept = [s for s in members if s.id not in dropped]
        first = min(kept, key=lambda s: rank[s.id]) if kept else None
        if first and first.id != kept[0].id:
            reordered.append({
                "entry": first.entry or first.section,
                "was_first": kept[0].text,
                "now_first": first.text,
            })

    report = {
        "job": spec.as_dict(),
        "coverage": match.gap_report(before_text, audit.text, required, spec.preferred),
        # Keyword coverage of what is read first — the part reordering moves.
        # Neither number predicts a callback; the send log does that, over time.
        "first_glance": match.gap_report(glance_before, glance_after, required, spec.preferred),
        "reordered": reordered,
        "skills_reordered": [
            {
                "line": doc.skills[i].label,
                "before": [texdoc.unescape(x) for x in doc.skills[i].items],
                "after": [texdoc.unescape(doc.skills[i].items[j]) for j in perm],
            }
            for i, perm in skill_order.items()
        ],
        "headline": {"from": headline[0], "to": headline[1]} if headline else None,
        "arranged": arranged,
        # Rewrites that passed the guard; a model that answered but had every
        # line refused changed nothing, and must not count as "reworded".
        "llm_used": model_rewrites > 0,
        "model_rewrites": model_rewrites,
        "changed": [
            {
                "slot": slot_id,
                "section": (doc.slot(slot_id).section if doc.slot(slot_id) else ""),
                "entry": (doc.slot(slot_id).entry if doc.slot(slot_id) else ""),
                "before": (doc.slot(slot_id).text if doc.slot(slot_id) else ""),
                "after": text,
            }
            for slot_id, text in accepted.items()
        ],
        "rejected": rejected,
        "dropped": [
            {
                "slot": slot_id,
                "text": (doc.slot(slot_id).text if doc.slot(slot_id) else ""),
                "why": "did not fit on one page",
            }
            for slot_id in dropped
        ],
        "pdf": {
            "pages": audit.pages,
            "characters": audit.chars,
            "problems": audit.problems(),
            "fonts": audit.fonts,
        },
        "notes": notes,
        "slots_total": len(doc.slots),
        "slots_editable": len(doc.editable_slots),
    }

    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return TailorResult(
        ok=True,
        pdf_path=str(pdf_path),
        tex_path=str(tex_path),
        report_path=str(report_path),
        report=report,
    )
