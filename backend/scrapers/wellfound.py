"""
Wellfound (formerly AngelList Talent) Job Scraper — searches jobs using public landing pages.
"""
import json
from typing import List
from bs4 import BeautifulSoup
from loguru import logger
from playwright.async_api import async_playwright

from backend.scrapers.base import create_browser_context, normalize_job
from backend.config import settings

ROLE_SLUGS = {
    "software engineer": "software-engineer",
    "backend engineer": "backend-engineer",
    "full stack developer": "full-stack-developer",
    "ai engineer": "ai-engineer",
    "software developer": "software-developer",
}

async def scrape_wellfound_jobs(
    roles: List[str] = None,
    max_jobs: int = 25,
    headless: bool = True,
) -> List[dict]:
    """Scrape jobs from Wellfound India."""
    roles = roles or settings.target_roles_list
    all_jobs = []

    async with async_playwright() as pw:
        browser, ctx = await create_browser_context(pw, headless=headless)
        page = await ctx.new_page()

        for role in roles:
            slug = ROLE_SLUGS.get(role.lower().strip(), role.lower().strip().replace(" ", "-"))
            url = f"https://wellfound.com/role/l/{slug}/india"
            logger.info(f"Wellfound: navigating to {url}")

            try:
                await page.goto(url, timeout=30000)
                await page.wait_for_timeout(4000) # wait for page hydration
                html = await page.content()

                soup = BeautifulSoup(html, "html.parser")
                next_data_el = soup.find("script", {"id": "__NEXT_DATA__"})

                if not next_data_el:
                    logger.warning(f"Wellfound: Could not find __NEXT_DATA__ for {role}")
                    continue

                data = json.loads(next_data_el.string)
                apollo_state = data.get("props", {}).get("pageProps", {}).get("apolloState", {}).get("data", {})

                startup_keys = [k for k in apollo_state.keys() if k.startswith("StartupResult:")]
                
                job_count = 0
                for startup_key in startup_keys:
                    if job_count >= max_jobs:
                        break

                    startup_data = apollo_state.get(startup_key, {})
                    company_name = startup_data.get("name", "")
                    highlighted_jobs = startup_data.get("highlightedJobListings", [])

                    for job_ref in highlighted_jobs:
                        ref_id = job_ref.get("__ref")
                        if not ref_id or ref_id not in apollo_state:
                            continue

                        job_data = apollo_state[ref_id]
                        title = job_data.get("title", "")
                        job_id = job_data.get("id", "")
                        slug_title = job_data.get("slug", "")

                        loc_names = job_data.get("locationNames", [])
                        location = ", ".join(loc_names) if loc_names else "India"
                        job_url = f"https://wellfound.com/jobs/{job_id}-{slug_title}" if job_id else url

                        jd_text = job_data.get("description", "")
                        if jd_text:
                            jd_soup = BeautifulSoup(jd_text, "html.parser")
                            jd_clean = jd_soup.get_text(separator="\n").strip()
                        else:
                            jd_clean = ""

                        normalized = normalize_job({
                            "title": title,
                            "company": company_name,
                            "location": location,
                            "url": job_url,
                            "jd_text": jd_clean,
                            "easy_apply": False
                        }, "wellfound")

                        all_jobs.append(normalized)
                        job_count += 1
                        if job_count >= max_jobs:
                            break

                logger.info(f"Wellfound: Found {job_count} jobs for role '{role}'")

            except Exception as e:
                logger.error(f"Wellfound error for {role}: {e}")
                continue

        await browser.close()

    return all_jobs
