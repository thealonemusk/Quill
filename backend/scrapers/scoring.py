"""Heuristic relevance scoring for discovered jobs."""

from __future__ import annotations

from typing import List, Tuple

from backend.autopilot.select import Tier, company_tier
from backend.scrapers.filter_utils import parse_experience_years

# Company quality comes from `autopilot.select.company_tier`, which is the
# curated FAANG/ELITE/STRONG/KNOWN ranking and matches on whole words.
#
# This used to score against TOP_COMPANIES — the list of career boards the
# scrapers crawl, which is not a quality ranking at all. Two consequences:
# Google, Microsoft, Amazon and Uber got no bonus because they run their own
# ATS and live in bigtech.py, while "Accenture India" and "Adobe India" did.
# The test was also `premium in company or company in premium`, and the
# reversed half is true for any short string — an empty company name matched
# every premium name and scored a perfect 100.


def _title_matches_role(title: str, roles: List[str]) -> Tuple[bool, bool]:
    """Return (exact_match, partial_match)."""
    title_lower = title.lower()
    for role in roles:
        rl = role.lower().strip()
        if not rl:
            continue
        if rl in title_lower:
            return True, False
        words = rl.split()
        if len(words) > 1 and all(w in title_lower for w in words):
            return False, True
    return False, False


def score_job(
    job_data: dict,
    target_roles: List[str],
    preferred_locations: List[str],
    experience_years: int = 1,
) -> Tuple[float, str]:
    """Score 0–100 with a short human-readable reason."""
    score = 52.0
    reasons: List[str] = []

    title = job_data.get("title", "")
    title_lower = title.lower()
    company = job_data.get("company", "").lower()
    platform = job_data.get("platform", "")
    location = job_data.get("location", "").lower()
    jd_text = job_data.get("jd_text", "") or ""
    jd_lower = jd_text[:3000].lower()

    if platform == "company_careers":
        if len(jd_text) >= 200:
            score += 14
            reasons.append("Direct career site (with JD)")
        else:
            score += 4
            reasons.append("Direct career site")
    elif platform == "linkedin":
        score += 8
        reasons.append("LinkedIn listing")
    elif platform == "naukri":
        score += 10
        reasons.append("Naukri (0-2 yr filter)")

    exact, partial = _title_matches_role(title, target_roles)
    if exact:
        score += 20
        reasons.append("Strong role match")
    elif partial:
        score += 12
        reasons.append("Partial role match")

    # Tools on the resume. A JD that names them is a closer match than a
    # generic QA lead posting. Missing them does not drop the job.
    stack = ("selenium", "appium", "playwright", "restassured", "rest assured", "testng", "jmeter")
    stack_hits = sum(1 for tool in stack if tool in jd_lower or tool in title_lower)
    if stack_hits:
        score += min(12, stack_hits * 4)
        reasons.append("Automation stack")

    loc_hit = False
    for loc in preferred_locations:
        ll = loc.lower().strip()
        if ll and (ll in location or ll in jd_lower):
            score += 10
            reasons.append(f"Location: {loc}")
            loc_hit = True
            break
    if not loc_hit and ("remote" in location or "remote" in title_lower):
        if any(m in location for m in ("india", "bengaluru", "bangalore", "hyderabad", "pune", "noida", "gurgaon", "gurugram")):
            score += 8
            reasons.append("Remote-friendly")

    tier = company_tier(job_data.get("company", ""))
    if tier >= Tier.ELITE:
        score += 14
        reasons.append(f"{tier.name.capitalize()} company")
    elif tier is Tier.STRONG:
        score += 10
        reasons.append("Well-known company")
    elif tier is Tier.KNOWN:
        score += 5
        reasons.append("Recognised company")

    exp_str = job_data.get("experience_required", "")
    exp_range = (
        parse_experience_years(exp_str)
        or parse_experience_years(title_lower)
        or parse_experience_years(jd_lower)
    )
    if exp_range:
        min_y, max_y = exp_range
        if min_y <= experience_years + 0.5:
            score += 8
            reasons.append("Experience fits profile")
        elif min_y <= experience_years + 1.5:
            score += 3
        else:
            score -= 10

    if len(jd_text) > 400:
        score += 5
        reasons.append("Detailed JD")
    elif platform == "company_careers" and len(jd_text) < 80:
        score -= 10
        reasons.append("Thin career listing")

    # Same source as the bonus above, so the two can never disagree — the old
    # STAFFING_KEYWORDS list was a second, shorter copy of EXCLUDED_MARKERS.
    if tier is Tier.EXCLUDED:
        score -= 15
        reasons.append("Staffing / IT services")

    if job_data.get("easy_apply"):
        score += 4

    score = max(0.0, min(100.0, round(score, 1)))
    fit_reason = "; ".join(reasons[:4]) if reasons else "Matched search criteria"
    return score, fit_reason
