"""
FastAPI REST API for Quill dashboard.
"""
import asyncio
import re
import sys
from pathlib import Path
from typing import List, Optional
from datetime import datetime, timedelta

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import func
from sqlalchemy.orm import Session
from loguru import logger

from backend.job_freshness import JOB_FRESHNESS_HOURS
from backend.db.models import Job, get_db, init_db
from backend import seen_ledger
from backend.config import settings
from backend.platforms import DEFAULT_SCAN_PLATFORMS, SCAN_PLATFORMS, ALL_PLATFORMS
from backend.applier.ats import detect_ats
from backend.autopilot.select import cap_jobs_per_company, company_tier
from backend.applier.profile import RESUMES_DIR, load_profile, save_profile
from backend.api.autofill import router as autofill_router
from backend.api.autopilot import router as autopilot_router
from backend.scrapers.filter_utils import job_in_region

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _env_path() -> Path:
    path = PROJECT_ROOT / "config" / ".env"
    if not path.exists():
        path = PROJECT_ROOT / ".env"
    return path

app = FastAPI(title="Quill API", version="1.0.0")

DASHBOARD_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


def _extension_origin_re() -> str:
    # The service worker calls from chrome-extension://<id>. An unpacked
    # install's id changes per machine, so any extension is accepted unless
    # EXTENSION_ID pins this one — then no other installed extension can read
    # the profile or the resume.
    pinned = (getattr(settings, "extension_id", "") or "").strip()
    if pinned and re.fullmatch(r"[a-p]{32}", pinned):
        return rf"^chrome-extension://{pinned}$"
    return r"^(chrome|moz)-extension://[A-Za-z0-9-]+$"


_EXTENSION_ORIGIN = re.compile(_extension_origin_re())

app.add_middleware(
    CORSMiddleware,
    allow_origins=DASHBOARD_ORIGINS,
    allow_origin_regex=_extension_origin_re(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@app.middleware("http")
async def local_only(request, call_next):
    """
    Two checks CORS does not make.

    Host: a page on the internet can rebind its own domain to 127.0.0.1 and
    then read this API as same-origin. Its requests still carry that domain in
    the Host header, so anything but localhost is refused.

    Origin on writes: CORS stops a page *reading* a response, not *sending* the
    request. A "simple" POST (no-cors, no Content-Type) reaches the handler
    with no preflight, and FastAPI parses the body as JSON anyway — so any site
    could start an apply or rewrite .env. Browsers send Origin on every
    cross-origin POST; one that is not the dashboard or the extension is
    refused. Scripts on this machine send none and are unaffected.
    """
    from fastapi.responses import JSONResponse

    host = (request.url.hostname or "").lower()
    if host not in _LOCAL_HOSTS:
        return JSONResponse({"detail": "Quill only answers on localhost"}, status_code=403)
    origin = request.headers.get("origin")
    if request.method not in _SAFE_METHODS and origin:
        allowed = origin in DASHBOARD_ORIGINS or _EXTENSION_ORIGIN.match(origin)
        if not allowed:
            return JSONResponse({"detail": f"Origin {origin} may not change Quill"}, status_code=403)
    return await call_next(request)

app.include_router(autofill_router)
app.include_router(autopilot_router)


@app.on_event("startup")
async def startup():
    init_db()
    logger.info("Quill API started")


# ─── Pydantic Schemas ────────────────────────────────────────────────────────

class JobOut(BaseModel):
    id: str
    title: str
    company: str
    platform: str
    url: str
    location: str
    experience_required: str
    salary_range: str
    relevance_score: float
    fit_reason: str
    missing_skills: str
    seniority_level: str
    is_engineering_role: bool
    easy_apply: bool
    recruiter_name: str
    recruiter_profile: str
    status: str
    ats_type: Optional[str] = ""
    apply_status: Optional[str] = ""
    apply_error: Optional[str] = ""
    discovered_at: Optional[datetime]
    visited_at: Optional[datetime]
    applied_at: Optional[datetime]
    tier: str = "UNKNOWN"
    tier_value: int = 0

    class Config:
        from_attributes = True

    @field_validator("ats_type", "apply_status", "apply_error", mode="before")
    @classmethod
    def blank_apply_fields(cls, value):
        return value or ""

    @model_validator(mode="after")
    def attach_tier(self):
        tier = company_tier(self.company)
        self.tier = tier.name
        self.tier_value = int(tier)
        return self


class ScanRequest(BaseModel):
    platforms: List[str] = list(DEFAULT_SCAN_PLATFORMS)
    max_jobs_per_platform: int = 400
    headless: bool = True


class StatusUpdate(BaseModel):
    status: str
    notes: Optional[str] = None


class ApplyRequest(BaseModel):
    job_ids: Optional[List[str]] = None
    auto_submit: bool = False  # ignored: Quill never submits
    headless: bool = False


class ManualJobRequest(BaseModel):
    title: str
    company: str
    url: str
    jd_text: str
    location: str = ""
    platform: str = "manual"

    @field_validator("url")
    @classmethod
    def http_only(cls, value: str) -> str:
        # The dashboard renders this as a link; "javascript:..." would run on
        # the dashboard's origin, which the API trusts with credentials.
        if not re.match(r"^https?://", (value or "").strip(), re.I):
            raise ValueError("url must start with http:// or https://")
        return value.strip()


# ─── Jobs Endpoints ──────────────────────────────────────────────────────────

@app.get("/api/jobs", response_model=List[JobOut])
def get_jobs(
    status: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None),
    max_age_hours: int = Query(JOB_FRESHNESS_HOURS, ge=1),
    db: Session = Depends(get_db),
):
    """Get all jobs with optional filters. Hides stale discovered/scored jobs by default."""
    query = db.query(Job)
    if status:
        query = query.filter(Job.status == status)
    if platform:
        query = query.filter(Job.platform == platform)
    if min_score is not None:
        query = query.filter(Job.relevance_score >= min_score)

    cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
    query = query.filter(
        (Job.status.notin_(["discovered", "scored"])) | (Job.discovered_at >= cutoff)
    )
    jobs = query.order_by(Job.relevance_score.desc()).all()
    dirty = False
    for job in jobs:
        if not job.ats_type:
            job.ats_type = detect_ats(job.url)
            dirty = True
        if job.apply_status is None:
            job.apply_status = ""
            dirty = True
        if job.apply_error is None:
            job.apply_error = ""
            dirty = True
    if dirty:
        db.commit()
    visible = [job for job in jobs if job_in_region(job, settings.active_region)]
    if settings.active_region == "europe":
        # Amazon alone was most of the Europe list. Eight per employer
        # leaves room for the other sponsors.
        visible = cap_jobs_per_company(visible)
    return visible


@app.get("/api/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.ats_type:
        job.ats_type = detect_ats(job.url)
        db.commit()
    return job


@app.post("/api/jobs/manual")
def add_manual_job(req: ManualJobRequest, db: Session = Depends(get_db)):
    """Add a job manually."""
    import uuid

    existing = db.query(Job).filter(Job.url == req.url).first()
    if existing:
        raise HTTPException(status_code=409, detail="Job with this URL already exists")

    job = Job(
        id=str(uuid.uuid4()),
        title=req.title,
        company=req.company,
        platform=req.platform,
        url=req.url,
        location=req.location,
        jd_text=req.jd_text,
        relevance_score=100.0,
        fit_reason="Manually added job posting",
        missing_skills="[]",
        seniority_level="entry",
        is_engineering_role=True,
        status="discovered",
        ats_type=detect_ats(req.url),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return {"job_id": job.id, "score": job.relevance_score, "message": "Job added"}


@app.patch("/api/jobs/{job_id}/status")
def update_job_status(job_id: str, update: StatusUpdate, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.status = update.status
    if update.status == "applied":
        job.applied_at = datetime.utcnow()
    if update.notes and job.application:
        job.application.notes = update.notes
    # A status that is a decision (applied, rejected, ignored -> dismissed, ...)
    # goes to the ledger now, not only when some later purge happens to read
    # it off the row: before this, "rejected" never reached it at all.
    decision = seen_ledger.decision_for(job, default="")
    if decision:
        seen_ledger.mark(
            db,
            url=job.url,
            status=decision,
            reason=f"set to {update.status} on the dashboard",
            title=job.title or "",
            company=job.company or "",
            platform=job.platform or "",
        )
    db.commit()
    return {"success": True, "status": update.status}


@app.post("/api/jobs/{job_id}/visit")
def mark_job_visited(job_id: str, db: Session = Depends(get_db)):
    """Mark a job as visited when the user opens the posting link."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.visited_at:
        job.visited_at = datetime.utcnow()
        db.commit()
    return {"success": True, "visited_at": job.visited_at}


class UnblockRequest(BaseModel):
    url: str


CLEARABLE_STATUSES = ["discovered", "scored", "tailored", "ignored", "rejected"]


@app.delete("/api/jobs/clear")
def clear_discovered_jobs(
    include_applied: bool = Query(False),
    db: Session = Depends(get_db),
):
    """
    Empty the working queue.

    Two things this deliberately does NOT do any more:

    * It no longer marks what it removes as "dismissed". Dismissed is a
      blocking status, so tidying the dashboard was quietly blacklisting every
      job on it — after a few rounds a scan finding hundreds of postings could
      insert twenty, the rest being permanently blocked. Cleared jobs are now
      recorded as "cleared", which is observable but not blocking, so a live
      posting can be surfaced again by the next scan.
    * It does not touch a job that is mid-process (applying, interviewing, an
      offer) — only finished or never-started ones.

    `include_applied` also removes rows already applied to. The application
    itself stays recorded in the seen ledger, which is what stops the job
    being offered again, but the `jobs` row and its Application detail go.

    Only the region selected on the dashboard. Clearing India leaves the
    UK / Ireland / Germany / Netherlands rows, and the reverse.
    """
    statuses = list(CLEARABLE_STATUSES)
    if include_applied:
        statuses.append("applied")

    jobs_to_delete = [
        job for job in db.query(Job).filter(Job.status.in_(statuses)).all()
        if job_in_region(job, settings.active_region)
    ]

    # Only a row carrying no decision becomes "cleared". Applied, rejected,
    # ignored (dismissed) and opened (visited) rows keep that decision in the
    # ledger — writing "cleared" over them put jobs the user had already
    # dismissed, opened or been rejected from back into the next scan. `mark`
    # also refuses to downgrade whatever the ledger already holds.
    applied_count = sum(
        1 for job in jobs_to_delete
        if job.status == "applied" or job.apply_status == "applied"
    )
    seen_ledger.mark_removed_jobs(db, jobs_to_delete, "cleared", "cleared from the dashboard")

    deleted_count = 0
    for job in jobs_to_delete:
        if job.application:
            db.delete(job.application)
        db.delete(job)
        deleted_count += 1
    db.commit()
    return {
        "success": True,
        "deleted_count": deleted_count,
        "applied_removed": applied_count,
    }


@app.post("/api/jobs/purge-visited")
def purge_visited(db: Session = Depends(get_db)):
    """
    Drop every job already opened, so the queue only holds what is still to do.

    Applied / interviewing / offer rows are left alone by
    `purge_visited_jobs` — opening a posting you then applied to must not
    delete the record of the application.
    """
    from backend.pipeline import purge_visited_jobs

    removed = purge_visited_jobs(db)
    return {"success": True, "removed_count": removed}


@app.post("/api/jobs/purge-stale")
def purge_stale_jobs(
    max_age_hours: int = Query(JOB_FRESHNESS_HOURS, ge=1),
    db: Session = Depends(get_db),
):
    """Delete discovered/scored jobs older than max_age_hours."""
    from backend.pipeline import purge_stale_discovered_jobs

    purged_count = purge_stale_discovered_jobs(db, max_age_hours=max_age_hours)
    return {"success": True, "purged_count": purged_count}


@app.post("/api/jobs/purge-experienced")
def purge_experienced_jobs(db: Session = Depends(get_db)):
    """Purge this region's jobs that fail experience, frontend, or location filters."""
    from backend.scrapers.filter_utils import is_career_listing_eligible, passes_hard_filters

    all_jobs = db.query(Job).all()
    purged_count = 0
    for job in all_jobs:
        if job.status in {"applied", "interviewing", "offer"}:
            continue
        if not job_in_region(job, settings.active_region):
            continue
        job_dict = {
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "experience_required": job.experience_required,
            "jd_text": job.jd_text,
            "platform": job.platform,
        }
        is_valid, _ = passes_hard_filters(
            job_dict, max_years=settings.experience_years, region=settings.active_region,
        )
        if is_valid and job.platform == "company_careers":
            is_valid, _ = is_career_listing_eligible(job_dict)
        if not is_valid:
            # Rejected / ignored / opened rows keep that decision; only an
            # undecided row is recorded as merely "filtered".
            seen_ledger.mark_removed_jobs(db, [job], "filtered", "failed hard filters on purge")
            if job.application:
                db.delete(job.application)
            db.delete(job)
            purged_count += 1
    db.commit()
    return {"success": True, "purged_count": purged_count}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    seen_ledger.mark(
        db,
        url=job.url,
        status="dismissed",
        reason="deleted by user",
        title=job.title or "",
        company=job.company or "",
        platform=job.platform or "",
    )
    if job.application:
        db.delete(job.application)
    db.delete(job)
    db.commit()
    return {"success": True}


# --- Seen ledger -------------------------------------------------------------


@app.get("/api/seen/stats")
def seen_stats(db: Session = Depends(get_db)):
    """Counts by ledger status - how many jobs future scans will skip, and why."""
    return seen_ledger.stats(db)


@app.post("/api/seen/unblock")
def seen_unblock(payload: UnblockRequest, db: Session = Depends(get_db)):
    """Let a job be rediscovered after it was visited, dismissed, or expired."""
    if not seen_ledger.unblock(db, payload.url):
        raise HTTPException(status_code=404, detail="URL not in the seen ledger")
    return {"success": True, "url": payload.url}


# ─── Scan Pipeline ───────────────────────────────────────────────────────────

_scan_status = {"running": False, "last_result": None, "started_at": None}

# A scan that has been "running" for longer than this is wedged, not slow. No
# scraper has a hard timeout of its own, so without this a single hung request
# leaves `running` stuck True for the life of the process — and every later
# Scan click is refused with a 409 the user never sees.
SCAN_STALL_SECONDS = 30 * 60


def _scan_runtime() -> float:
    started = _scan_status.get("started_at")
    if not started:
        return 0.0
    return (datetime.utcnow() - started).total_seconds()


def _scan_is_stalled() -> bool:
    return bool(_scan_status["running"]) and _scan_runtime() > SCAN_STALL_SECONDS


def _autopilot_running() -> bool:
    from backend.api.autopilot import _status as autopilot_status

    return bool(autopilot_status.get("running"))


@app.post("/api/scan")
async def start_scan(req: ScanRequest, background_tasks: BackgroundTasks):
    """Trigger a background job scan across platforms."""
    if _scan_status["running"] and not _scan_is_stalled():
        raise HTTPException(
            status_code=409,
            detail=f"A scan has been running for {int(_scan_runtime())}s. "
                   f"Stop it first, or wait for it to finish.",
        )
    # A scan and a fill share one persistent Chrome profile; the second to
    # start cannot open it, and the scan then reports "0 roles" silently.
    if _apply_status["running"] or _autopilot_running():
        raise HTTPException(status_code=409, detail="An apply run is in progress — wait for it to finish")
    if _scan_is_stalled():
        logger.warning(
            f"Previous scan wedged after {int(_scan_runtime())}s — starting a new one"
        )

    # Claimed here, not inside the task. FastAPI runs background tasks *after*
    # the response is sent, so setting it in there leaves a window where the
    # POST has answered "started" but /api/scan/status still says False — the
    # dashboard polls, sees False, and drops straight out of its scanning
    # state. It also made the 409 guard above racy against a double click.
    _scan_status["running"] = True
    _scan_status["last_result"] = None
    _scan_status["started_at"] = datetime.utcnow()

    async def do_scan():
        from backend.scan_control import clear_stop

        clear_stop()
        try:
            from backend.pipeline import run_scan_pipeline
            result = await run_scan_pipeline(
                platforms=req.platforms,
                max_jobs_per_platform=req.max_jobs_per_platform,
                headless=req.headless,
            )
            _scan_status["last_result"] = result
        except Exception as e:
            logger.error(f"Scan error: {e}")
            _scan_status["last_result"] = {"error": str(e)}
        finally:
            _scan_status["running"] = False
            _scan_status["started_at"] = None

    background_tasks.add_task(do_scan)
    return {"message": "Scan started", "running": True}


@app.get("/api/scan/status")
def get_scan_status():
    return {
        **_scan_status,
        "elapsed_seconds": int(_scan_runtime()),
        "stalled": _scan_is_stalled(),
    }


@app.post("/api/scan/stop")
def stop_scan(force: bool = Query(False)):
    """
    Ask the scan to stop.

    The flag is cooperative — a scraper blocked on a request that never returns
    will not see it. `force` is the way out of that: it releases the status so
    a new scan can be started, which is the only thing the user actually needs
    when the old one is wedged.
    """
    from backend.scan_control import request_stop

    if not _scan_status["running"]:
        return {"message": "Scan is not running", "running": False}

    request_stop()
    if force or _scan_is_stalled():
        elapsed = int(_scan_runtime())
        _scan_status["running"] = False
        _scan_status["started_at"] = None
        _scan_status["last_result"] = {
            "error": f"Scan abandoned after {elapsed}s — it had stopped responding."
        }
        logger.warning(f"Scan force-released after {elapsed}s")
        return {"message": "Scan abandoned", "running": False, "forced": True}

    return {"message": "Stop requested", "running": True}


# ─── Auto-apply ──────────────────────────────────────────────────────────────

_apply_status = {"running": False, "last_result": None, "current_job": None}


@app.get("/api/profile")
def get_profile():
    profile = load_profile()
    data = profile.to_dict()
    data["missing"] = profile.missing_required()
    data["has_resume"] = profile.resume_file() is not None
    data["ready_to_apply"] = not data["missing"]
    return data


@app.put("/api/profile")
def update_profile(payload: dict):
    try:
        profile = save_profile(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    data = profile.to_dict()
    data["missing"] = profile.missing_required()
    data["has_resume"] = profile.resume_file() is not None
    data["ready_to_apply"] = not data["missing"]
    return data


@app.post("/api/profile/resume")
async def upload_resume_file(file: UploadFile = File(...)):
    suffix = Path(file.filename or "resume.pdf").suffix.lower()
    if suffix not in {".pdf", ".doc", ".docx"}:
        raise HTTPException(status_code=400, detail="Resume must be PDF, DOC, or DOCX")
    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    dest = RESUMES_DIR / f"resume{suffix}"
    dest.write_bytes(await file.read())
    rel = f"resumes/resume{suffix}"
    save_profile({"resume_path": rel})
    return {"success": True, "resume_path": rel}


@app.get("/api/apply/status")
def get_apply_status():
    return _apply_status


@app.post("/api/apply/stop")
async def stop_apply():
    from backend.applier.control import request_stop
    from backend.applier.engine import close_apply_browser

    if not _apply_status["running"]:
        return {"message": "Apply is not running", "running": False}
    request_stop()
    await close_apply_browser()
    return {"message": "Stop requested", "running": True}


@app.post("/api/apply")
async def start_apply(
    req: ApplyRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    if _apply_status["running"]:
        raise HTTPException(status_code=409, detail="Apply already running")
    if _scan_status["running"]:
        raise HTTPException(status_code=409, detail="Scan is running — wait until it finishes")
    if _autopilot_running():
        raise HTTPException(status_code=409, detail="Autopilot is running — wait until it finishes")

    profile = load_profile()
    missing = [m for m in profile.missing_required() if m != "resume"]
    if missing:
        raise HTTPException(status_code=400, detail="Profile incomplete: " + ", ".join(missing))

    # Which jobs, decided here. The dashboard now sends the ids it counted in
    # its confirm dialog. With no ids, the run used to take every non-skipped
    # row in the database — stale ones GET /api/jobs hides included — so
    # "apply to 12 jobs?" could start 40. The same freshness rule applies now.
    # An explicit empty list is refused: the engine reads [] as "everything".
    job_ids = req.job_ids
    if job_ids is not None and not job_ids:
        raise HTTPException(status_code=400, detail="No jobs selected")
    if job_ids is None:
        from backend.applier.engine import eligible_jobs

        cutoff = datetime.utcnow() - timedelta(hours=JOB_FRESHNESS_HOURS)
        job_ids = [
            j.id for j in eligible_jobs(db)
            if job_in_region(j, settings.active_region)
            and (
                j.status not in ("discovered", "scored")
                or (j.discovered_at is not None and j.discovered_at >= cutoff)
            )
        ]
        if not job_ids:
            raise HTTPException(status_code=400, detail="No eligible jobs to apply to")

    # Claimed before the task is queued, for the same reason as the scan: a
    # background task starts after the response, so the dashboard's first poll
    # would otherwise see running=False and give up on the run.
    _apply_status["running"] = True
    _apply_status["current_job"] = None

    async def do_apply():
        try:
            from backend.applier.engine import run_apply_pipeline
            result = await run_apply_pipeline(
                job_ids=job_ids,
                auto_submit=req.auto_submit,
                headless=req.headless,
            )
            _apply_status["last_result"] = result
        except Exception as e:
            logger.error(f"Apply error: {e}")
            _apply_status["last_result"] = {"error": str(e)}
        finally:
            _apply_status["running"] = False
            _apply_status["current_job"] = None

    background_tasks.add_task(do_apply)
    return {"message": "Apply started", "running": True}


@app.post("/api/jobs/{job_id}/apply")
async def apply_single_job(job_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    req = ApplyRequest(job_ids=[job_id], auto_submit=False, headless=False)
    return await start_apply(req, background_tasks)


# ─── Analytics ───────────────────────────────────────────────────────────────

@app.get("/api/analytics")
def get_analytics(db: Session = Depends(get_db)):
    """Return aggregated application stats."""
    status_counts = dict(
        db.query(Job.status, func.count(Job.id)).group_by(Job.status).all()
    )
    by_status = {
        status: status_counts.get(status, 0)
        for status in (
            "discovered", "scored", "tailored", "applied",
            "interviewing", "rejected", "offer",
        )
    }
    total = sum(status_counts.values())

    platform_counts = dict(
        db.query(Job.platform, func.count(Job.id)).group_by(Job.platform).all()
    )
    by_platform = {platform: platform_counts.get(platform, 0) for platform in ALL_PLATFORMS}

    top_companies = (
        db.query(Job.company, Job.relevance_score)
        .order_by(Job.relevance_score.desc())
        .limit(10)
        .all()
    )

    return {
        "total_jobs": total,
        "by_status": by_status,
        "by_platform": by_platform,
        "top_companies": [{"company": c, "score": s} for c, s in top_companies],
    }


# ─── Settings ────────────────────────────────────────────────────────────────

def _target_region(new_settings: dict) -> str:
    """A save that omits the field keeps the current region."""
    raw = new_settings.get("target_region")
    if raw in (None, ""):
        return settings.active_region
    region = str(raw).strip().lower()
    if region not in {"india", "europe"}:
        raise HTTPException(status_code=400, detail="target_region must be india or europe")
    return region


@app.get("/api/settings")
def get_settings():
    return {
        "target_roles": settings.target_roles_list,
        "experience_years": settings.experience_years,
        "preferred_locations": settings.preferred_locations_list,
        "target_region": settings.active_region,
        "min_relevance_score": settings.min_relevance_score,
        "user_full_name": settings.user_full_name,
        "excluded_companies": settings.excluded_companies_list,
        "has_gemini_key": bool(settings.gemini_api_key),
        "has_linkedin": bool(settings.linkedin_email),
        "has_naukri": bool(settings.naukri_email),
        "has_indeed": bool(settings.indeed_email),
        "has_instahyre": bool(settings.instahyre_email),
        "supported_platforms": SCAN_PLATFORMS,
    }


@app.post("/api/settings")
def update_settings(new_settings: dict):
    """Update settings (writes to .env file)."""
    env_path = _env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)

    existing_lines = []
    if env_path.exists():
        existing_lines = env_path.read_text(encoding="utf-8").splitlines()

    updates = {
        "TARGET_ROLES": ",".join(new_settings.get("target_roles", settings.target_roles_list)),
        "EXPERIENCE_YEARS": str(new_settings.get("experience_years", settings.experience_years)),
        "PREFERRED_LOCATIONS": ",".join(new_settings.get("preferred_locations", settings.preferred_locations_list)),
        "TARGET_REGION": _target_region(new_settings),
        "MIN_RELEVANCE_SCORE": str(new_settings.get("min_relevance_score", settings.min_relevance_score)),
        "USER_FULL_NAME": new_settings.get("user_full_name", settings.user_full_name),
        "EXCLUDED_COMPANIES": ",".join(
            new_settings.get("excluded_companies", settings.excluded_companies_list)
        ),
        "GEMINI_API_KEY": new_settings.get("gemini_api_key", settings.gemini_api_key),
        "LINKEDIN_EMAIL": new_settings.get("linkedin_email", settings.linkedin_email),
        "LINKEDIN_PASSWORD": new_settings.get("linkedin_password", settings.linkedin_password),
        "NAUKRI_EMAIL": new_settings.get("naukri_email", settings.naukri_email),
        "NAUKRI_PASSWORD": new_settings.get("naukri_password", settings.naukri_password),
        "INDEED_EMAIL": new_settings.get("indeed_email", settings.indeed_email),
        "INDEED_PASSWORD": new_settings.get("indeed_password", settings.indeed_password),
        "INSTAHYRE_EMAIL": new_settings.get("instahyre_email", settings.instahyre_email),
        "INSTAHYRE_PASSWORD": new_settings.get("instahyre_password", settings.instahyre_password),
    }

    # A value is written as one `KEY=value` line. A newline inside one would
    # start a line of the caller's choosing — "x\nOPENAI_BASE_URL=https://evil"
    # sent every later tailoring call, API key included, to that host.
    for key, value in updates.items():
        if any(ch in str(value) for ch in "\r\n\x00"):
            raise HTTPException(status_code=400, detail=f"{key} may not contain a line break")
    updates = {k: str(v) for k, v in updates.items()}

    # Rewrite in place: keep comments, blank lines and the order of the file,
    # replace only the keys being set, append the ones that are new.
    out, seen = [], set()
    for line in existing_lines:
        key = line.partition("=")[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    out.extend(f"{k}={v}" for k, v in updates.items() if k not in seen)
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")

    # The settings object is built once at import. Without this, a change here
    # did nothing until the server was restarted, while GET /api/settings kept
    # reporting the old values.
    for key, value in updates.items():
        attr = key.lower()
        if hasattr(settings, attr):
            current = getattr(settings, attr)
            try:
                setattr(settings, attr, type(current)(value) if not isinstance(current, str) else value)
            except (TypeError, ValueError):
                pass

    return {"success": True}
