"""
InstaHyre Job Scraper — searches tech jobs on InstaHyre.
"""
from typing import List
from loguru import logger
from playwright.async_api import async_playwright

from backend.scrapers.base import create_browser_context, normalize_job
from backend.config import settings


async def scrape_instahyre_jobs(
    roles: List[str] = None,
    locations: List[str] = None,
    max_jobs: int = 25,
    headless: bool = True,
) -> List[dict]:
    """Scrape jobs from Instahyre India."""
    roles = roles or settings.target_roles_list
    locations = ["Bengaluru", "Delhi", "Noida", "Pune", "Hyderabad", "Mumbai" , "Gurugram"]
    all_jobs = []

    async with async_playwright() as pw:
        browser, ctx = await create_browser_context(pw, headless=headless)
        page = await ctx.new_page()

        try:
            await page.goto("https://www.instahyre.com/candidate/login/", timeout=30000)
            await page.wait_for_timeout(2000)

            google_btn = await page.query_selector(
                'a[href*="google"], button:has-text("Google"), [class*="google-login"], '
                'a:has-text("Google"), .social-login a'
            )
            if not google_btn:
                logger.error("InstaHyre: Could not find 'Continue with Google' button")
                await browser.close()
                return []

            # Google OAuth opens in a popup.
            async with page.context.expect_page() as popup_info:
                await google_btn.click()
            google_page = await popup_info.value
            await google_page.wait_for_load_state("domcontentloaded", timeout=20000)
            await google_page.wait_for_timeout(2000)

            email_input = await google_page.query_selector('input[type="email"]')
            if email_input:
                await email_input.fill(settings.instahyre_email)
                await google_page.click('button:has-text("Next"), #identifierNext')
                await google_page.wait_for_timeout(3000)

            try:
                account = await google_page.query_selector(f'[data-email="{settings.instahyre_email}"]')
                if account:
                    await account.click()
                    await google_page.wait_for_timeout(3000)
            except Exception:
                pass

            await page.wait_for_timeout(6000)
            logger.info("InstaHyre Google login successful")
        except Exception as e:
            logger.warning(f"InstaHyre Google login failed (will attempt search anyway): {e}")

        for role in roles:
            try:
                search_url = (
                    f"https://www.instahyre.com/search-jobs/"
                    f"?designation={role.replace(' ', '+')}"
                    f"&experience=0-2" # 0-2 years
                )
                await page.goto(search_url, timeout=30000)
                await page.wait_for_timeout(3000)

                for _ in range(3):
                    await page.keyboard.press("End")
                    await page.wait_for_timeout(1500)

                job_cards = await page.query_selector_all(".job-card, .opportunity-card, [class*='job-item']")
                logger.info(f"Found {len(job_cards)} InstaHyre jobs for '{role}'")

                for card in job_cards[:max_jobs]:
                    try:
                        title_el = await card.query_selector(".job-title, h3, [class*='title']")
                        company_el = await card.query_selector(".company-name, [class*='company']")
                        location_el = await card.query_selector(".location, [class*='location']")
                        link_el = await card.query_selector("a")

                        title = (await title_el.inner_text()).strip() if title_el else ""
                        company = (await company_el.inner_text()).strip() if company_el else ""
                        location_text = (await location_el.inner_text()).strip() if location_el else ""
                        href = await link_el.get_attribute("href") if link_el else ""

                        if not title:
                            continue

                        url = f"https://www.instahyre.com{href}" if href and href.startswith("/") else href or ""

                        jd_text = ""
                        try:
                            if url:
                                dp = await ctx.new_page()
                                await dp.goto(url, timeout=20000)
                                await dp.wait_for_timeout(2000)
                                jd_el = await dp.query_selector(".job-description, [class*='description']")
                                if jd_el:
                                    jd_text = await jd_el.inner_text()
                                await dp.close()
                        except Exception:
                            pass

                        raw = {
                            "title": title,
                            "company": company,
                            "location": location_text,
                            "url": url,
                            "jd_text": jd_text.strip(),
                        }
                        all_jobs.append(normalize_job(raw, "instahyre"))
                    except Exception as e:
                        logger.warning(f"Error parsing InstaHyre card: {e}")
                        continue

            except Exception as e:
                logger.error(f"InstaHyre search failed: {e}")
                continue

        await browser.close()

    return all_jobs
