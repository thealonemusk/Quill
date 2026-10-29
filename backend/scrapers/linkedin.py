"""
LinkedIn Job Scraper — searches and scrapes jobs from LinkedIn public listings.
Uses the guest seeMoreJobPostings API with pagination for higher coverage.
"""
import asyncio
import re
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Set

from bs4 import BeautifulSoup
from loguru import logger

from backend.scrapers import http
from backend.scrapers.base import normalize_job
from backend.scrapers.filter_utils import (
    ENTRY_TITLE_SIGNALS,
    EUROPE_SEARCH_LOCATIONS,
    passes_hard_filters,
)
from backend.scrapers.registry import resolve_discovery_roles, resolve_roles
from backend.scan_control import should_stop
from backend.config import settings
from backend.job_freshness import LINKEDIN_TIME_FILTER

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT}

# Broader India coverage beyond user settings (scoring still ranks preferred cities).
#
# Every entry here multiplies the request count by the number of roles and
# pages, so this list is the single biggest lever on whether LinkedIn blocks
# the scan. "India" already returns roles in every city; the named cities are
# here only because LinkedIn ranks a city query differently, so a handful of
# the largest markets is worth it and a long tail is not.
EXTRA_LOCATIONS = [
    "India",
    "Bengaluru", "Hyderabad", "Pune", "Noida", "Mumbai",
]

# LinkedIn resolves these to the same geo, so searching both doubles the
# requests for an identical result set.
LOCATION_SYNONYMS = {
    "bangalore": "bengaluru",
    "gurgaon": "gurugram",
    "new delhi": "delhi",
    "bombay": "mumbai",
    "calcutta": "kolkata",
}

SKIP_SEARCH_LOCATIONS = {"remote", "work from home", "wfh", "anywhere"}

# Searching for a city whose jobs the hard filters throw away is pure cost.
from backend.scrapers.filter_utils import EXCLUDED_CITIES

LINKEDIN_PAGES = 3
# One page per city. Three pages across six cities is what left a Europe
# scan fetching descriptions long after the list still looked empty.
EUROPE_LINKEDIN_PAGES = 1
EUROPE_LISTING_CAP = 40
# First sponsors in the list, one page in each of the four countries.
EUROPE_SPONSOR_SEARCHES = 72
EUROPE_PER_COMPANY = 4
PAGE_SIZE = 10

# Two JD fetches per listing, and the search pages are still going. Six
# workers on a host that throttles is how a scan earns a block.
JD_WORKERS = 2

# Stop searching once this many pages have failed outright. Past this point
# LinkedIn has decided, and the remaining requests only deepen the block.
MAX_FAILED_PAGES = 8


def _merge_locations(locations: List[str]) -> List[str]:
    merged: List[str] = []
    seen: Set[str] = set()
    for loc in list(locations) + EXTRA_LOCATIONS:
        key = loc.lower().strip()
        key = LOCATION_SYNONYMS.get(key, key)
        if not key or key in seen or key in SKIP_SEARCH_LOCATIONS:
            continue
        # A city the hard filters reject is a search we pay for and discard.
        if any(city in key for city in EXCLUDED_CITIES):
            continue
        seen.add(key)
        merged.append(loc.strip())
    return merged


def _search_queries(role: str) -> List[str]:
    """
    Query variants per role.

    One. The "<role> 0-2 years" variant doubled every scan's request count to
    surface jobs the plain query already returns — LinkedIn matches on the
    description, and the experience cap is enforced by the hard filters
    afterwards regardless. It was not worth a block.
    """
    base = role.strip()
    return [base] if base else []


def _parse_search_cards(html: str) -> List[dict]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.find_all("li")
    results = []

    for card in cards:
        title_el = card.select_one(".base-search-card__title, h3")
        company_el = card.select_one(".base-search-card__subtitle, h4")
        location_el = card.select_one(".job-search-card__location")
        link_el = card.find("a", href=True)

        title = title_el.get_text(strip=True) if title_el else ""
        company = company_el.get_text(strip=True) if company_el else ""
        location = location_el.get_text(strip=True) if location_el else ""
        url = link_el["href"].split("?")[0] if link_el else ""

        if title and url:
            results.append({
                "title": title,
                "company": company,
                "location": location,
                "url": url,
            })

    return results


def _fetch_search_page(
    keywords: str, location: str, start: int, time_filter: str
) -> Optional[List[dict]]:
    """Fetch one page of search results.

    Returns [] for a genuinely empty page and None when the request failed, so
    the caller can tell "no more jobs" from "LinkedIn refused to answer". The
    two used to be indistinguishable: a 429 body parses to zero cards, and the
    pagination loop read that as the end of the results and stopped early.
    """
    params = urllib.parse.urlencode({
        "keywords": keywords,
        "location": location,
        "f_TPR": time_filter,
        "start": start,
    })
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?{params}"
    resp = http.get(url, headers=HEADERS)
    if resp.status_code == 404:
        return []
    if resp.status_code != 200:
        logger.warning(
            f"LinkedIn search HTTP {resp.status_code} for '{keywords}' / "
            f"'{location}' @ {start} - results may be incomplete"
        )
        return None
    return _parse_search_cards(resp.text)


def _linkedin_job_id(url: str) -> str:
    match = re.search(r"(\d{8,})", url or "")
    return match.group(1) if match else ""


def _jd_from_html(html) -> str:
    soup = BeautifulSoup(html, "html.parser")
    jd_el = soup.find(class_="show-more-less-html__markup")
    if not jd_el:
        jd_el = soup.find(class_="description__text")
    return jd_el.get_text(separator="\n").strip() if jd_el else ""


def _has_entry_title(title: str) -> bool:
    t = (title or "").lower()
    return any(sig in t for sig in ENTRY_TITLE_SIGNALS)


def _scrape_job_detail_bs4(url: str) -> dict:
    empty = {"jd_text": "", "easy_apply": False, "recruiter_profile": ""}
    job_id = _linkedin_job_id(url)
    pages = []
    if job_id:
        pages.append(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}")
    pages.append(url)
    for page_url in pages:
        try:
            resp = http.get(page_url, headers=HEADERS, fresh=True)
            jd_text = _jd_from_html(resp.content)
            if len(jd_text) >= 80:
                return {"jd_text": jd_text, "easy_apply": False, "recruiter_profile": ""}
        except http.RateLimited:
            # In cooldown: every remaining listing will fail the same way, so
            # return quietly and let the caller drop what it cannot verify.
            return empty
        except Exception as exc:
            logger.debug(f"Could not scrape job detail {page_url}: {exc}")
    return empty


def linkedin_page_count(region: str) -> int:
    return EUROPE_LINKEDIN_PAGES if region == "europe" else LINKEDIN_PAGES


def europe_sponsor_queries(role: str) -> List[tuple]:
    """Role plus employer, in the four countries. Amazon is searched on its own API."""
    from backend.autopilot.select import EUROPE_VISA_SPONSORS

    places = ("United Kingdom", "Ireland", "Germany", "Netherlands")
    role = (role or "Software Engineer").strip()
    queries = []
    for company in EUROPE_VISA_SPONSORS:
        if company == "amazon":
            continue
        for place in places:
            queries.append((f"{role} {company}", place))
            if len(queries) >= EUROPE_SPONSOR_SEARCHES:
                return queries
    return queries


def _collect_listings(
    roles: List[str],
    locations: List[str],
    max_jobs: int,
    pages: int = LINKEDIN_PAGES,
) -> List[dict]:
    seen_urls: Set[str] = set()
    listings: List[dict] = []
    failed_pages = 0

    for time_filter in [LINKEDIN_TIME_FILTER]:
        for role in roles:
            for query in _search_queries(role):
                for location in locations:
                    for page in range(pages):
                        if should_stop():
                            return listings
                        start = page * PAGE_SIZE
                        try:
                            batch = _fetch_search_page(query, location, start, time_filter)
                        except http.RateLimited as exc:
                            # LinkedIn has put us in cooldown. Nothing further
                            # in this scan will succeed, and asking anyway only
                            # lengthens the block.
                            logger.warning(f"LinkedIn stopped early: {exc}")
                            return listings
                        except Exception as exc:
                            logger.warning(
                                f"LinkedIn page failed for '{query}' / '{location}' @ {start}: {exc}"
                            )
                            batch = None

                        if batch is None:
                            # Request failed after retries. Stop paginating this
                            # query, but count it so the scan reports the gap
                            # instead of looking like a clean empty result.
                            failed_pages += 1
                            if failed_pages >= MAX_FAILED_PAGES:
                                logger.warning(
                                    f"LinkedIn: giving up after {failed_pages} failed pages"
                                )
                                return listings
                            break

                        if not batch:
                            break

                        for item in batch:
                            url = item["url"]
                            if url in seen_urls:
                                continue
                            seen_urls.add(url)

                            preview = {
                                "title": item["title"],
                                "company": item["company"],
                                "location": item["location"] or location,
                                "url": url,
                                "jd_text": "",
                                "platform": "linkedin",
                            }
                            ok, _ = passes_hard_filters(
                                preview, require_jd=False, region=settings.active_region,
                            )
                            if not ok:
                                continue

                            listings.append(preview)
                            if len(listings) >= max_jobs:
                                return listings

                        if len(batch) < PAGE_SIZE:
                            break

        if len(listings) >= max_jobs // 2:
            break

    if failed_pages:
        logger.warning(
            f"LinkedIn: {failed_pages} search pages failed after retries - "
            "this scan is missing results"
        )
    return listings


def _collect_sponsor_listings(role: str, max_jobs: int) -> List[dict]:
    """Search known sponsors. A bare role search was mostly recruiters."""
    from backend.autopilot.select import sponsors_europe_visa

    listings: List[dict] = []
    seen_urls: Set[str] = set()
    per_company: dict = {}
    failed_pages = 0
    for keywords, location in europe_sponsor_queries(role):
        if should_stop() or len(listings) >= max_jobs:
            break
        try:
            batch = _fetch_search_page(keywords, location, 0, LINKEDIN_TIME_FILTER)
        except http.RateLimited as exc:
            logger.warning(f"LinkedIn stopped early: {exc}")
            break
        except Exception as exc:
            logger.warning(f"LinkedIn page failed for '{keywords}' / '{location}': {exc}")
            failed_pages += 1
            if failed_pages >= MAX_FAILED_PAGES:
                break
            continue
        if not batch:
            continue
        for item in batch:
            url = item["url"]
            if url in seen_urls:
                continue
            company = item.get("company") or ""
            if not sponsors_europe_visa(company):
                continue
            key = company.lower()
            if per_company.get(key, 0) >= EUROPE_PER_COMPANY:
                continue
            seen_urls.add(url)
            preview = {
                "title": item["title"],
                "company": company,
                "location": item["location"] or location,
                "url": url,
                "jd_text": "",
                "platform": "linkedin",
            }
            ok, _ = passes_hard_filters(
                preview, require_jd=False, region=settings.active_region,
            )
            if not ok:
                continue
            listings.append(preview)
            per_company[key] = per_company.get(key, 0) + 1
            if len(listings) >= max_jobs:
                break
    if failed_pages:
        logger.warning(
            f"LinkedIn: {failed_pages} sponsor searches failed after retries"
        )
    return listings


def _enrich_listings(listings: List[dict]) -> List[dict]:
    if not listings:
        return []

    reasons: Counter = Counter()
    enriched = []
    # Stop between batches. One pool over the whole list ignores the stop
    # flag until every description has been fetched, so a cancelled scan
    # stored nothing.
    step = max(JD_WORKERS * 4, 1)
    for start in range(0, len(listings), step):
        if should_stop():
            logger.info("LinkedIn: stop requested — keeping descriptions fetched so far")
            break
        chunk = listings[start:start + step]
        with ThreadPoolExecutor(max_workers=JD_WORKERS) as pool:
            details = list(pool.map(_scrape_job_detail_bs4, [j["url"] for j in chunk]))
        for listing, detail in zip(chunk, details):
            job_data = {**listing, **detail, "platform": "linkedin"}
            ok, reason = passes_hard_filters(
                job_data,
                require_jd=not _has_entry_title(job_data.get("title", "")),
                region=settings.active_region,
            )
            if not ok:
                if "JD missing" in reason:
                    reasons["jd_missing"] += 1
                elif "more than" in reason:
                    reasons["experience_over_2y"] += 1
                elif "excluded" in reason.lower() or "excluded level" in reason.lower():
                    reasons["senior_title"] += 1
                elif "Frontend" in reason:
                    reasons["frontend"] += 1
                elif "intern" in reason.lower():
                    reasons["intern"] += 1
                else:
                    reasons["location_or_other"] += 1
                continue
            enriched.append(normalize_job(job_data, "linkedin"))
    if reasons:
        logger.info(f"LinkedIn: dropped {sum(reasons.values())} after JD — {dict(reasons)}")
    return enriched


async def scrape_linkedin_jobs(
    roles: List[str] = None,
    locations: List[str] = None,
    max_jobs: int = 500,
    headless: bool = True,
) -> List[dict]:
    """
    Main LinkedIn scraper entry point.
    Returns list of normalized job dicts.
    """
    if settings.active_region == "europe":
        # A role-only search returned recruiters. Ask for known sponsors,
        # and keep Amazon on its own API where it can be capped.
        roles = resolve_roles(settings.target_roles_list)
        role = roles[0] if roles else "Software Engineer"
        max_jobs = min(max_jobs, EUROPE_LISTING_CAP)
        queries = europe_sponsor_queries(role)
        logger.info(
            f"LinkedIn: {len(queries)} sponsor searches for {role!r} "
            f"(cap {max_jobs} listings)"
        )
        listings = await asyncio.to_thread(_collect_sponsor_listings, role, max_jobs)
    else:
        roles = resolve_discovery_roles(roles or settings.target_roles_list)
        locations = _merge_locations(locations or settings.preferred_locations_list)
        pages = linkedin_page_count("india")
        logger.info(f"LinkedIn: searching {len(roles)} roles across {len(locations)} locations")
        searches = len(roles) * len(locations) * pages
        logger.info(
            f"LinkedIn: searching {len(roles)} roles x {len(locations)} locations "
            f"x {pages} pages = up to {searches} requests"
        )
        listings = await asyncio.to_thread(_collect_listings, roles, locations, max_jobs, pages)
    logger.info(f"LinkedIn: {len(listings)} listings passed title pre-filter")

    jobs = await asyncio.to_thread(_enrich_listings, listings)
    logger.info(f"LinkedIn: {len(jobs)} jobs after JD validation")

    if "www.linkedin.com" in http.rate_limited_hosts():
        logger.warning(
            "LinkedIn rate-limited this scan (HTTP 429). Results are incomplete — "
            "wait ~10 minutes before scanning again."
        )
    return jobs
