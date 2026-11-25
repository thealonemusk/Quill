"""
Naukri.com Job Scraper — searches entry-level (0-2 yr) jobs on Naukri.com.
Uses public search URLs; no login required.
"""
from typing import List, Set

from loguru import logger
from playwright.async_api import async_playwright

from backend.scrapers.filter_utils import passes_hard_filters
from backend.scrapers.base import STEALTH_JS, normalize_job
from backend.scrapers.registry import resolve_discovery_roles
from backend.scan_control import should_stop
from backend.config import settings
from backend.job_freshness import NAUKRI_JOB_AGE_DAYS

NAUKRI_LOCATIONS = [
    "Bengaluru", "Hyderabad", "Pune", "Mumbai", "Gurugram", "Gurgaon",
    "Noida", "Delhi", "Chennai", "Kolkata",
]
NAUKRI_PAGES = 2
SKIP_SEARCH_LOCATIONS = {"remote", "work from home", "wfh", "anywhere"}
LOC_SLUG_ALIASES = {
    "bengaluru": "bangalore",
    "gurugram": "gurgaon",
}
CARD_SELECTOR = (
    ".srp-jobtuple-wrapper, .cust-job-tuple, article.jobTuple, "
    "div[class*='jobTuple']"
)


def _merge_locations(locations: List[str]) -> List[str]:
    merged: List[str] = []
    seen: Set[str] = set()
    for loc in list(locations) + NAUKRI_LOCATIONS:
        key = loc.lower().strip()
        if not key or key in seen or key in SKIP_SEARCH_LOCATIONS:
            continue
        seen.add(key)
        merged.append(loc.strip())
    return merged


def _role_slug(role: str) -> str:
    return role.lower().replace(" ", "-").replace(".", "")


def _loc_slug(location: str) -> str:
    slug = _role_slug(location)
    return LOC_SLUG_ALIASES.get(slug, slug)


async def scrape_naukri_jobs(
    roles: List[str] = None,
    locations: List[str] = None,
    max_jobs: int = 300,
    headless: bool = True,
) -> List[dict]:
    """Scrape 0-2 year jobs from Naukri.com."""
    roles = resolve_discovery_roles(roles or settings.target_roles_list)
    locations = _merge_locations(locations or settings.preferred_locations_list)
    all_jobs: List[dict] = []
    seen_urls: Set[str] = set()
    empty_streak = 0

    logger.info(f"Naukri: searching {len(roles)} roles across {len(locations)} locations")

    async with async_playwright() as pw:
        launch_kwargs = {
            "headless": headless,
            "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        }
        browser = None
        for channel in ("chrome", None):
            kwargs = dict(launch_kwargs)
            if channel:
                kwargs["channel"] = channel
            try:
                browser = await pw.chromium.launch(**kwargs)
                break
            except Exception as exc:
                logger.debug(f"Naukri Chromium launch channel={channel}: {exc}")
        if browser is None:
            logger.error("Naukri: could not launch Chrome")
            return []

        ctx = await browser.new_context(
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            viewport={"width": 1400, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        await ctx.add_init_script(STEALTH_JS)
        page = await ctx.new_page()

        stop_all = False
        for role in roles:
            if should_stop() or stop_all:
                break
            for location in locations:
                if should_stop() or stop_all:
                    break
                for page_no in range(1, NAUKRI_PAGES + 1):
                    if should_stop() or len(all_jobs) >= max_jobs:
                        break
                    try:
                        job_slug = _role_slug(role)
                        loc_slug = _loc_slug(location)
                        search_url = (
                            f"https://www.naukri.com/{job_slug}-jobs-in-{loc_slug}-0-to-2-years"
                            f"?jobAge={NAUKRI_JOB_AGE_DAYS}&sort=1&pageNo={page_no}"
                        )

                        await page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
                        try:
                            await page.wait_for_selector(CARD_SELECTOR, timeout=15000)
                        except Exception:
                            pass

                        job_cards = await page.query_selector_all(CARD_SELECTOR)
                        if not job_cards:
                            title = await page.title()
                            empty_streak += 1
                            logger.warning(
                                f"Naukri no cards for '{role}' in '{location}' "
                                f"p{page_no} title={title!r}"
                            )
                            if empty_streak >= 4:
                                logger.error(
                                    "Naukri: 4 empty pages in a row — blocked or layout changed"
                                )
                                stop_all = True
                            break

                        empty_streak = 0
                        logger.info(
                            f"Naukri p{page_no}: {len(job_cards)} cards for '{role}' in '{location}'"
                        )

                        for card in job_cards:
                            if len(all_jobs) >= max_jobs:
                                break
                            try:
                                title_el = await card.query_selector(
                                    "a.title, .jobTupleHeader a, h2 a"
                                )
                                company_el = await card.query_selector(
                                    "a.comp-name, .company-name, a[class*='comp']"
                                )
                                location_el = await card.query_selector(
                                    ".loc-wrap, .loc, .location"
                                )
                                exp_el = await card.query_selector(
                                    ".exp-wrap, .exp, .experience"
                                )

                                title = (await title_el.inner_text()).strip() if title_el else ""
                                company = (await company_el.inner_text()).strip() if company_el else ""
                                location_text = (await location_el.inner_text()).strip() if location_el else ""
                                exp = (await exp_el.inner_text()).strip() if exp_el else ""
                                url = await title_el.get_attribute("href") if title_el else ""

                                if not title or not url or url in seen_urls:
                                    continue

                                preview = {
                                    "title": title,
                                    "company": company,
                                    "location": location_text or location,
                                    "url": url,
                                    "experience_required": exp,
                                    "jd_text": "",
                                    "platform": "naukri",
                                }
                                ok, _ = passes_hard_filters(preview)
                                if not ok:
                                    continue

                                seen_urls.add(url)
                                all_jobs.append(normalize_job(preview, "naukri"))
                            except Exception as e:
                                logger.debug(f"Error parsing Naukri card: {e}")
                                continue

                    except Exception as e:
                        logger.error(f"Naukri search failed for {role}/{location} p{page_no}: {e}")
                        break

                if len(all_jobs) >= max_jobs:
                    break
            if len(all_jobs) >= max_jobs:
                break

        await ctx.close()
        await browser.close()

    logger.info(f"Naukri: {len(all_jobs)} jobs after validation")
    return all_jobs
