"""
Amazon, Microsoft and Google — the three that run their own ATS.

They are handled apart from `company_careers.py` because none of them speaks
Greenhouse or Lever, and each fails differently:

  Amazon     public JSON search API. Works today, no browser needed.
  Microsoft  has a JSON API at gcsservices.careers.microsoft.com, but it fails
             TLS handshake from this machine; the careers site itself is a
             JavaScript shell, so it needs a browser.
  Google     retired its public search API (v2 and v3 both 404). Listing pages
             are client-rendered, so it needs a browser too.

Discovery is only half the story. All three require an account before the
application form opens, so jobs found here are marked `needs_login` and are
routed to the credentialed apply path rather than the anonymous one.
"""
from __future__ import annotations

import re
import time
from typing import Dict, Iterable, List, Optional

import requests
from loguru import logger

from backend.config import settings
from backend.scrapers.base import normalize_job
from backend.scrapers.filter_utils import EUROPE_PREFERRED_LOCATIONS, europe_location_ok
from backend.scan_control import should_stop

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*"}
TIMEOUT = 25

AMAZON_SEARCH = "https://www.amazon.jobs/en/search.json"
AMAZON_JOB = "https://www.amazon.jobs"

INDIA_TOKENS = ("india", "bengaluru", "bangalore", "hyderabad", "pune", "mumbai",
                "delhi", "noida", "gurgaon", "gurugram", "chennai", "kolkata")


def _search_places() -> List[tuple]:
    """(location query, Amazon country code) for the active region."""
    if settings.active_region == "europe":
        return [
            ("United Kingdom", "GBR"),
            ("Ireland", "IRL"),
            ("Germany", "DEU"),
            ("Netherlands", "NLD"),
        ]
    return [("India", "IND")]


def _title_ok(title: str, roles: Iterable[str]) -> bool:
    """Amazon's search returns support and analyst roles for a software query."""
    from backend.scrapers.company_careers import _role_matches
    return _role_matches(title, list(roles))


def _location_in_region(location: str) -> bool:
    if settings.active_region == "europe":
        return europe_location_ok(location)
    return _is_india(location)


def _is_india(location: str) -> bool:
    """
    Location match on word boundaries.

    A plain substring test is wrong here and was: an `"in, "` token matched
    "Dubl*in, D*ublin" and let Ireland and United States roles through.
    """
    blob = (location or "").lower()
    return any(
        re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", blob)
        for token in INDIA_TOKENS
    )


async def _scroll(page, rounds: int = 3) -> None:
    """Both careers sites load results lazily as the page is scrolled."""
    for _ in range(rounds):
        await page.mouse.wheel(0, 2200)
        await page.wait_for_timeout(1800)


def scrape_amazon(
    roles: Iterable[str],
    max_jobs: int = 120,
    page_size: int = 100,
) -> List[Dict]:
    """
    Amazon jobs via the public amazon.jobs search API.

    Applying needs an amazon.jobs account, and most SDE requisitions route to an
    online assessment before a human reads anything — so these are worth
    surfacing, but they are never a fire-and-forget submit.
    """
    found: List[Dict] = []
    seen: set[str] = set()

    for place, country in _search_places():
        if should_stop():
            break
        for role in roles:
            if should_stop():
                return found
            offset = 0
            while len(found) < max_jobs:
                if should_stop():
                    return found
                params = {
                    "base_query": role,
                    "loc_query": place,
                    "country": country,
                    "result_limit": page_size,
                    "offset": offset,
                    "sort": "recent",
                }
                try:
                    resp = requests.get(AMAZON_SEARCH, params=params, headers=HEADERS, timeout=TIMEOUT)
                    if resp.status_code != 200:
                        logger.warning(f"Amazon search HTTP {resp.status_code} for {role!r} in {place}")
                        break
                    payload = resp.json()
                except requests.exceptions.ConnectionError as exc:
                    # DNS failure is the host, not this one query. Walking every
                    # role and country turns one outage into a scan that stores nothing.
                    logger.warning(f"Amazon search unreachable: {exc}")
                    return found
                except Exception as exc:  # noqa: BLE001 - one bad role must not kill the scan
                    logger.warning(f"Amazon search failed for {role!r} in {place}: {exc}")
                    break

                jobs = payload.get("jobs") or []
                if not jobs:
                    break

                for job in jobs:
                    path = job.get("job_path") or ""
                    url = f"{AMAZON_JOB}{path}" if path.startswith("/") else path
                    if not url or url in seen:
                        continue
                    location = job.get("location") or job.get("normalized_location") or ""
                    if not _location_in_region(location):
                        continue
                    seen.add(url)
                    if not _title_ok(job.get("title") or "", roles):
                        continue
                    found.append(normalize_job({
                        "title": job.get("title") or "",
                        "company": "Amazon",
                        "url": url,
                        "location": location,
                        "jd_text": " ".join(filter(None, [
                            job.get("description") or "",
                            job.get("basic_qualifications") or "",
                            job.get("preferred_qualifications") or "",
                        ]))[:20000],
                        "experience_required": job.get("job_schedule_type") or "",
                        "easy_apply": False,
                    }, "bigtech"))
                    if len(found) >= max_jobs:
                        break

                offset += page_size
                if offset >= int(payload.get("hits") or 0):
                    break
                time.sleep(0.4)          # be a polite client

    logger.info(f"Amazon: {len(found)} roles")
    return found


async def scrape_microsoft(
    roles: Iterable[str], max_jobs: int = 60, headless: bool = False
) -> List[Dict]:
    """
    Microsoft careers, driven through a browser.

    The JSON endpoint at gcsservices.careers.microsoft.com refuses the TLS
    handshake from this machine, and jobs.careers.microsoft.com serves a
    JavaScript shell, so the listing has to be rendered.
    """
    from playwright.async_api import async_playwright

    from backend.scrapers.base import create_browser_context

    found: List[Dict] = []
    try:
        async with async_playwright() as pw:
            browser, ctx = await create_browser_context(pw, headless=headless)
            page = await ctx.new_page()
            for place, _country in _search_places():
                if should_stop():
                    break
                for role in roles:
                    if should_stop() or len(found) >= max_jobs:
                        break
                    url = (
                        "https://jobs.careers.microsoft.com/global/en/search"
                        f"?q={requests.utils.quote(role)}&lc={requests.utils.quote(place)}"
                    )
                    await page.goto(url, wait_until="networkidle", timeout=70000)
                    await page.wait_for_timeout(9000)          # heavy SPA
                    await _scroll(page)
                    # Result cards are anchors to apply.careers.microsoft.com; the
                    # title and location are the first two lines of the card text.
                    cards = await page.evaluate(
                        """() => Array.from(document.querySelectorAll('a[href*="/careers/job/"]'))
                            .map(a => {
                              const lines = a.innerText.split('\\n').map(s => s.trim()).filter(Boolean);
                              return {title: lines[0] || '', location: lines[1] || '', href: a.href};
                            }).filter(x => x.title && x.href)"""
                    )
                    for card in cards:
                        # The lc= parameter does not reliably filter, so the location
                        # is checked here rather than trusted from the query.
                        if not _location_in_region(card.get("location", "")):
                            continue
                        if not _title_ok(card["title"], roles):
                            continue
                        found.append(normalize_job({
                            "title": card["title"],
                            "company": "Microsoft",
                            "url": card["href"],
                            "location": card.get("location") or place,
                            "jd_text": "",
                            "easy_apply": False,
                        }, "bigtech"))
                        if len(found) >= max_jobs:
                            break
                    if len(found) >= max_jobs:
                        break
                if len(found) >= max_jobs:
                    break
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Microsoft scrape failed: {exc}")

    logger.info(f"Microsoft: {len(found)} roles")
    return found


async def scrape_google(
    roles: Iterable[str], max_jobs: int = 60, headless: bool = False
) -> List[Dict]:
    """Google careers, driven through a browser — the public API was retired."""
    from playwright.async_api import async_playwright

    from backend.scrapers.base import create_browser_context

    # Same names the allow-list accepts. A second, shorter regex dropped cities
    # the filter would have kept, so the card scan never saw them.
    if settings.active_region == "europe":
        loc_re = "|".join(EUROPE_PREFERRED_LOCATIONS)
    else:
        loc_re = "India|Bengaluru|Bangalore|Hyderabad|Pune|Gurgaon|Noida"
    # The anchor itself carries no text; the title and location live in the
    # surrounding card, so walk up from the link.
    script = f"""() => Array.from(
              document.querySelectorAll('a[href*="/jobs/results/"]'))
            .filter(a => /\\/jobs\\/results\\/\\d/.test(a.href))
            .map(a => {{
              const card = a.closest('li, [class*="lLd3Je"], div[jscontroller]') || a.parentElement;
              const h = card && card.querySelector('h3, [role="heading"]');
              const text = card ? card.innerText.split('\\n').map(s => s.trim()).filter(Boolean) : [];
              const loc = text.find(t => /{loc_re}/i.test(t));
              return {{
                title: (h ? h.innerText : text[0] || '').trim(),
                location: loc || '',
                href: a.href,
              }};
            }}).filter(x => x.title && x.href)"""

    found: List[Dict] = []
    try:
        async with async_playwright() as pw:
            browser, ctx = await create_browser_context(pw, headless=headless)
            page = await ctx.new_page()
            for place, _country in _search_places():
                if should_stop():
                    break
                for role in roles:
                    if should_stop() or len(found) >= max_jobs:
                        break
                    url = (
                        "https://www.google.com/about/careers/applications/jobs/results/"
                        f"?q={requests.utils.quote(role)}&location={requests.utils.quote(place)}"
                    )
                    await page.goto(url, wait_until="networkidle", timeout=70000)
                    await page.wait_for_timeout(9000)
                    await _scroll(page)
                    cards = await page.evaluate(script)
                    for card in cards:
                        if not _location_in_region(card.get("location", "")):
                            continue
                        if not _title_ok(card["title"], roles):
                            continue
                        found.append(normalize_job({
                            "title": card["title"],
                            "company": "Google",
                            "url": card["href"],
                            "location": card.get("location") or place,
                            "jd_text": "",
                            "easy_apply": False,
                        }, "bigtech"))
                        if len(found) >= max_jobs:
                            break
                    if len(found) >= max_jobs:
                        break
                if len(found) >= max_jobs:
                    break
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Google scrape failed: {exc}")

    logger.info(f"Google: {len(found)} roles")
    return found


# Every company here puts the application behind a sign-in, so the apply stage
# must use stored credentials rather than the anonymous Greenhouse/Lever path.
NEEDS_LOGIN = {"amazon", "microsoft", "google"}


def requires_login(company: str) -> bool:
    return (company or "").strip().lower() in NEEDS_LOGIN
