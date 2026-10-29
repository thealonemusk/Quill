"""Scraper registry — maps platform keys to scrape callables."""

from typing import Awaitable, Callable, Dict, List

ScrapeFn = Callable[..., Awaitable[List[dict]]]

_FALLBACK_ROLES = [
    "Software Engineer",
    "Backend Engineer",
    "Full Stack Developer",
    "AI Engineer",
    "Software Developer",
    "Platform Engineer",
    "DevOps Engineer",
    "SDE",
    "Machine Learning Engineer",
]

# Extra roles searched during discovery (user target_roles still drive scoring priority).
_DISCOVERY_EXTRA_ROLES = [
    "Associate Software Engineer",
    "Junior Software Engineer",
    "Entry Level Software Engineer",
    "Python Developer",
    "Java Developer",
    "Golang Developer",
    "Node.js Developer",
    "Cloud Engineer",
    "Data Engineer",
]


async def _run_linkedin(roles, max_jobs, headless):
    from backend.scrapers.linkedin import scrape_linkedin_jobs
    return await scrape_linkedin_jobs(roles=roles, max_jobs=max_jobs, headless=headless)


async def _run_indeed(roles, max_jobs, headless):
    from backend.scrapers.indeed import scrape_indeed_jobs
    return await scrape_indeed_jobs(roles=roles, max_jobs=max_jobs, headless=headless)


async def _run_naukri(roles, max_jobs, headless):
    from backend.scrapers.naukri import scrape_naukri_jobs
    return await scrape_naukri_jobs(roles=roles, max_jobs=max_jobs, headless=headless)


async def _run_instahyre(roles, max_jobs, headless):
    from backend.scrapers.instahyre import scrape_instahyre_jobs
    return await scrape_instahyre_jobs(roles=roles, max_jobs=max_jobs, headless=headless)


async def _run_wellfound(roles, max_jobs, headless):
    from backend.scrapers.wellfound import scrape_wellfound_jobs
    return await scrape_wellfound_jobs(roles=roles, max_jobs=max_jobs, headless=headless)


async def _run_company_careers(roles, max_jobs, headless):
    from backend.scrapers.company_careers import scrape_company_career_jobs
    return await scrape_company_career_jobs(roles=roles, max_jobs=max_jobs)


async def _run_amazon(roles, max_jobs, headless):
    import asyncio

    from backend.config import settings
    from backend.scrapers.bigtech import scrape_amazon

    if settings.active_region == "europe":
        from backend.autopilot.select import EUROPE_JOBS_PER_COMPANY
        roles = resolve_roles(settings.target_roles_list)
        max_jobs = min(max_jobs, EUROPE_JOBS_PER_COMPANY)
    return await asyncio.to_thread(scrape_amazon, roles, max_jobs)


async def _run_microsoft(roles, max_jobs, headless):
    from backend.config import settings
    from backend.scrapers.bigtech import scrape_microsoft

    if settings.active_region == "europe":
        roles = resolve_roles(settings.target_roles_list)
    return await scrape_microsoft(roles, max_jobs, headless=False)


async def _run_google(roles, max_jobs, headless):
    from backend.config import settings
    from backend.scrapers.bigtech import scrape_google

    if settings.active_region == "europe":
        roles = resolve_roles(settings.target_roles_list)
    return await scrape_google(roles, max_jobs, headless=False)


async def _run_bigtech(roles, max_jobs, headless):
    """
    Amazon, Microsoft and Google — each runs its own ATS, none speaks
    Greenhouse or Lever, so they need bespoke handling. Amazon has a public
    JSON API; the other two are client-rendered and need a visible browser,
    which is why headless is not honoured for them.
    """
    import asyncio

    from backend.scrapers.bigtech import scrape_amazon, scrape_google, scrape_microsoft

    per_source = max(10, max_jobs // 3)
    jobs = await asyncio.to_thread(scrape_amazon, roles, per_source)
    for fn in (scrape_microsoft, scrape_google):
        try:
            jobs += await fn(roles, per_source, headless=False)
        except Exception:
            continue          # one silent source must not lose the others
    return jobs[:max_jobs]


SCRAPER_REGISTRY: Dict[str, ScrapeFn] = {
    "linkedin": _run_linkedin,
    "indeed": _run_indeed,
    "naukri": _run_naukri,
    "instahyre": _run_instahyre,
    "wellfound": _run_wellfound,
    "company_careers": _run_company_careers,
    "bigtech": _run_bigtech,
    "amazon": _run_amazon,
    "microsoft": _run_microsoft,
    "google": _run_google,
}

SCRAPER_LABELS = {
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    "naukri": "Naukri",
    "instahyre": "InstaHyre",
    "wellfound": "Wellfound",
    "company_careers": "Company Careers",
    "bigtech": "Amazon / Microsoft / Google",
    "amazon": "Amazon",
    "microsoft": "Microsoft",
    "google": "Google",
    "manual": "Manual",
}


def resolve_roles(settings_roles: List[str]) -> List[str]:
    roles = [r.strip() for r in settings_roles if r.strip()]
    return roles if roles else list(_FALLBACK_ROLES)


def resolve_discovery_roles(settings_roles: List[str]) -> List[str]:
    """Broader role list for scraping; keeps user roles first for priority."""
    merged: List[str] = []
    seen: set = set()
    for role in list(settings_roles) + _FALLBACK_ROLES + _DISCOVERY_EXTRA_ROLES:
        key = role.lower().strip()
        if key and key not in seen:
            seen.add(key)
            merged.append(role.strip())
    return merged if merged else list(_FALLBACK_ROLES)
