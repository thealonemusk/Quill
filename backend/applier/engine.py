"""Apply orchestrator: one browser, sequential jobs, DB status updates."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Dict, List, Optional

from loguru import logger
from playwright.async_api import async_playwright

from sqlalchemy import or_

from backend.applier.adapters import LOG_DIR, apply_on_page
from backend.applier.ats import detect_ats
from backend.applier.routing import Routing, needs_linkedin, route_job
from backend.applier.control import clear_stop, should_stop
from backend.applier.fields import APPLY_REGION, JOB_LOCATION
from backend.config import settings
from backend.applier.filler import screenshot_failure
from backend.applier.linkedin_apply import ensure_linkedin_session
from backend.applier.profile import ApplicantProfile, ensure_resume_pdf, load_profile
from backend.db.models import Application, Job, SessionLocal
from backend.resume import sends
from backend.resume.tailor import tailored_upload
from backend import seen_ledger
from backend.scrapers.base import create_browser_context

SKIP_STATUSES = {"applied", "interviewing", "rejected", "offer", "ignored"}
MANUAL_APPLY_STATUSES = {"failed", "needs_review", "skipped"}
DELAY_BETWEEN_JOBS_SEC = 8
_active_context = None


async def _reuse_page(context):
    pages = [p for p in context.pages if not p.is_closed()]
    return pages[0] if pages else await context.new_page()


async def _close_extra_pages(context, keep) -> None:
    for extra in list(context.pages):
        if extra != keep:
            try:
                await extra.close()
            except Exception:
                pass


async def close_apply_browser() -> None:
    ctx = _active_context
    if ctx is None:
        return
    try:
        await ctx.close()
    except Exception:
        pass


def _skip_unfinished(db, jobs: List[Job], summary: Dict) -> None:
    for job in jobs:
        fresh = db.query(Job).filter(Job.id == job.id).first()
        if not fresh or fresh.apply_status not in {"queued", "applying"}:
            continue
        fresh.apply_status = "skipped"
        fresh.apply_error = "Stopped by user"
        summary["skipped"] = summary.get("skipped", 0) + 1
        summary["results"].append({
            "job_id": fresh.id,
            "title": fresh.title,
            "company": fresh.company,
            "status": "skipped",
            "message": "Stopped by user",
            "ats": fresh.ats_type or "",
        })
    db.commit()
    summary["message"] = "Stopped by user"


def eligible_jobs(db, job_ids: Optional[List[str]] = None) -> List[Job]:
    q = db.query(Job)
    if job_ids:
        q = q.filter(Job.id.in_(job_ids))
    else:
        q = q.filter(~Job.status.in_(SKIP_STATUSES))
        q = q.filter(
            or_(
                Job.apply_status.is_(None),
                Job.apply_status == "",
                ~Job.apply_status.in_({"applied"} | MANUAL_APPLY_STATUSES),
            )
        )
    jobs = q.order_by(Job.relevance_score.desc()).all()
    if job_ids:
        jobs = [j for j in jobs if j.status not in SKIP_STATUSES]
    return jobs


def _record_result(db, job: Job, result: Dict) -> None:
    status = result.get("status") or "failed"
    message = result.get("message") or ""
    job.ats_type = result.get("ats") or job.ats_type or detect_ats(job.url)
    job.apply_status = status
    job.apply_error = message
    if status == "applied":
        job.status = "applied"
        job.applied_at = datetime.utcnow()
        seen_ledger.mark(
            db,
            url=job.url,
            status="applied",
            reason=message[:200],
            title=job.title or "",
            company=job.company or "",
            platform=job.platform or "",
        )
        if not job.application:
            db.add(Application(job_id=job.id, notes=message))
        else:
            job.application.notes = message
    db.commit()


async def apply_one_job(
    page,
    job: Job,
    profile: ApplicantProfile,
    resume,
    auto_submit: bool,
    apply_url: str = "",
) -> Dict:
    """
    Apply to one job.

    `apply_url` is the form the router resolved, which is not always `job.url`:
    a LinkedIn listing that names a Greenhouse link in its description is
    applied to at that link, not on LinkedIn.
    """
    target = apply_url or job.url
    # Read by the resolver for questions that name no country ("Will you
    # require sponsorship?"): they mean the job's country.
    token = JOB_LOCATION.set(job.location or "")
    region_token = APPLY_REGION.set(settings.active_region)
    try:
        return await apply_on_page(
            page=page,
            url=target,
            profile=profile,
            resume=resume,
            job_title=f"{job.title} ({job.location})" if job.location else job.title,
            company=job.company,
            auto_submit=auto_submit,
            job_id=job.id,
            jd_text=job.jd_text or "",
        )
    except Exception as exc:
        if should_stop():
            return {"status": "skipped", "message": "Stopped by user", "ats": detect_ats(job.url)}
        logger.exception(f"Apply failed for {job.title} @ {job.company}")
        await screenshot_failure(page, LOG_DIR / f"{job.id}.png")
        return {"status": "failed", "message": str(exc), "ats": detect_ats(job.url)}
    finally:
        JOB_LOCATION.reset(token)
        APPLY_REGION.reset(region_token)


async def run_apply_pipeline(
    job_ids: Optional[List[str]] = None,
    auto_submit: bool = False,
    headless: bool = False,
) -> Dict:
    profile = load_profile()
    missing = profile.missing_required()
    # Resume can be generated after the browser starts
    missing = [m for m in missing if m != "resume"]
    if missing:
        return {
            "error": "Profile incomplete: " + ", ".join(missing),
            "applied": 0,
            "failed": 0,
            "needs_review": 0,
            "skipped": 0,
        }

    db = SessionLocal()
    summary = {
        "applied": 0,
        "failed": 0,
        "needs_review": 0,
        "skipped": 0,
        "results": [],
    }
    global _active_context
    clear_stop()
    try:
        jobs = eligible_jobs(db, job_ids)
        if not jobs:
            summary["message"] = "No eligible jobs to apply to"
            return summary

        # Decide per job who applies — the bot or the human — before opening a
        # browser. Previously every job was assumed to be a LinkedIn job, so a
        # batch of Greenhouse forms stalled on a LinkedIn login it never needed.
        plan: Dict[str, Routing] = {}
        auto_jobs: List[Job] = []
        for job in jobs:
            routing = route_job(job)
            plan[job.id] = routing
            job.apply_error = ""
            if not job.ats_type:
                job.ats_type = routing.ats or detect_ats(job.url)
            if routing.is_auto:
                job.apply_status = "queued"
                auto_jobs.append(job)
            else:
                # Not a failure — work for the review queue, applied through the
                # extension. Recorded so the dashboard can list it.
                job.apply_status = "manual_queue"
                job.apply_error = routing.reason
                summary["manual_queue"] = summary.get("manual_queue", 0) + 1
                summary["results"].append({
                    "job_id": job.id,
                    "title": job.title,
                    "company": job.company,
                    "status": "manual_queue",
                    "message": routing.reason,
                    "ats": routing.ats,
                    "apply_url": routing.apply_url,
                })
        db.commit()

        if not auto_jobs:
            summary["message"] = (
                f"No job in this batch can be applied to automatically. "
                f"{summary.get('manual_queue', 0)} queued for manual apply."
            )
            return summary

        jobs = auto_jobs

        async with async_playwright() as playwright:
            browser, context = await create_browser_context(playwright, headless=headless)
            _active_context = context
            page = await _reuse_page(context)
            try:
                if should_stop():
                    _skip_unfinished(db, jobs, summary)
                    return summary
                resume = await ensure_resume_pdf(page, profile)
                if not resume:
                    return {**summary, "error": "Could not create or find a resume PDF"}
                if should_stop():
                    _skip_unfinished(db, jobs, summary)
                    return summary

                # Only sign in to LinkedIn if something in this batch is
                # actually hosted there. A Greenhouse-only run must never be
                # blocked by an unrelated login.
                if needs_linkedin([plan[j.id] for j in jobs]):
                    if not await ensure_linkedin_session(page):
                        return {
                            **summary,
                            "error": "LinkedIn not signed in. Use the TempoApply Chrome window, then retry.",
                        }

                for i, job in enumerate(jobs):
                    if should_stop():
                        _skip_unfinished(db, jobs, summary)
                        return summary
                    fresh = db.query(Job).filter(Job.id == job.id).first()
                    if not fresh:
                        continue
                    fresh.apply_status = "applying"
                    db.commit()

                    # The job's tailored PDF when one exists, else the default.
                    job_resume = tailored_upload(fresh.id, resume.name) or resume
                    logger.info(
                        f"Resume for {fresh.company}: "
                        f"{'tailored ' if job_resume != resume else 'default '}{job_resume}"
                    )
                    sends.record(db, fresh.id, job_resume, channel="headless",
                                 tailored=job_resume != resume)

                    page = await _reuse_page(context)
                    result = await apply_one_job(
                        page, fresh, profile, job_resume, auto_submit,
                        apply_url=plan[fresh.id].apply_url,
                    )
                    await _close_extra_pages(context, page)

                    _record_result(db, fresh, result)
                    bucket = result.get("status") or "failed"
                    if bucket not in summary:
                        summary[bucket] = 0
                    summary[bucket] = summary.get(bucket, 0) + 1
                    summary["results"].append({
                        "job_id": fresh.id,
                        "title": fresh.title,
                        "company": fresh.company,
                        "status": bucket,
                        "message": result.get("message", ""),
                        "ats": result.get("ats", ""),
                    })
                    logger.info(
                        f"Apply {i + 1}/{len(jobs)} {fresh.title} @ {fresh.company}: {bucket}"
                    )
                    if should_stop():
                        _skip_unfinished(db, jobs, summary)
                        return summary
                    if i < len(jobs) - 1:
                        for _ in range(DELAY_BETWEEN_JOBS_SEC):
                            if should_stop():
                                _skip_unfinished(db, jobs, summary)
                                return summary
                            await asyncio.sleep(1)
            finally:
                _active_context = None
                try:
                    await context.close()
                except Exception:
                    pass
                if browser:
                    try:
                        await browser.close()
                    except Exception:
                        pass
    finally:
        db.close()

    return summary
