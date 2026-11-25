"""
Base scraper utilities — shared helpers for all platform scrapers.
"""
import hashlib
from pathlib import Path
from typing import Optional
from loguru import logger
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

PROFILE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "chrome_profile"


def url_hash(url: str) -> str:
    """Stable hash for deduplication."""
    return hashlib.md5(url.encode()).hexdigest()


STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
"""


async def create_browser_context(playwright, headless: bool = True, storage_state=None) -> tuple:
    """Launch Chrome with a disk profile so LinkedIn/Gmail stay signed in across runs."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]
    launch_kwargs = {
        "user_data_dir": str(PROFILE_DIR),
        "headless": headless,
        "args": args,
        "ignore_default_args": ["--enable-automation"],
        "viewport": {"width": 1400, "height": 900},
        "locale": "en-IN",
        "timezone_id": "Asia/Kolkata",
    }
    context = None
    last_exc = None
    for channel in ("chrome", None):
        kwargs = dict(launch_kwargs)
        if channel:
            kwargs["channel"] = channel
        try:
            context = await playwright.chromium.launch_persistent_context(**kwargs)
            break
        except Exception as exc:
            last_exc = exc
            logger.debug(f"Chromium launch channel={channel}: {exc}")
    if context is None:
        raise RuntimeError(
            "Could not launch Chrome. Close any leftover Quill Chrome window "
            f"and retry. ({last_exc})"
        )
    await context.add_init_script(STEALTH_JS)
    return context.browser, context


def normalize_job(raw: dict, platform: str) -> dict:
    """Normalize a raw scraped job dict to standard schema."""
    from backend.applier.ats import detect_ats

    url = raw.get("url", "").strip()
    return {
        "title": raw.get("title", "").strip(),
        "company": raw.get("company", "").strip(),
        "platform": platform,
        "url": url,
        "location": raw.get("location", "").strip(),
        "experience_required": raw.get("experience_required", "").strip(),
        "salary_range": raw.get("salary_range", "").strip(),
        "jd_text": raw.get("jd_text", "").strip(),
        "easy_apply": raw.get("easy_apply", False),
        "recruiter_name": raw.get("recruiter_name", "").strip(),
        "recruiter_profile": raw.get("recruiter_profile", "").strip(),
        "ats_type": raw.get("ats_type") or detect_ats(url),
        "relevance_score": 0.0,
        "fit_reason": "",
        "missing_skills": "[]",
        "seniority_level": "",
        "is_engineering_role": True,
        "status": "discovered",
    }
