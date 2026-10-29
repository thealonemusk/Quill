"""
Autopilot API — select the best N jobs, tailor, fill, and queue for review.

Deliberately a separate router with its own status dict and its own URL prefix,
so nothing here can disturb the existing scan and apply endpoints the dashboard
depends on.

The run never submits. It fills each application, screenshots it, and leaves a
review queue — the user approves. That is a design decision, not a limitation:
a mis-parsed field reaching a real employer cannot be recalled, and at the
companies being targeted a wasted application costs months.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.applier.routing import Route
from backend.autopilot.select import Tier, cap_jobs_per_company, select
from backend.config import settings
from backend.db.models import Job, SessionLocal, get_db
from backend.resume.tailor import tailored_pdf
from backend.scrapers.filter_utils import EUROPE_PREFERRED_LOCATIONS, job_in_region

router = APIRouter(prefix="/api/autopilot", tags=["autopilot"])

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SHOT_DIR = PROJECT_ROOT / "data" / "apply_logs"
CLOSED_STATUSES = ["applied", "interviewing", "offer", "rejected", "ignored"]

# Same shape as _scan_status / _apply_status in main.py, kept separate so an
# autopilot run and a plain scan cannot corrupt each other's state.
_status: Dict[str, Any] = {
    "running": False,
    "stage": "",
    "done": 0,
    "total": 0,
    "current": "",
    "last_result": None,
    "started_at": None,
}


# ── Schemas ──────────────────────────────────────────────────────────────────

class SelectRequest(BaseModel):
    limit: int = Field(30, ge=1, le=200)
    min_tier: str = "UNKNOWN"
    auto_only: bool = False


class RunRequest(BaseModel):
    job_ids: List[str] = Field(default_factory=list)
    tailor: bool = True
    auto_submit: bool = False      # never true by default; the queue is the point


def _tier_from_name(name: str) -> Tier:
    try:
        return Tier[(name or "UNKNOWN").upper()]
    except KeyError:
        return Tier.UNKNOWN


# ── Selection ────────────────────────────────────────────────────────────────

@router.get("/candidates")
def candidates(
    limit: int = Query(30, ge=1, le=200),
    min_tier: str = Query("UNKNOWN"),
    auto_only: bool = Query(False),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Rank every known job and return the chosen set plus what was passed over.

    The rejected list is returned on purpose: it is the only way to tell whether
    the criteria are excluding what you meant them to.
    """
    # Rejected and ignored are decisions, not a queue: re-selecting them
    # re-filled jobs the user had closed, and "tailored" overwrote the status.
    region = settings.active_region
    jobs = [
        job for job in db.query(Job).filter(~Job.status.in_(CLOSED_STATUSES)).all()
        if job_in_region(job, region)
    ]
    if region == "europe":
        jobs.sort(key=lambda job: -(job.relevance_score or 0))
        jobs = cap_jobs_per_company(jobs)
    preferred = (
        EUROPE_PREFERRED_LOCATIONS if region == "europe"
        else settings.preferred_locations_list
    )
    chosen, passed = select(
        jobs,
        preferred_locations=preferred,
        limit=limit,
        min_tier=_tier_from_name(min_tier),
        auto_only=auto_only,
    )

    tally: Dict[str, int] = {}
    for c in chosen:
        tally[c.tier.name] = tally.get(c.tier.name, 0) + 1
    routes: Dict[str, int] = {}
    for c in chosen:
        routes[c.routing.route.value] = routes.get(c.routing.route.value, 0) + 1

    return {
        "selected": [c.as_dict() for c in chosen],
        "passed_over": [c.as_dict() for c in passed[:60]],
        "stats": {
            "pool": len(jobs),
            "selected": len(chosen),
            "eligible": len([c for c in passed if c.ok]) + len(chosen),
            "by_tier": tally,
            "by_route": routes,
        },
    }


# ── The run ──────────────────────────────────────────────────────────────────

@router.get("/status")
def status() -> Dict[str, Any]:
    return _status


@router.post("/stop")
def stop() -> Dict[str, Any]:
    from backend.applier.control import request_stop

    if not _status["running"]:
        return {"message": "Autopilot is not running", "running": False}
    request_stop()
    return {"message": "Stop requested", "running": True}


@router.post("/run")
async def run(req: RunRequest, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """Tailor and fill the chosen jobs, then stop for review."""
    if _status["running"]:
        raise HTTPException(status_code=409, detail="Autopilot is already running")

    # Pre-flight in the endpoint, before the task is queued, so failures are
    # visible as an HTTP error rather than buried in a background log.
    from backend.api.main import _apply_status, _scan_status

    if _scan_status.get("running"):
        raise HTTPException(status_code=409, detail="A scan is running — wait for it to finish")
    if _apply_status.get("running"):
        raise HTTPException(status_code=409, detail="An apply run is in progress")
    if not req.job_ids:
        raise HTTPException(status_code=400, detail="No jobs selected")

    from backend.applier.profile import load_profile

    profile = load_profile()
    missing = [m for m in profile.missing_required() if m != "resume"]
    if missing:
        raise HTTPException(status_code=400, detail="Profile incomplete: " + ", ".join(missing))

    async def task() -> None:
        from backend.applier.control import clear_stop
        from backend.applier.engine import run_apply_pipeline

        clear_stop()
        _status.update(running=True, stage="starting", done=0,
                       total=len(req.job_ids), current="", last_result=None,
                       started_at=datetime.utcnow().isoformat())
        result: Dict[str, Any] = {"tailored": [], "apply": None, "errors": []}
        try:
            if req.tailor:
                _status["stage"] = "tailoring"
                result["tailored"] = await asyncio.to_thread(_tailor_all, req.job_ids)

            _status.update(stage="filling", done=0)
            result["apply"] = await run_apply_pipeline(
                job_ids=req.job_ids,
                auto_submit=req.auto_submit,
                headless=False,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced through status
            logger.exception("Autopilot run failed")
            result["errors"].append(str(exc))
        finally:
            _status.update(running=False, stage="done", current="", last_result=result)

    # Claimed here, not inside the task: a background task starts after the
    # response, so a double click used to pass the check above twice and start
    # two runs over the same jobs.
    _status["running"] = True
    background_tasks.add_task(task)
    return {"message": "Autopilot started", "running": True, "jobs": len(req.job_ids)}


def _tailor_all(job_ids: List[str]) -> List[Dict[str, Any]]:
    """Tailor each job's resume. A failure here must not stop the fill stage."""
    from backend.resume import tailor as tailor_mod

    out: List[Dict[str, Any]] = []
    db = SessionLocal()
    try:
        for i, job_id in enumerate(job_ids, 1):
            try:
                job = db.query(Job).filter(Job.id == job_id).first()
            except Exception as exc:  # noqa: BLE001
                # A failed commit on the previous job (SQLite "database is
                # locked" while the extension writes) leaves the session
                # needing a rollback; without one every later query raised,
                # _tailor_all died, and the fill stage never ran.
                db.rollback()
                out.append({"job_id": job_id, "ok": False, "error": f"database: {exc}"})
                continue
            if not job:
                continue
            _status.update(done=i, current=f"{job.title} @ {job.company}")
            if not (job.jd_text or "").strip():
                out.append({"job_id": job_id, "ok": False, "error": "no job description stored"})
                continue
            try:
                res = tailor_mod.tailor(
                    job.jd_text, job_title=job.title, company=job.company, job_id=job.id
                )
                out.append({
                    "job_id": job_id,
                    "ok": res.ok,
                    "pdf": res.pdf_path,
                    "error": res.error,
                    "coverage": (res.report.get("coverage", {}) or {}).get("delta"),
                })
                # Only an open job moves to "tailored"; a status the user
                # set (applied, rejected, ignored) is theirs, not the bot's.
                if res.ok and job.status in {"discovered", "scored", "tailored"}:
                    job.status = "tailored"
                    db.commit()
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                out.append({"job_id": job_id, "ok": False, "error": str(exc)})
    finally:
        db.close()
    return out


# ── Review queue ─────────────────────────────────────────────────────────────

@router.get("/queue")
def queue(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Everything waiting on a human: filled-but-unsubmitted, and manual jobs."""
    rows = (
        db.query(Job)
        .filter(Job.apply_status.in_(["needs_review", "manual_queue", "failed"]))
        .order_by(Job.relevance_score.desc())
        .all()
    )
    items = []
    for job in rows:
        shot = SHOT_DIR / f"{job.id}-filled.png"
        if not shot.is_file():
            shot = SHOT_DIR / f"{job.id}.png"
        # The same lookup the apply path uses, so "tailored" here means that
        # PDF is what gets uploaded.
        pdf = tailored_pdf(job.id)
        items.append({
            "job_id": job.id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "url": job.url,
            "ats": job.ats_type or "",
            "apply_status": job.apply_status or "",
            "reason": job.apply_error or "",
            "has_screenshot": shot.is_file(),
            "has_tailored_resume": bool(pdf),
        })
    return {"items": items, "count": len(items)}


@router.get("/screenshot/{job_id}")
def screenshot(job_id: str):
    """The filled form, as the bot left it."""
    # Joined into a path: "..\\x" would walk out of the log directory.
    if not re.fullmatch(r"[0-9A-Za-z-]{8,64}", job_id or ""):
        raise HTTPException(status_code=404, detail="No screenshot for this job")
    for name in (f"{job_id}-filled.png", f"{job_id}.png"):
        path = SHOT_DIR / name
        if path.is_file():
            return FileResponse(path, media_type="image/png")
    raise HTTPException(status_code=404, detail="No screenshot for this job")


@router.get("/callbacks")
def callbacks(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Callback rate by how the resume was prepared — whether tailoring pays."""
    from backend.resume.sends import callback_stats

    return callback_stats(db)


@router.get("/resume/{job_id}")
def tailored_resume(job_id: str):
    """The tailored PDF produced for this job."""
    match = tailored_pdf(job_id)
    if match:
        return FileResponse(match, media_type="application/pdf",
                            filename=f"resume_{job_id[:8]}.pdf")
    raise HTTPException(status_code=404, detail="No tailored resume for this job")


class DecisionRequest(BaseModel):
    status: str = "applied"        # applied | ignored
    notes: str = ""


@router.post("/queue/{job_id}/decide")
def decide(job_id: str, req: DecisionRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Record what the user did with a queued application."""
    from backend import seen_ledger

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if req.status == "applied":
        job.status = "applied"
        job.apply_status = "applied"
        job.applied_at = datetime.utcnow()
    else:
        job.status = "ignored"
        job.apply_status = "skipped"
    job.apply_error = req.notes or job.apply_error
    db.commit()

    seen_ledger.mark(
        db, url=job.url, status="applied" if req.status == "applied" else "dismissed",
        reason=req.notes or "autopilot review", title=job.title or "",
        company=job.company or "", platform=job.platform or "", commit=True,
    )
    return {"success": True, "job_id": job_id, "status": job.status}


# ── Workday accounts ─────────────────────────────────────────────────────────
#
# Every Workday employer is a separate tenant with its own login, so an account
# has to be recorded per employer. Passwords are write-only through this API:
# they go in, they are never read back out.

class WorkdayAccountIn(BaseModel):
    tenant: str
    email: str
    password: str
    label: str = ""
    note: str = ""


@router.get("/workday/accounts")
def workday_accounts(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Recorded logins, plus the employers in your queue that still need one."""
    from backend.applier.workday_creds import load_accounts, missing_tenants, signup_url

    accounts = load_accounts()
    urls = [
        j.url for j in db.query(Job)
        .filter(Job.url.like("%myworkday%"))
        .filter(~Job.status.in_(["applied", "interviewing", "offer"]))
        .all()
    ]
    missing = missing_tenants(urls)
    by_tenant = {}
    for url in urls:
        from backend.applier.workday_creds import tenant_of
        t = tenant_of(url)
        if t in missing and t not in by_tenant:
            by_tenant[t] = signup_url(url)

    return {
        "accounts": [a.redacted() for a in accounts.values()],
        "missing": [{"tenant": t, "signup_url": by_tenant.get(t, "")} for t in missing],
    }


@router.post("/workday/accounts")
def save_workday_account(req: WorkdayAccountIn) -> Dict[str, Any]:
    from backend.applier.workday_creds import save_account

    if not req.tenant.strip() or not req.email.strip() or not req.password:
        raise HTTPException(status_code=400, detail="tenant, email and password are all required")
    account = save_account(req.tenant, req.email, req.password, req.label, req.note)
    return {"success": True, "account": account.redacted()}


@router.delete("/workday/accounts/{tenant}")
def delete_workday_account(tenant: str) -> Dict[str, Any]:
    from backend.applier.workday_creds import delete_account

    if not delete_account(tenant):
        raise HTTPException(status_code=404, detail=f"No account recorded for {tenant}")
    return {"success": True, "tenant": tenant}
