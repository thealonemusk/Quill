"""
Autofill API — the brain behind the browser extension.

The extension scrapes the form it is sitting on and POSTs the raw fields here;
this module answers with a value per field. All matching goes through
`backend.applier.fields`, the same resolver the Playwright auto-apply uses, so
tuning an alias or a custom answer improves both paths at once. Nothing here
touches a browser — it is pure label -> value.
"""
from __future__ import annotations

import base64
import mimetypes
import re
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend import seen_ledger
from backend.applier.ats import detect_ats
from backend.applier.fields import (
    JOB_LOCATION,
    OPTION_FALLBACKS,
    is_consent_label,
    is_skip_field,
    match_field_key,
    pick_option,
    pick_option_for_key,
    resolve_value,
)
from backend.applier.profile import ApplicantProfile, load_profile
from backend.applier.sections import KINDS, needed_counts, section_value, skill_list
from backend.db.models import Job, get_db

router = APIRouter(prefix="/api/autofill", tags=["autofill"])

# Field types the extension can type into. Anything else is reported as `skip`.
TEXTUAL_TYPES = {
    "text", "email", "tel", "phone", "url", "number", "search", "date",
    "textarea", "month",
}

# Page-title noise: "Job Application for Backend Engineer at Acme | Greenhouse"
TITLE_NOISE = re.compile(
    r"(job application for|apply for|application for|careers?|jobs?"
    r"|\|.*$|-\s*(greenhouse|lever|workday|ashby).*$)",
    re.I,
)

# File inputs that are not the resume.
NON_RESUME_UPLOAD = ("cover", "photo", "transcript", "certificate", "id proof", "payslip")


# ─── Schemas ─────────────────────────────────────────────────────────────────

class ScrapedField(BaseModel):
    idx: str
    selector: str = ""
    tag: str = ""
    type: str = ""
    name: str = ""
    id: str = ""
    automation: str = ""
    autocomplete: str = ""
    placeholder: str = ""
    label: str = ""
    group_label: str = ""          # fieldset/legend text for radio groups
    option_label: str = ""         # this radio's own label
    group_key: str = ""            # radio's shared question container, for nameless radios
    value: str = ""
    checked: bool = False
    required: bool = False
    options: List[str] = Field(default_factory=list)
    role: str = ""
    section_kind: str = ""         # experience | education | website
    section_index: int = 0         # 1-based entry number within that section


class ResolveRequest(BaseModel):
    url: str = ""
    page_title: str = ""
    job_title: str = ""
    company: str = ""
    overwrite: bool = False        # refill fields that already hold a value
    fields: List[ScrapedField] = Field(default_factory=list)


class Fill(BaseModel):
    idx: str
    selector: str = ""
    action: str                    # text | select | combobox | checkbox | radio | file | skip
    value: str = ""
    options: List[str] = Field(default_factory=list)
    label: str = ""
    key: str = ""                  # which profile key answered it, for debugging
    confidence: str = "high"       # high | low
    reason: str = ""
    values: List[str] = Field(default_factory=list)   # multiselect: pick each


class ResolveResponse(BaseModel):
    ats: str
    job_title: str
    company: str
    job_id: Optional[str] = None
    known_job: bool = False
    has_resume: bool = False
    sections_needed: Dict[str, int] = Field(default_factory=dict)
    fills: List[Fill]
    unresolved: List[str]
    stats: Dict[str, int]


class TrackRequest(BaseModel):
    url: str
    title: str = ""
    company: str = ""
    status: str = "applied"        # applied | visited
    notes: str = ""


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _clean_title(page_title: str) -> str:
    text = TITLE_NOISE.sub(" ", page_title or "")
    return re.sub(r"\s+", " ", text).strip(" -|·").strip()


def _company_from_url(url: str) -> str:
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    path = parsed.path.strip("/")
    # Greenhouse/Lever/Ashby put the company in the first path segment,
    # Workday puts it in the host.
    if any(m in host for m in ("greenhouse.io", "lever.co", "ashbyhq.com")):
        first = path.split("/")[0] if path else ""
        if first and first not in {"embed", "jobs"}:
            return first.replace("-", " ").title()
    if "myworkdayjobs.com" in host or "myworkday.com" in host:
        return host.split(".")[0].replace("-", " ").title()
    if not host or "localhost" in host or re.fullmatch(r"[\d.:]+", host):
        return ""
    ignore = {"www", "jobs", "careers", "boards", "apply", "com", "co", "io", "in", "net", "org"}
    parts = [p for p in host.split(".") if p not in ignore and not p.isdigit()]
    return parts[0].replace("-", " ").title() if parts else ""


def _lookup_job(db: Session, url: str) -> Optional[Job]:
    """Find the job row this page belongs to, so the extension can close the loop."""
    if not url:
        return None
    job = db.query(Job).filter(Job.url == url).first()
    if job:
        return job
    normalized = seen_ledger.normalize_url(url)
    bare = url.split("?")[0].rstrip("/")
    for candidate in db.query(Job).filter(Job.url.like(f"{bare}%")).limit(10).all():
        if seen_ledger.normalize_url(candidate.url) == normalized:
            return candidate
    return None


def _phone_value(profile: ApplicantProfile, field: ScrapedField, desired: str) -> str:
    blob = f"{field.label} {field.name} {field.autocomplete} {field.placeholder}".lower()
    # "Phone Device Type" is a dropdown wanting "Mobile", not a number — the
    # resolver already answered it, so leave its answer alone.
    if "device" in blob or "phone type" in blob:
        return desired or "Mobile"
    if "country" in blob or "dialing" in blob or "isd" in blob:
        return profile.phone_country_code or profile.phone_country
    if "extension" in blob or blob.strip().endswith(" ext"):
        return ""
    if desired and desired not in {profile.phone, profile.phone_e164()}:
        return desired
    # The national number is the safe default. Every portal that wants a phone
    # number also asks for the country separately — Workday has a whole
    # "Country Phone Code" field — and a "+91" typed into the number box on top
    # of that is either rejected by validation or submitted as +91+91.
    # A widget that genuinely wants E.164 says so in its placeholder.
    placeholder = (field.placeholder or "").strip()
    if placeholder.startswith("+") and "+91" not in placeholder:
        return profile.phone_e164() or profile.phone_national()
    return profile.phone_national() or profile.phone_e164()


def _is_resume_input(field: ScrapedField) -> bool:
    blob = f"{field.label} {field.name} {field.id} {field.automation}".lower()
    return not any(x in blob for x in NON_RESUME_UPLOAD)


SKILL_LABELS = ("skill", "skills", "key skills", "technical skills", "competenc")


def _is_skill_multiselect(field: ScrapedField, label: str) -> bool:
    """A skills picker takes many taxonomy entries, not one comma-joined string."""
    blob = f"{label} {field.automation}".lower()
    if not any(x in blob for x in SKILL_LABELS):
        return False
    # A plain text box is happy with the whole skills line; only a picker needs
    # the values fed in one at a time.
    return (
        field.role == "combobox"
        or field.tag not in {"input", "textarea"}
        or "multiselect" in field.automation.lower()
    )


def _option_candidates(key: str, desired: str) -> List[str]:
    """
    Acceptable answers for a widget, best first.

    Workday's dropdowns are not `<select>`, so their options do not exist in
    the DOM until the listbox is opened — the backend cannot see them and the
    match has to happen in the extension. Sending the fallbacks along is what
    lets a tenant offering only "Phone"/"Main" still answer Phone Device Type.
    """
    out = [desired] if desired else []
    for alternative in OPTION_FALLBACKS.get(key, ()):
        if alternative.lower() not in {v.lower() for v in out}:
            out.append(alternative)
    return out


def _emit(field: ScrapedField, base: dict, desired: str, key: str, ftype: str) -> Fill:
    """Turn a resolved value into the action this particular widget needs."""
    if ftype == "checkbox":
        if field.checked:
            return Fill(**base, action="skip", reason="already checked")
        if (desired or "").strip().lower() not in {"yes", "true", "y", "1"}:
            return Fill(**base, action="skip", reason="no answer")
        return Fill(**base, action="checkbox", value="Yes", key=key)

    if field.tag == "select" or field.options:
        choice = pick_option_for_key(field.options, desired, key)
        if not choice:
            return Fill(**base, action="skip", value=desired, options=field.options,
                        confidence="low", reason="no matching option")
        return Fill(**base, action="select", value=choice, options=field.options, key=key)

    if field.role == "combobox" or field.tag not in {"input", "textarea"}:
        return Fill(**base, action="combobox", value=desired, key=key, confidence="low",
                    values=_option_candidates(key, desired))

    return Fill(**base, action="text", value=desired, key=key)


def _resolve_one(
    profile: ApplicantProfile,
    field: ScrapedField,
    job_title: str,
    company: str,
    overwrite: bool,
) -> Fill:
    label = field.label or field.placeholder or field.name or ""
    ftype = (field.type or field.tag or "").lower()
    base = {"idx": field.idx, "selector": field.selector, "label": label}

    if is_skip_field(label, field.name, field.autocomplete, field.automation):
        return Fill(**base, action="skip", reason="honeypot")

    if ftype == "file":
        if not _is_resume_input(field):
            return Fill(**base, action="skip", reason="not a resume input")
        return Fill(**base, action="file", key="resume", reason="resume")

    already_set = bool((field.value or "").strip())
    if already_set and not overwrite and ftype != "checkbox":
        return Fill(**base, action="skip", reason="already filled")

    # A field inside a repeating block answers for *that* entry — "Company" in
    # work-experience 2 is the second employer, not the current one.
    if field.section_kind in KINDS:
        desired = section_value(profile, field.section_kind, field.section_index or 1, label)
        key = f"{field.section_kind}[{field.section_index or 1}]"
        if not desired:
            return Fill(**base, action="skip", key=key, reason="no value for this entry")
        return _emit(field, base, desired, key, ftype)

    # A skills picker takes many values, not one string.
    if _is_skill_multiselect(field, label):
        skills = skill_list(profile)
        if skills:
            return Fill(**base, action="multiselect", value=skills[0], values=skills,
                        key="skills", confidence="low")

    desired = resolve_value(profile, label, job_title, company)
    key = match_field_key(label) or ""

    if ftype in {"tel", "phone"} or "phone" in f"{label} {field.name}".lower():
        desired = _phone_value(profile, field, desired)
        key = key or "phone"

    # <input type="date"> silently rejects anything that is not YYYY-MM-DD, so
    # a human answer like "30 days" or "Immediate" becomes a real date.
    if ftype == "date" and desired and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", desired.strip()):
        desired = profile.available_date_iso()

    if ftype == "checkbox":
        if field.checked:
            return Fill(**base, action="skip", reason="already checked")
        consent = is_consent_label(label)
        want_yes = (desired or "").strip().lower() in {"yes", "true", "y", "1"} or consent
        if not want_yes:
            return Fill(**base, action="skip", reason="no answer")
        return Fill(**base, action="checkbox", value="Yes", key=key or "consent",
                    confidence="high" if consent else "low")

    if not desired:
        return Fill(**base, action="skip", reason="no match")

    if field.tag == "select" or field.options:
        choice = pick_option_for_key(field.options, desired, key)
        if not choice:
            return Fill(**base, action="skip", value=desired, options=field.options,
                        confidence="low", reason="no matching option")
        return Fill(**base, action="select", value=choice, options=field.options,
                    key=key, confidence="high")

    if field.role == "combobox" or field.tag not in {"input", "textarea"}:
        return Fill(**base, action="combobox", value=desired, key=key, confidence="low",
                    values=_option_candidates(key, desired))

    if ftype in TEXTUAL_TYPES or field.tag in {"input", "textarea"}:
        return Fill(**base, action="text", value=desired, key=key,
                    confidence="high" if key else "low")

    return Fill(**base, action="skip", reason=f"unsupported type {ftype}")


def _radio_group_key(field: ScrapedField) -> str:
    """
    Which question a radio belongs to. By `name` where the page gives one;
    otherwise by the question container the scraper saw (`group_key`), then
    by the question text within its entry. Only a radio with none of those
    stands alone — and `_resolve_radio_group` then clicks it only if its own
    label is the answer.
    """
    if field.name:
        return f"name:{field.name}"
    if field.group_key:
        return f"box:{field.group_key}"
    if field.group_label:
        return f"q:{field.section_kind}|{field.section_index}|{field.group_label.strip().lower()}"
    return f"__{field.idx}"


# Word ends for radio-label matching: "No, I will not" starts with the word
# "no"; "Yes, I will now…" does not contain it.
_RADIO_DELIM = r"[\s()\[\],/|:;!?]"
_RADIO_NEGATION = re.compile(r"(?:^|[^a-z])(?:no|not|never|none|cannot)(?![a-z])|n['’]t(?![a-z])")
_RADIO_DECLINE = ("decline", "prefer not", "do not wish", "don't wish", "not to answer", "not to say")
_RADIO_YES = {"yes", "y", "true", "1"}
_RADIO_NO = {"no", "n", "false", "0"}


def _radio_norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _starts_with_word(text: str, word: str) -> bool:
    return bool(word) and bool(re.match(rf"{re.escape(word)}(?:$|{_RADIO_DELIM}|[.\-](?:\s|$))", text))


def _contains_word(text: str, word: str) -> bool:
    return bool(word) and bool(
        re.search(rf"(?:^|{_RADIO_DELIM}){re.escape(word)}(?:$|{_RADIO_DELIM}|[.\-](?:\s|$))", text)
    )


def _leading_yes_no(text: str) -> str:
    for word in ("yes", "no"):
        if _starts_with_word(text, word):
            return word
    return ""


def _radio_rank(label: str, desired: str) -> int:
    """0 exact … 4 whole word inside; -1 when this label is not the answer."""
    text, want = _radio_norm(label), _radio_norm(desired)
    if want in _RADIO_YES:
        want = "yes"
    elif want in _RADIO_NO:
        want = "no"
    if not text or not want:
        return -1
    if text == want:
        return 0
    lead = _leading_yes_no(want)
    if lead:
        if _leading_yes_no(text) != lead:
            return -1
        if want == lead:
            return 1
    if any(x in want for x in _RADIO_DECLINE) and any(x in text for x in _RADIO_DECLINE):
        return 1
    if bool(_RADIO_NEGATION.search(text)) != bool(_RADIO_NEGATION.search(want)):
        return -1
    if _starts_with_word(text, want):
        return 2
    if _starts_with_word(want, text):
        return 3
    if _contains_word(text, want):
        return 4
    return -1


def _radio_label_matches(label: str, desired: str) -> bool:
    return _radio_rank(label, desired) >= 0


def _best_radio_label(labels: List[str], desired: str) -> Optional[str]:
    ranked = [(r, len(l), i) for i, l in enumerate(labels) if (r := _radio_rank(l, desired)) >= 0]
    return labels[min(ranked)[2]] if ranked else None


def _resolve_radio_group(
    profile: ApplicantProfile,
    group: List[ScrapedField],
    job_title: str,
    company: str,
) -> List[Fill]:
    """One radio group answers one question: resolve the question, then pick the option."""
    if any(f.checked for f in group):
        return []
    question = next((f.group_label for f in group if f.group_label), "") or group[0].label
    desired = resolve_value(profile, question, job_title, company)
    if not desired:
        return []
    labels = [f.option_label or f.label or f.value for f in group]
    choice = pick_option(labels, desired)
    # A radio is only clicked when its own label says the answer. pick_option
    # returns the sole option of a one-item list whatever was asked, so a
    # radio the grouping could not pair with its siblings would otherwise be
    # "answered" Yes to a question whose answer is No.
    if not choice or not _radio_label_matches(choice, desired):
        choice = _best_radio_label(labels, desired)
    if not choice:
        return []
    for field, option in zip(group, labels):
        if option == choice:
            return [Fill(
                idx=field.idx, selector=field.selector, action="radio",
                value=choice, label=question, key="radio", confidence="high",
            )]
    return []


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/ping")
def ping():
    """Extension handshake: is the backend up, and is the profile usable?"""
    profile = load_profile()
    missing = profile.missing_required()
    return {
        "ok": True,
        "name": profile.full_name,
        "email": profile.email,
        "has_resume": profile.resume_file() is not None,
        "missing": missing,
        "ready": not missing,
    }


@router.post("/resolve", response_model=ResolveResponse)
def resolve(req: ResolveRequest, db: Session = Depends(get_db)):
    profile = load_profile()
    job = _lookup_job(db, req.url)
    job_title = req.job_title or (job.title if job else "") or _clean_title(req.page_title)
    company = req.company or (job.company if job else "") or _company_from_url(req.url)

    fills: List[Fill] = []
    radio_groups: Dict[str, List[ScrapedField]] = {}

    # "Will you require sponsorship?" means sponsorship where the job is. Only
    # a known job has a location; an unknown page leaves such questions blank.
    token = JOB_LOCATION.set((job.location if job else "") or "")
    try:
        for field in req.fields:
            if (field.type or "").lower() == "radio":
                radio_groups.setdefault(_radio_group_key(field), []).append(field)
                continue
            fills.append(_resolve_one(profile, field, job_title, company, req.overwrite))

        for group in radio_groups.values():
            fills.extend(_resolve_radio_group(profile, group, job_title, company))
    finally:
        JOB_LOCATION.reset(token)

    actionable = [f for f in fills if f.action != "skip"]
    answered = {f.idx for f in actionable}

    unresolved = [
        (f.label or f.placeholder or f.name)
        for f in req.fields
        if f.required
        and f.idx not in answered
        and not (f.value or "").strip()
        and not f.checked
        and (f.type or "").lower() != "radio"
    ]
    # A required radio group counts as unanswered only if no option in it got a fill.
    for group in radio_groups.values():
        if any(f.required for f in group) and not any(f.idx in answered for f in group):
            question = next((f.group_label for f in group if f.group_label), "") or group[0].label
            if question:
                unresolved.append(question)

    return ResolveResponse(
        ats=detect_ats(req.url),
        job_title=job_title,
        company=company,
        job_id=job.id if job else None,
        known_job=bool(job),
        has_resume=profile.resume_file() is not None,
        sections_needed=needed_counts(profile),
        fills=fills,
        unresolved=[u for u in unresolved if u][:20],
        stats={
            "scraped": len(req.fields),
            "filled": len(actionable),
            "skipped": len(fills) - len(actionable),
            "unresolved": len(unresolved),
        },
    )


_APPLY_SUFFIX_RE = re.compile(r"/apply(?:/.*)?$", re.I)


def _job_for_page(db: Session, urls: List[str]) -> Optional[Job]:
    """
    The job a form page belongs to. Workday and Lever put the form at the
    posting URL plus `/apply…`, so that suffix is tried off as well. Nothing
    looser than that: a wrong match would attach another job's resume.
    """
    for url in urls:
        if not url:
            continue
        bare = url.split("#")[0].split("?")[0].rstrip("/")
        for candidate in (url, _APPLY_SUFFIX_RE.sub("", bare)):
            job = _lookup_job(db, candidate)
            if job:
                return job
    return None


@router.get("/resume")
def resume(url: str = "", tab_url: str = "", db: Session = Depends(get_db)):
    """
    Resume bytes for the extension to drop into a file input — the tailored PDF
    for the job on this page when one exists, otherwise the default resume.
    """
    from backend.resume.tailor import tailored_upload

    profile = load_profile()
    default = profile.resume_file()
    job = _job_for_page(db, [url, tab_url])
    path = tailored_upload(job.id, default.name if default else "") if job else None
    tailored = path is not None
    path = path or default
    if not path:
        raise HTTPException(status_code=404, detail="No resume on file — upload one in Settings")
    if job:
        # A page that is not a known job cannot be joined to an outcome later.
        from backend.resume import sends

        sends.record(db, job.id, path, channel="extension", tailored=tailored)
    mime = mimetypes.guess_type(path.name)[0] or "application/pdf"
    return {
        "filename": path.name,
        "mime": mime,
        "b64": base64.b64encode(path.read_bytes()).decode("ascii"),
        "tailored": tailored,
        "job_id": job.id if job else "",
    }


# ─── Tailor from the extension ───────────────────────────────────────────────
#
# The description comes from the best source there is, in this order:
#   1. text the user selected on the page — an explicit choice wins;
#   2. the description the scanner stored for this job;
#   3. the description container on the page (Greenhouse/Lever/Ashby show it
#      above the form; Workday on the posting page);
#   4. one the extension saw earlier in the same tab — Workday's application
#      steps show no description at all, but its posting page did.
# A page that is not a known job becomes one, so the later Autofill finds the
# tailored PDF by the same URL lookup it already uses.
#
# Tailoring takes one to four minutes on free models and the extension's
# requests time out in twelve seconds, so POST starts it and GET reports it.

MIN_JD_CHARS = 300
MAX_JD_CHARS = 20000

_tailor_lock = threading.Lock()
_tailor_runs: Dict[str, Dict[str, object]] = {}   # job_id -> status
_session_factory = None                           # tests point this at a throwaway DB


def _new_session():
    if _session_factory is not None:
        return _session_factory()
    from backend.db.models import SessionLocal

    return SessionLocal()


class TailorRequest(BaseModel):
    url: str
    tab_url: str = ""
    title: str = ""
    company: str = ""
    jd_text: str = ""
    jd_source: str = ""             # selection | page | remembered


def _run_tailor(job_id: str, jd_text: str, title: str, company: str) -> None:
    from backend.resume import tailor as tailor_mod

    try:
        res = tailor_mod.tailor(jd_text, job_title=title, company=company, job_id=job_id)
        report = res.report or {}
        status = {
            "state": "done" if res.ok else "failed",
            "ok": res.ok,
            "error": res.error,
            "pages": (report.get("pdf") or {}).get("pages"),
            "reworded": report.get("model_rewrites", 0),
            "refused": len(report.get("rejected") or []),
            "reordered": len(report.get("reordered") or []) + len(report.get("skills_reordered") or []),
            "notes": str(report.get("notes") or "")[:300],
        }
        if res.ok:
            db = _new_session()
            try:
                job = db.get(Job, job_id)
                # Only an open job moves to "tailored"; the user's own
                # statuses (applied, rejected, ignored) are left alone.
                if job and job.status in {"discovered", "scored", "tailored"}:
                    job.status = "tailored"
                    db.commit()
            finally:
                db.close()
    except Exception as exc:  # noqa: BLE001 - reported to the extension, not raised
        status = {"state": "failed", "ok": False, "error": str(exc)[:300]}
    with _tailor_lock:
        _tailor_runs[job_id].update(status, finished_at=datetime.utcnow().isoformat())


@router.post("/tailor")
def start_tailor(req: TailorRequest, db: Session = Depends(get_db)):
    """Start tailoring the resume for the job on this page."""
    if not re.match(r"^https?://", req.url or "", re.I):
        raise HTTPException(status_code=400, detail="Tailoring needs a job page URL")
    with _tailor_lock:
        busy = next((jid for jid, s in _tailor_runs.items() if s.get("state") == "running"), None)
    if busy:
        raise HTTPException(status_code=409, detail="Already tailoring a resume — wait for it to finish")

    page_jd = " ".join((req.jd_text or "").split())[:MAX_JD_CHARS]
    job = _job_for_page(db, [req.url, req.tab_url])
    saved = " ".join((job.jd_text or "").split()) if job else ""

    if req.jd_source == "selection" and len(page_jd) >= MIN_JD_CHARS:
        jd, source = page_jd, "your selection"
    elif len(saved) >= MIN_JD_CHARS:
        jd, source = saved, "the saved job description"
    elif len(page_jd) >= MIN_JD_CHARS:
        jd, source = page_jd, "this page" if req.jd_source != "remembered" else "the posting page"
    else:
        raise HTTPException(
            status_code=400,
            detail="No job description found. Select the description text on the page, then press Tailor again.",
        )

    if job is None:
        job = Job(
            id=str(uuid.uuid4()),
            title=(_clean_title(req.title) or "Untitled role")[:200],
            company=(req.company or _company_from_url(req.url) or "Unknown")[:200],
            platform="extension",
            url=req.url.split("#")[0],
            location="",
            jd_text=jd,
            relevance_score=100.0,
            fit_reason="Tailored from the browser extension",
            missing_skills="[]",
            seniority_level="entry",
            is_engineering_role=True,
            status="discovered",
            ats_type=detect_ats(req.url),
        )
        db.add(job)
    elif len(saved) < MIN_JD_CHARS:
        job.jd_text = jd                 # keep it for the next tailor and for scoring
    db.commit()

    with _tailor_lock:
        _tailor_runs[job.id] = {
            "state": "running", "job_id": job.id, "source": source,
            "company": job.company, "started_at": datetime.utcnow().isoformat(),
        }
    threading.Thread(
        target=_run_tailor, args=(job.id, jd, job.title, job.company), daemon=True
    ).start()
    return {"job_id": job.id, "state": "running", "source": source, "jd_chars": len(jd)}


@router.get("/tailor/{job_id}")
def tailor_status(job_id: str):
    """Progress of a tailoring run started from the extension."""
    with _tailor_lock:
        status = dict(_tailor_runs.get(job_id) or {})
    if not status:
        raise HTTPException(status_code=404, detail="No tailoring run for this job")
    if status.get("ok"):
        status["pdf_url"] = f"/api/autopilot/resume/{job_id}"
    return status


@router.post("/track")
def track(req: TrackRequest, db: Session = Depends(get_db)):
    """Record an application submitted by hand in the browser, so the dashboard knows."""
    job = _lookup_job(db, req.url)
    created = False
    if not job:
        job = Job(
            id=str(uuid.uuid4()),
            title=req.title or "Untitled role",
            company=req.company or _company_from_url(req.url) or "Unknown",
            platform="extension",
            url=req.url,
            location="",
            jd_text="",
            relevance_score=100.0,
            fit_reason="Added from the browser extension",
            missing_skills="[]",
            seniority_level="entry",
            is_engineering_role=True,
            status="discovered",
            ats_type=detect_ats(req.url),
        )
        db.add(job)
        created = True

    if req.status == "applied":
        job.status = "applied"
        job.apply_status = "applied"
        job.apply_error = ""
        job.applied_at = datetime.utcnow()
    elif not job.visited_at:
        job.visited_at = datetime.utcnow()
    db.commit()
    db.refresh(job)

    seen_ledger.mark(
        db,
        url=job.url,
        status="applied" if req.status == "applied" else "visited",
        reason=req.notes or "browser extension",
        title=job.title or "",
        company=job.company or "",
        platform=job.platform or "extension",
        commit=True,
    )
    return {"success": True, "job_id": job.id, "created": created, "status": job.status}
