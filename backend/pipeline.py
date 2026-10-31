"""
Job Pipeline Orchestrator — multi-platform job discovery and experience validation.
"""
import asyncio
import json
import re
import urllib.parse
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy.orm import Session

from backend.applier.ats import detect_ats
from backend.db.models import Job, SessionLocal
from backend.scrapers.filter_utils import (
    EUROPE_PREFERRED_LOCATIONS,
    is_career_listing_eligible,
    job_in_region,
    passes_hard_filters,
)
from backend.scrapers import http
from backend.scrapers.scoring import score_job
from backend.scrapers.registry import SCRAPER_REGISTRY, SCRAPER_LABELS, resolve_discovery_roles
from backend.config import settings
from backend.platforms import DEFAULT_SCAN_PLATFORMS
from backend.job_freshness import JOB_FRESHNESS_HOURS
from backend.scan_control import clear_stop, should_stop
from backend import seen_ledger


_WS_RE = re.compile(r"\s+")


def _dup_key(title: str, company: str, location: str) -> str:
    """Title + company + location, for spotting one posting under two URLs."""
    parts = [_WS_RE.sub(" ", (v or "").strip().lower()) for v in (title, company, location)]
    if not parts[0] or not parts[1]:
        return ""
    return "|".join(parts)


def _host(url: str) -> str:
    try:
        host = urllib.parse.urlsplit(url or "").netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _is_cross_post(seen_hosts, host: str) -> bool:
    """Is this the same posting already taken from a *different* site?

    Dedup is by URL first. Title + company used to be a second key, and it
    dropped distinct requisitions: Amazon lists dozens of "Software
    Development Engineer" roles, one per team and city, and all but the first
    vanished. Now location is part of the key, and two URLs on the same host
    are two requisitions (that host's own job ids differ) — only a match from
    another host, e.g. LinkedIn mirroring a careers page, is a duplicate.
    """
    return bool(seen_hosts) and any(h != host for h in seen_hosts)


def _known_jobs(db: Session):
    """URLs already stored, and the hosts each title+company+location came from.

    One query for the table. `upsert_jobs` used to ask again for every
    candidate that survived the filters.
    """
    urls = set()
    dups: dict = {}
    rows = db.query(Job.url, Job.title, Job.company, Job.location).all()
    for url, title, company, location in rows:
        urls.add(url)
        dkey = _dup_key(title, company, location)
        if dkey:
            dups.setdefault(dkey, set()).add(_host(url))
    return urls, dups


def upsert_jobs(jobs: list, db: Session) -> int:
    """Insert new jobs, skipping anything the seen ledger says we already handled.

    Returns the count of new jobs. Every rejected job is recorded in the ledger
    with its reason, so a scan that drops 300 postings can still say why.
    """
    count = 0
    # One query for the whole batch instead of a SELECT per candidate.
    blocked_urls, _ = seen_ledger.load_blocklist(db)
    known_urls, known_dups = _known_jobs(db)
    ledger = seen_ledger.load_rows(
        db, (seen_ledger.url_key(job.get("url", "")) for job in jobs)
    )
    batch_urls = set()
    # dup key -> hosts it was accepted from (see `_is_cross_post`).
    batch_dups: dict = {}

    max_exp_years = getattr(settings, "experience_years", 2)
    min_score = getattr(settings, "min_relevance_score", 55)

    excluded = [c.lower() for c in settings.excluded_companies_list if c.strip()]

    for job_data in jobs:
        url = job_data.get("url", "")
        title = job_data.get("title", "")
        company = job_data.get("company", "")

        if not url:
            continue

        ukey = seen_ledger.url_key(url)
        dkey = _dup_key(title, company, job_data.get("location", ""))
        host = _host(url)

        if ukey in batch_urls or _is_cross_post(batch_dups.get(dkey), host):
            continue

        # The ledger remembers decisions the user already made, even for jobs
        # whose queue row was purged. Without this the next scan re-adds them.
        if ukey in blocked_urls:
            logger.debug(f"Seen ledger blocked [{company} - {title}]")
            continue

        if excluded and company:
            company_lower = company.lower()
            if any(exc in company_lower for exc in excluded):
                logger.info(f"Skipping excluded company: {company}")
                seen_ledger.record(db, job_data, "filtered", f"Excluded company: {company}", cache=ledger)
                continue

        region = settings.active_region
        ok, reason = passes_hard_filters(job_data, max_years=max_exp_years, region=region)
        if not ok:
            logger.info(f"Hard filter excluded [{company} - {title}]: {reason}")
            seen_ledger.record(db, job_data, "filtered", reason, cache=ledger)
            continue

        if job_data.get("platform") == "company_careers":
            eligible, reason = is_career_listing_eligible(job_data)
            if not eligible:
                logger.info(f"Career listing excluded [{company} - {title}]: {reason}")
                seen_ledger.record(db, job_data, "filtered", reason, cache=ledger)
                continue

        preferred = (
            EUROPE_PREFERRED_LOCATIONS if region == "europe"
            else settings.preferred_locations_list
        )
        score, fit_reason = score_job(
            job_data,
            target_roles=settings.target_roles_list,
            preferred_locations=preferred,
            experience_years=max_exp_years,
        )
        if score < min_score:
            logger.info(f"Below min score ({score} < {min_score}): [{company} - {title}]")
            seen_ledger.record(
                db, job_data, "filtered", f"Below min score: {score} < {min_score}",
                cache=ledger,
            )
            continue

        job_data["score"] = score
        job_data["fit_reason"] = fit_reason

        # Added only once a job passes, so a richer duplicate later in the
        # batch can still win over a thin one seen first.
        batch_urls.add(ukey)
        if dkey:
            batch_dups.setdefault(dkey, set()).add(host)

        if url in known_urls:
            continue

        # Equality on the normalized key, not ilike: ilike read "_" and "%"
        # in a title as wildcards. Hosts were loaded with the same key.
        if dkey and _is_cross_post(known_dups.get(dkey), host):
            continue

        missing_skills = json.dumps(job_data.get("missing_skills", []))
        job = Job(
            title=job_data.get("title", ""),
            company=job_data.get("company", ""),
            platform=job_data.get("platform", ""),
            url=url,
            location=job_data.get("location", ""),
            experience_required=job_data.get("experience_required", ""),
            salary_range=job_data.get("salary_range", ""),
            jd_text=job_data.get("jd_text", ""),
            relevance_score=job_data.get("score", 0.0),
            fit_reason=job_data.get("fit_reason", ""),
            missing_skills=missing_skills,
            seniority_level=job_data.get("seniority_level", ""),
            is_engineering_role=job_data.get("is_engineering_role", True),
            easy_apply=job_data.get("easy_apply", False),
            recruiter_name=job_data.get("recruiter_name", ""),
            recruiter_profile=job_data.get("recruiter_profile", ""),
            ats_type=job_data.get("ats_type") or detect_ats(url),
            status="discovered",
        )
        db.add(job)
        seen_ledger.record(db, job_data, "discovered", fit_reason, cache=ledger)
        count += 1
    db.commit()
    return count


def purge_visited_jobs(db: Session) -> int:
    """Remove jobs the user opened that are still in this region's queue."""
    visited = [
        job for job in db.query(Job).filter(
            Job.visited_at.isnot(None),
            Job.status.in_(["discovered", "scored", "tailored", "ignored"]),
        ).all()
        if job_in_region(job, settings.active_region)
    ]
    # Mark before deleting: after db.delete the row's URL is no longer readable,
    # and an unrecorded purge is exactly how these jobs came back.
    seen_ledger.mark_removed_jobs(db, visited, "visited", "opened by user")
    removed = 0
    for job in visited:
        if job.application:
            db.delete(job.application)
        db.delete(job)
        removed += 1
    if removed:
        db.commit()
    return removed


def purge_stale_discovered_jobs(db: Session, max_age_hours: int = JOB_FRESHNESS_HOURS) -> int:
    """Remove this region's discovered/scored jobs older than max_age_hours."""
    cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
    stale = [
        job for job in db.query(Job).filter(
            Job.status.in_(["discovered", "scored"]),
            Job.discovered_at < cutoff,
        ).all()
        if job_in_region(job, settings.active_region)
    ]
    # A stale row the user opened stays "visited": expiry is not a decision.
    seen_ledger.mark_removed_jobs(
        db, stale, "expired", f"aged out of the queue after {max_age_hours}h"
    )
    removed = 0
    for job in stale:
        if job.application:
            db.delete(job.application)
        db.delete(job)
        removed += 1
    if removed:
        db.commit()
    return removed


async def run_scan_pipeline(
    platforms: list = None,
    max_jobs_per_platform: int = 400,
    headless: bool = True,
) -> dict:
    """
    Run job discovery: scrape selected platforms, apply filters, store in DB.
    """
    platforms = platforms or list(DEFAULT_SCAN_PLATFORMS)
    # The dashboard posts the India board list. Europe ignores that and
    # searches only the sources that can name a UK / IE / DE / NL city.
    if settings.active_region == "europe":
        # Amazon has a JSON API, so it can run beside LinkedIn. Microsoft and
        # Google need a browser on the same Chrome profile; starting both
        # closes the window ("Opening in existing browser session"), and
        # jobs.careers.microsoft.com does not resolve from this machine.
        # Those two employers are in the LinkedIn sponsor search instead.
        platforms = ["linkedin", "amazon"]
        logger.info("Europe region: scanning LinkedIn and Amazon")
    roles = resolve_discovery_roles(settings.target_roles_list)
    logger.info(f"Targeting roles (discovery): {roles}")
    clear_stop()
    # Per-scan, so a host throttled an hour ago is tried again now.
    http.reset_rate_limit_state()

    all_raw_jobs = []
    tasks = []

    for key in platforms:
        scrape_fn = SCRAPER_REGISTRY.get(key)
        if not scrape_fn:
            logger.warning(f"Unknown platform '{key}' — skipping")
            continue

        label = SCRAPER_LABELS.get(key, key)

        async def run_one(fn=scrape_fn, name=key, display=label):
            if should_stop():
                logger.info(f"Scan stop requested — skipping {display}")
                return name, []
            logger.info(f"Scraping {display}...")
            try:
                jobs = await fn(roles=roles, max_jobs=max_jobs_per_platform, headless=headless)
            except Exception as exc:
                logger.error(f"{name} scraping error: {exc}")
                return name, []
            return name, jobs or []

        tasks.append(run_one())

    db = SessionLocal()
    stale_removed = 0
    visited_removed = 0
    new_count = 0
    try:
        visited_removed = purge_visited_jobs(db)
        if visited_removed:
            logger.info(f"Removed {visited_removed} previously visited jobs")

        if tasks:
            for finished in asyncio.as_completed(tasks):
                try:
                    name, result = await finished
                except Exception as exc:
                    logger.error(f"Platform scraping error: {exc}")
                    continue
                if not result:
                    logger.info(f"{name}: 0 jobs")
                    continue
                all_raw_jobs.extend(result)
                added = upsert_jobs(result, db)
                new_count += added
                logger.info(f"{name}: {len(result)} jobs ({added} new in db)")

        logger.info(f"Total raw jobs found: {len(all_raw_jobs)}")

        stale_removed = purge_stale_discovered_jobs(db, max_age_hours=JOB_FRESHNESS_HOURS)
        if stale_removed:
            logger.info(f"Purged {stale_removed} stale discovered jobs (>{JOB_FRESHNESS_HOURS}h)")
    finally:
        db.close()

    throttled = http.rate_limited_hosts()
    result = {
        "total_scraped": len(all_raw_jobs),
        "qualified": len(all_raw_jobs),
        "new_in_db": new_count,
        "visited_removed": visited_removed,
        "stale_removed": stale_removed,
        "platforms": platforms,
        "rate_limited": throttled,
    }
    # A scan that returned nothing because a source blocked us must say so.
    # Reporting "0 new jobs" for a 429 sends you looking at the filters.
    if throttled:
        names = ", ".join(h.replace("www.", "") for h in throttled)
        result["warning"] = (
            f"{names} rate-limited this scan (HTTP 429), so its results are "
            f"missing or incomplete. Wait ~10 minutes before scanning again."
        )
    return result
