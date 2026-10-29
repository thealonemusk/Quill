"""
Indeed Job Scraper — searches and scrapes jobs from Indeed India.
Handles public job search and JD extraction using BeautifulSoup to avoid Playwright subprocess issues.
"""
import asyncio
from typing import List
from loguru import logger
import requests

from backend.scrapers import http
from bs4 import BeautifulSoup
import urllib.parse
import json

from backend.scrapers.base import normalize_job
from backend.config import settings
from backend.job_freshness import INDEED_FROMAGE_DAYS

def _scrape_job_detail_bs4(url: str) -> dict:
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        resp = http.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.content, 'html.parser')
        
        jd_el = soup.find(id='jobDescriptionText')
        jd_text = jd_el.get_text(separator='\n').strip() if jd_el else ""
        
        return {
            "jd_text": jd_text,
            "easy_apply": False,
            "recruiter_name": "",
            "recruiter_profile": "",
        }
    except Exception as e:
        logger.debug(f"Could not scrape job detail {url}: {e}")
        return {"jd_text": "", "easy_apply": False, "recruiter_name": "", "recruiter_profile": ""}

async def scrape_indeed_jobs(
    roles: List[str] = None,
    locations: List[str] = None,
    max_jobs: int = 25,
    headless: bool = True,
) -> List[dict]:
    """
    Main Indeed scraper entry point using requests/BeautifulSoup.
    Returns list of normalized job dicts.
    """
    roles = roles or settings.target_roles_list
    locations = ["Bengaluru", "Delhi", "Noida", "Pune", "Hyderabad", "Mumbai" , "Gurugram"]
    all_jobs = []

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    
    for role in roles:
        for location in locations:
            try:
                query = urllib.parse.quote(role)
                loc = urllib.parse.quote(location)
                search_url = (
                    f"https://in.indeed.com/jobs"
                    f"?q={query}"
                    f"&l={loc}"
                    f"&sc=0kf%3Aexplvl(ENTRY_LEVEL)%3B" # Entry Level / <2 yrs
                    f"&sort=date&fromage={INDEED_FROMAGE_DAYS}"
                )
                
                resp = await asyncio.to_thread(http.get, search_url, headers=headers, timeout=15)
                soup = BeautifulSoup(resp.content, 'html.parser')
                
                # Indeed sometimes injects jobs via window.mosaic.providerData
                script_tags = soup.find_all('script')
                mosaic_data = None
                for script in script_tags:
                    if script.string and 'window.mosaic.providerData["mosaic-provider-jobcards"]=' in script.string:
                        try:
                            json_str = script.string.split('window.mosaic.providerData["mosaic-provider-jobcards"]=')[1].split(';\n')[0]
                            mosaic_data = json.loads(json_str)
                            break
                        except Exception:
                            pass

                job_cards = []
                if mosaic_data and 'metaData' in mosaic_data and 'mosaicProviderJobCardsModel' in mosaic_data['metaData']:
                    job_cards = mosaic_data['metaData']['mosaicProviderJobCardsModel'].get('results', [])
                
                logger.info(f"Found {len(job_cards)} Indeed jobs for '{role}' in '{location}'")
                
                jobs_scraped = 0
                for card in job_cards[:max_jobs]:
                    try:
                        title = card.get('title', '')
                        company = card.get('company', '')
                        city = card.get('jobLocationCity', location)
                        url = f"https://in.indeed.com/viewjob?jk={card.get('jobkey', '')}"
                        
                        if not title or not card.get('jobkey'):
                            continue

                        detail = await asyncio.to_thread(_scrape_job_detail_bs4, url)
                        
                        raw = {
                            "title": title,
                            "company": company,
                            "location": city,
                            "url": url,
                            **detail,
                        }
                        
                        all_jobs.append(normalize_job(raw, "indeed"))
                        jobs_scraped += 1
                        
                    except Exception as e:
                        logger.debug(f"Indeed bs4: Skipping card due to parse error: {e}")
                        continue
                        
                logger.info(f"Scraped {jobs_scraped} Indeed jobs for '{role}'")
                
            except Exception as e:
                logger.error(f"Indeed search failed for {role}/{location}: {e}")
                continue
                
    return all_jobs
