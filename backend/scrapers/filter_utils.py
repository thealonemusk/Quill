"""
Filter utilities — text parsing and strict experience/title/location checks.
Hard limits: <= 2 years experience, no frontend-only roles.
Location follows the region: India, or the UK, Ireland, Germany, and the Netherlands.
"""

import re
from typing import Optional, Tuple

# Matched as whole words (see `_title_has_keyword`), never as substrings:
# "vp" rejected "Software Engineer - VPN", "head" rejected "Headless Commerce"
# and "lead" rejected "Leadership Tools". "leader" is listed on its own because
# the bounded "lead" no longer reaches "Team Leader".
EXCLUDED_TITLE_KEYWORDS = [
    "senior", "sr.", "sr", "lead", "leader", "staff", "principal", "manager",
    "director", "head", "architect", "sde 2", "sde-2", "sde2", "sde 3", "sde-3",
    "sde3", "sde ii", "sde-ii", "sde iii", "sde-iii", "level 2", "level 3", "level 4",
    "l2", "l3", "l4", "l5",
    "expert", "vp", "vice president", "chief", "tech lead", "team lead",
    "consultant", "specialist", "advanced software", "advanced engineer",
    "advanced developer",
]

EXCLUDED_TITLE_REGEXES = [
    r'\b(?:ii|iii|iv|v)(?:new)?\b',
    r'\bl[2-7]\b',
    # A standalone level number: "Engineer 2", "SDE - 3". The digit must end the
    # token — without `(?![\d.a-z])` this read " 2026" in "2026 New Grad", "5G"
    # and "3D" as levels and rejected exactly the entry-level roles we want.
    r'(?<!\d)[-_\s](?:2|3|4|5)(?![\d.a-z])(?!(?:\s*\+|\s*(?:years?|yrs?|yr|y)\b|\s+to\s+\d))',
]

MAX_JD_EXPERIENCE_YEARS = 2.0

INTERN_PATTERN = re.compile(r'\bintern(?:s|ship)?\b', re.IGNORECASE)

ENTRY_TITLE_SIGNALS = [
    "new grad", "new-grad", "university", "graduate", "fresher", "entry level",
    "entry-level", "associate", "junior", "software engineer i",
    "sde i", "sde-1", "sde 1", "developer i", "engineer i", "engineer 1",
    "0-1 year", "0-2 year", "1-2 year", "upto 2", "up to 2", "early career",
]

GENERIC_ENGINEER_TITLES = [
    "software engineer", "software developer", "backend engineer", "backend developer",
    "full stack", "fullstack", "full-stack", "sde", "platform engineer",
    "devops engineer", "machine learning engineer", "ai engineer", "ml engineer",
]

YEAR_TOKEN = r'(?:years?|yrs?|yr)\b'

RANGE_YEARS_RE = re.compile(
    rf'(\d+(?:\.\d+)?)\s*(?:-|to|–|—)\s*(\d+(?:\.\d+)?)\s*{YEAR_TOKEN}',
    re.IGNORECASE,
)
PLUS_YEARS_RE = re.compile(
    rf'(\d+(?:\.\d+)?)\s*(?:\+|plus)\s*{YEAR_TOKEN}',
    re.IGNORECASE,
)
MIN_YEARS_RE = re.compile(
    rf'(?:minimum|min\.?|at least|atleast|more than|over)\s*(\d+(?:\.\d+)?)\s*{YEAR_TOKEN}',
    re.IGNORECASE,
)
SOLO_YEARS_RE = re.compile(
    rf'(\d+(?:\.\d+)?)\s*{YEAR_TOKEN}(?:\s*(?:\+|plus))?(?:\s+(?:of\s+)?(?:relevant\s+)?(?:professional\s+)?(?:industry\s+)?(?:software\s+)?(?:work\s+)?(?:experience|exp))?',
    re.IGNORECASE,
)
EXP_PREFIX_RE = re.compile(
    rf'(?:experience|exp(?:erience)?)\s*[:\-]?\s*(\d+(?:\.\d+)?)(?:\s*(?:-|to|–|—)\s*(\d+(?:\.\d+)?))?(?:\s*{YEAR_TOKEN})?',
    re.IGNORECASE,
)
JD_EXP_RE = re.compile(
    r'(\d+(?:\.\d+)?)\s*(?:\+|plus)?\s*(?:-|to|–|—)?\s*(\d+(?:\.\d+)?)?\s*(?:\+|plus)?\s*(?:years?|yrs?|yr)\b',
    re.IGNORECASE,
)
JD_HISTORY_BEFORE_RE = re.compile(
    r'(?:for|past|last|next|over the(?: past| last)?|in the last|since|founded|'
    r'nearly|almost|about|previous|prior|spanning)\s+$',
    re.IGNORECASE,
)
MIN_JD_CHARS = 80

INDIA_PLATFORMS = {"naukri", "instahyre", "indeed"}
# "ncr" is not here: bare, it is also a company ("Join NCR Voyix"), which let
# an Atlanta posting through. It is matched by NCR_RE instead.
INDIA_MARKERS = (
    "india", "bharat", "bengaluru", "bangalore", "hyderabad", "pune", "mumbai",
    "gurugram", "gurgaon", "noida", "delhi", "new delhi", "chennai",
    "kolkata", "ahmedabad", "jaipur", "chandigarh", "kochi", "coimbatore",
    "indore", "lucknow", "mysore", "mysuru", "thane", "navi mumbai",
    "telangana", "karnataka", "maharashtra", "haryana", "uttar pradesh",
    "tamil nadu", "kerala", "west bengal", "pan india", "remote - india",
    "remote (india)", "india (remote)",
)
ABROAD_MARKERS = (
    "united states", "u.s.a", "u.s.", "usa", "america",
    "california", "san francisco", "bay area", "new york", "seattle",
    "austin", "boston", "chicago", "denver", "atlanta", "texas",
    "washington", "los angeles", "san jose", "palo alto", "mountain view",
    "united kingdom", "england", "london", "manchester", "scotland",
    "canada", "toronto", "vancouver", "ontario", "montreal",
    "germany", "berlin", "munich", "netherlands", "amsterdam",
    "australia", "sydney", "melbourne", "singapore", "dubai", "uae",
    "ireland", "dublin", "poland", "warsaw", "remote - us", "remote, us",
    "remote (us)", "us remote",
)
# Too generic to place a job on their own when read out of JD prose: "India"
# in "our teams span the US, Europe and India" says where the company is, not
# where the role is. A JD has to name an Indian city (or use a location cue,
# below) before it counts.
JD_GENERIC_INDIA = {"india", "bharat", "pan india"}
# "Delhi NCR", "NCR, India", "NCR Region", or "NCR" as the whole location.
NCR_RE = re.compile(
    r'(?<![a-z0-9])(?:delhi[\s\-/]*ncr|ncr\s*(?:[,(\-/]\s*)?(?:india|region|delhi)|^\s*ncr\s*$)(?![a-z0-9])'
)
# Phrases that say where the role itself sits. Only what follows one of these,
# within a few words, is read as the job's location from the JD.
JD_LOCATION_CUE_RE = re.compile(
    r'(?:\bbased\s+(?:in|out\s+of|at)|\blocated\s+(?:in|at)|\blocations?\s*[:\-–]|'
    r'\bjob\s+location\b|\bwork\s+location\b|\boffice\s+in|\bwork(?:ing)?\s+from|'
    r'\bposition\s+is\s+(?:in|at)|\brole\s+is\s+(?:in|at)|\bon-?site\s+(?:in|at)|'
    r'\bhybrid\s+(?:in|at|from)|\brelocate\s+to)',
    re.IGNORECASE,
)
JD_CUE_WINDOW = 60
US_STATE_RE = re.compile(
    r',\s*(AL|AK|AZ|AR|CA|CO|CT|DC|DE|FL|GA|HI|IA|ID|IL|KS|KY|LA|MA|MD|ME|'
    r'MI|MN|MO|MS|MT|NC|ND|NE|NH|NJ|NM|NV|NY|OH|OK|OR|PA|RI|SC|SD|TN|TX|'
    r'UT|VA|VT|WA|WI|WV)\b',
    re.IGNORECASE,
)
# Cities to skip outright, however well the role scores.
EXCLUDED_CITIES = (
    "kochi", "cochin", "ernakulam",
    "chennai", "madras",
)

# LinkedIn keeps listings up after the employer has closed them. The posting
# still scrapes fine — title, company, a full description — so nothing else
# here rejects it, and it sits in the queue looking applicable until you open
# it. The phrase is on the page in place of the Apply button.
CLOSED_MARKERS = (
    "no longer accepting applications",
    "not currently accepting applications",
    "not accepting applications",
    "this job is no longer available",
    "this job is no longer accepting",
    "applications are closed",
    "position has been filled",
    "posting has expired",
)

FRONTEND_MARKERS = (
    "frontend", "front-end", "front end",
    "ui engineer", "ui developer", "ui/ux", "ux engineer",
    "react developer", "react.js developer", "reactjs developer",
    "angular developer", "vue developer", "vue.js",
)
FRONTEND_KEEP = (
    "full stack", "fullstack", "full-stack", "backend", "back-end", "back end",
)


def _title_has_keyword(title_lower: str, keyword: str) -> bool:
    """Whole-word match; a keyword ending in "." ("sr.") needs no right edge."""
    left = r'(?<![a-z0-9])'
    right = '' if keyword.endswith('.') else r'(?![a-z0-9])'
    return bool(re.search(left + re.escape(keyword) + right, title_lower))


def _experience_exceeds_cap(min_y: float, max_y: float, cap: float = MAX_JD_EXPERIENCE_YEARS) -> bool:
    return min_y > cap or max_y > cap


def _pair(min_y: float, max_y: float) -> Tuple[float, float]:
    if max_y < min_y:
        return max_y, min_y
    return min_y, max_y


def _ranges_from_text(text: str) -> list:
    if not text:
        return []
    found = []
    for match in RANGE_YEARS_RE.finditer(text):
        found.append(_pair(float(match.group(1)), float(match.group(2))))
    for match in PLUS_YEARS_RE.finditer(text):
        # "N+ years" states a minimum, nothing more. It used to be read as
        # N..N+3, so "1+ years" parsed as up to 4 and failed a cap of 2.
        min_y = float(match.group(1))
        found.append((min_y, min_y))
    for match in MIN_YEARS_RE.finditer(text):
        min_y = float(match.group(1))
        phrase = match.group(0).lower()
        if "more than" in phrase or phrase.startswith("over"):
            found.append((min_y + 0.1, min_y + 0.1))
        else:
            found.append((min_y, min_y))
    for match in EXP_PREFIX_RE.finditer(text):
        min_y = float(match.group(1))
        max_y = float(match.group(2)) if match.group(2) else min_y
        found.append(_pair(min_y, max_y))
    if not found:
        for match in SOLO_YEARS_RE.finditer(text):
            val = float(match.group(1))
            found.append((val, val))
    return found


def parse_experience_years(text: str) -> Optional[Tuple[float, float]]:
    ranges = _ranges_from_text(text or "")
    return ranges[0] if ranges else None


def _jd_experience_ranges(jd_text: str) -> list:
    found = []
    text = jd_text or ""
    for match in JD_EXP_RE.finditer(text):
        before = text[max(0, match.start() - 48):match.start()]
        if JD_HISTORY_BEFORE_RE.search(before):
            continue
        min_y = float(match.group(1))
        # A bare "N+" is a minimum: judged by N alone (see `_ranges_from_text`).
        max_y = float(match.group(2)) if match.group(2) else min_y
        if min_y > 15:
            continue
        found.append(_pair(min_y, max_y))
    return found


def is_job_experience_valid(
    job_data: dict,
    max_years: float = MAX_JD_EXPERIENCE_YEARS,
    require_jd: bool = True,
) -> Tuple[bool, str]:
    # The caller's cap is honoured: every caller passes settings.experience_years
    # (EXPERIENCE_YEARS, 2 today, shown as the "hard limit" at startup), so the
    # effective cap is unchanged. This used to ignore the argument outright.
    cap = MAX_JD_EXPERIENCE_YEARS if max_years is None else float(max_years)
    title =(job_data.get("title") or "").strip()
    title_lower = title.lower()
    jd_text = (job_data.get("jd_text") or "").strip()

    if title and INTERN_PATTERN.search(title):
        return False, f"Title mentions intern/internship: '{title}'"

    for keyword in EXCLUDED_TITLE_KEYWORDS:
        if _title_has_keyword(title_lower, keyword):
            return False, f"Title contains senior/excluded keyword: '{keyword}' in '{title}'"

    for pattern in EXCLUDED_TITLE_REGEXES:
        if re.search(pattern, title_lower):
            return False, f"Title matches excluded level pattern: '{pattern}' in '{title}'"

    ranges = []
    for blob in (title, job_data.get("experience_required", "") or ""):
        ranges.extend(_ranges_from_text(blob))
    ranges.extend(_jd_experience_ranges(jd_text))

    for min_y, max_y in ranges:
        if _experience_exceeds_cap(min_y, max_y, cap):
            return False, (
                f"Requires more than {cap:g} years of experience "
                f"(parsed {min_y:g}-{max_y:g})"
            )

    platform = (job_data.get("platform") or "").lower()
    if require_jd and platform == "linkedin" and len(jd_text) < MIN_JD_CHARS:
        return False, "LinkedIn JD missing; cannot verify experience"

    return True, "Passed experience boundaries check"


def is_career_listing_eligible(job_data: dict) -> Tuple[bool, str]:
    title_lower = job_data.get("title", "").lower()
    jd_text = (job_data.get("jd_text") or "").strip()

    if len(jd_text) >= 200:
        return True, "Has detailed JD"

    if any(sig in title_lower for sig in ENTRY_TITLE_SIGNALS):
        return True, "Entry-level signals in title"

    if any(g in title_lower for g in GENERIC_ENGINEER_TITLES):
        return False, (
            "Generic career-site listing without entry-level signals or JD: "
            f"'{job_data.get('title', '')}'"
        )

    return True, "Specific non-generic title"


def is_pure_frontend_role(title: str, jd_text: str = "") -> bool:
    t = (title or "").lower()
    if "react native" in t:
        return False
    if any(k in t for k in FRONTEND_KEEP):
        return False
    if any(m in t for m in FRONTEND_MARKERS):
        return True
    jd = (jd_text or "")[:400].lower()
    if jd and any(m in jd[:200] for m in ("this is a frontend", "this is a front-end", "front-end only")):
        return True
    return False


def _contains_marker(blob: str, marker: str) -> bool:
    return bool(re.search(rf'(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])', blob))


def _india_in(blob: str, markers=INDIA_MARKERS) -> bool:
    return any(_contains_marker(blob, m) for m in markers) or bool(NCR_RE.search(blob))


def _abroad_in(blob: str) -> bool:
    return any(_contains_marker(blob, m) for m in ABROAD_MARKERS)


def _jd_cued_places(jd: str) -> Tuple[bool, bool]:
    """(India, abroad) as named right after a location cue in the JD."""
    india = abroad = False
    for cue in JD_LOCATION_CUE_RE.finditer(jd):
        window = jd[cue.end():cue.end() + JD_CUE_WINDOW]
        india = india or _india_in(window)
        abroad = abroad or _abroad_in(window) or bool(US_STATE_RE.search(window))
    return india, abroad


# Europe mode: the UK, Ireland, Germany, and the Netherlands. "ireland" is not
# in the UK list; "northern ireland" is masked before the Ireland markers run,
# so it counts as the UK only.
_UK_MARKERS = (
    "united kingdom", "great britain", "northern ireland",
    "england", "scotland", "wales",
    "london", "manchester", "edinburgh", "birmingham", "bristol", "cambridge",
    "glasgow", "leeds", "uk",
)
_IRELAND_MARKERS = ("republic of ireland", "dublin", "cork", "galway", "ireland")
_GERMANY_MARKERS = ("germany", "berlin", "munich", "hamburg", "frankfurt")
_NETHERLANDS_MARKERS = (
    "netherlands", "holland", "amsterdam", "rotterdam", "utrecht",
    "eindhoven", "the hague",
)
EUROPE_COUNTRIES = ("united kingdom", "ireland", "germany", "netherlands")
_EUROPE_MARKER_SET = frozenset(
    _UK_MARKERS + _IRELAND_MARKERS + _GERMANY_MARKERS + _NETHERLANDS_MARKERS
)
# A city, not the country name. "teams span the UK and India" is not a UK role.
_EUROPE_CITIES = (
    "london", "manchester", "edinburgh", "birmingham", "bristol", "cambridge",
    "glasgow", "leeds", "dublin", "cork", "galway", "berlin", "munich",
    "hamburg", "frankfurt", "amsterdam", "rotterdam", "utrecht", "eindhoven",
    "the hague",
)
# Places the Europe search does not want, and that ABROAD_MARKERS does not
# already name. Paris would otherwise look like "no country" and fall through.
_OTHER_COUNTRY_MARKERS = (
    "france", "paris", "spain", "madrid", "barcelona", "italy", "milan", "rome",
    "poland", "warsaw", "sweden", "stockholm", "belgium", "brussels",
    "austria", "vienna", "switzerland", "zurich", "portugal", "lisbon",
    "denmark", "copenhagen", "finland", "helsinki", "prague", "czechia",
    "luxembourg", "norway", "oslo", "hungary", "budapest", "greece", "athens",
    "romania", "bucharest",
)
# What LinkedIn and the big-tech boards are asked for. Short on purpose:
# each city multiplies the request count.
EUROPE_SEARCH_LOCATIONS = [
    "London", "Manchester", "Dublin", "Berlin", "Munich", "Amsterdam",
]
# Autopilot scores a location by matching these. Anything the allow-list
# accepts has to hit one of them, or Edinburgh is "out of scope".
EUROPE_PREFERRED_LOCATIONS = [
    "United Kingdom", "UK", "England", "Scotland", "Wales", "Northern Ireland",
    "London", "Manchester", "Edinburgh", "Birmingham", "Bristol", "Cambridge",
    "Glasgow", "Leeds",
    "Ireland", "Dublin", "Cork", "Galway",
    "Germany", "Berlin", "Munich", "Hamburg", "Frankfurt",
    "Netherlands", "Holland", "Amsterdam", "Rotterdam", "Utrecht", "Eindhoven",
    "The Hague",
]

# A sentence that refuses a visa. Neutral mentions ("sponsorship") are not
# here — a silent description is kept. "or we will sponsor" in the same
# sentence cancels the refusal.
_VISA_REFUSAL = (
    "no visa sponsorship",
    "visa sponsorship is not available",
    "visa sponsorship is not offered",
    "visa sponsorship not available",
    "sponsorship is not available",
    "sponsorship is not offered",
    "do not offer sponsorship",
    "does not offer sponsorship",
    "will not sponsor",
    "cannot sponsor",
    "can't sponsor",
    "unable to sponsor",
    "unable to offer sponsorship",
    "not able to sponsor",
    "not willing to sponsor",
    "cannot provide sponsorship",
    "do not provide sponsorship",
    "does not provide sponsorship",
    "no sponsorship",
    "must already have the right to work",
    "must have the right to work",
    "must already be authorised to work",
    "must already be authorized to work",
    "must be authorised to work",
    "must be authorized to work",
    "must have unrestricted right to work",
    "existing right to work",
    "must hold the right to work",
    "right to work is required",
)
_VISA_OFFER = (
    "we will sponsor",
    "we can sponsor",
    "will sponsor",
    "can sponsor",
    "visa sponsorship is available",
    "visa sponsorship available",
    "sponsorship is available",
    "sponsorship available",
    "offer visa sponsorship",
    "provide visa sponsorship",
    "provide sponsorship",
    "sponsorship can be provided",
    "happy to sponsor",
    "willing to sponsor",
)


def europe_countries_in(blob: str) -> set:
    """Which of the four target countries `blob` names."""
    low = (blob or "").lower()
    found = set()
    if any(_contains_marker(low, m) for m in _UK_MARKERS):
        found.add("united kingdom")
    # "northern ireland" contains the word "ireland".
    masked = low.replace("northern ireland", " ")
    if any(_contains_marker(masked, m) for m in _IRELAND_MARKERS):
        found.add("ireland")
    if any(_contains_marker(low, m) for m in _GERMANY_MARKERS):
        found.add("germany")
    if any(_contains_marker(low, m) for m in _NETHERLANDS_MARKERS):
        found.add("netherlands")
    return found


def europe_location_ok(location: str) -> bool:
    """The location names one of the four countries and no other."""
    blob = location or ""
    return bool(europe_countries_in(blob)) and not _outside_europe(blob)


def _outside_europe(blob: str) -> bool:
    """A country other than the four. Target markers themselves do not count."""
    low = (blob or "").lower()
    if _india_in(low):
        return True
    for marker in ABROAD_MARKERS:
        if marker in _EUROPE_MARKER_SET:
            continue
        if _contains_marker(low, marker):
            return True
    if any(_contains_marker(low, m) for m in _OTHER_COUNTRY_MARKERS):
        return True
    return bool(US_STATE_RE.search(blob or ""))


def _europe_city_in(blob: str) -> bool:
    return any(_contains_marker(blob, city) for city in _EUROPE_CITIES)


def _jd_cued_europe(jd: str) -> Tuple[bool, bool]:
    """(target country, somewhere else) named right after a location cue."""
    europe = other = False
    for cue in JD_LOCATION_CUE_RE.finditer(jd):
        window = jd[cue.end():cue.end() + JD_CUE_WINDOW]
        europe = europe or bool(europe_countries_in(window))
        other = other or _outside_europe(window)
    return europe, other


def _sponsorship_offered(sentence: str) -> bool:
    """An offer of sponsorship, not the word sitting inside 'will not sponsor'."""
    for phrase in _VISA_OFFER:
        start = 0
        while True:
            at = sentence.find(phrase, start)
            if at < 0:
                break
            # "cannot provide sponsorship" contains the offer phrase.
            before = sentence[max(0, at - 12):at]
            if not re.search(r"(?:\bcannot|\bnot)\s+$", before):
                return True
            start = at + len(phrase)
    return False


def visa_refused(jd_text: str) -> Tuple[bool, str]:
    """A refusal to sponsor, unless that same sentence also offers one."""
    text = re.sub(r"\s+", " ", (jd_text or "").lower()).strip()
    if not text:
        return False, ""
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    sentences = [p.strip() for p in parts if p.strip()] or [text]
    for sentence in sentences:
        refusal = next((p for p in _VISA_REFUSAL if p in sentence), "")
        if refusal and not _sponsorship_offered(sentence):
            return True, f"Visa refused: '{refusal}'"
    return False, ""


def is_europe_location_allowed(job_data: dict) -> Tuple[bool, str]:
    """UK, Ireland, Germany, or the Netherlands, and nowhere else."""
    location = (job_data.get("location") or "").strip()
    title = job_data.get("title") or ""
    jd = (job_data.get("jd_text") or "")[:1200].lower()
    loc = location.lower()
    shown = location or title

    here = europe_countries_in(loc)
    elsewhere = _outside_europe(loc)
    if here and not elsewhere:
        return True, "UK, Ireland, Germany, or Netherlands"
    if elsewhere:
        return False, f"Outside UK, Ireland, Germany, and the Netherlands: '{shown}'"

    cue_here, cue_elsewhere = _jd_cued_europe(jd)
    if cue_here and not cue_elsewhere:
        return True, "Europe location (from JD)"
    if cue_elsewhere:
        return False, f"Outside Europe (from JD): '{shown}'"
    # A city in the description, with no other country. The country name alone
    # ("teams span the UK") does not place the role.
    if _europe_city_in(jd) and not _outside_europe(jd):
        return True, "Europe location (from JD)"
    if not loc:
        return False, "Missing location"
    if "remote" in loc or "work from home" in loc or loc in {"wfh", "anywhere", "multiple locations"}:
        return False, f"Remote outside UK, Ireland, Germany, and the Netherlands: '{location}'"
    return False, f"Outside UK, Ireland, Germany, and the Netherlands: '{location}'"


def is_location_allowed(job_data: dict, region: str = "india") -> Tuple[bool, str]:
    if region == "europe":
        return is_europe_location_allowed(job_data)
    return _india_location_allowed(job_data)


def _india_location_allowed(job_data: dict) -> Tuple[bool, str]:
    location = (job_data.get("location") or "").strip()
    title = job_data.get("title") or ""
    jd = (job_data.get("jd_text") or "")[:1200].lower()
    platform = (job_data.get("platform") or "").lower()

    # Cities the user will not relocate to. Matched on the location and title
    # only, never the JD — a Bengaluru posting that merely mentions a Chennai
    # office in its boilerplate is still a Bengaluru job.
    where = f"{location} {title}".lower()
    for city in EXCLUDED_CITIES:
        if _contains_marker(where, city):
            return False, f"Excluded city: '{location or title}'"

    # The location field (and title) decide first. The JD used to be pooled in
    # with them and an India hit anywhere won, so "Seattle, WA" whose
    # boilerplate said "our teams span the US, Europe and India" came through
    # as an India job.
    loc_india = _india_in(where) or bool(NCR_RE.search(location.lower()))
    loc_abroad = _abroad_in(where) or bool(US_STATE_RE.search(location))
    if loc_india:
        # Includes multi-city listings such as "Bengaluru; Seattle, WA".
        return True, "India location"
    if loc_abroad:
        return False, f"Non-India location: '{location or title}'"

    # Location empty or uninformative ("Remote", "Multiple locations"): only
    # now does the JD get a say, and only through India-specific phrasing — a
    # location cue ("based in Pune"), or an Indian city with no foreign one.
    cue_india, cue_abroad = _jd_cued_places(jd)
    if cue_india:
        return True, "India location (from JD)"
    if cue_abroad:
        return False, f"Non-India location (from JD): '{location or title}'"
    jd_city = _india_in(jd, tuple(m for m in INDIA_MARKERS if m not in JD_GENERIC_INDIA))
    jd_abroad = _abroad_in(jd)
    if jd_city and not jd_abroad:
        return True, "India location (from JD)"
    if jd_abroad and not jd_city:
        return False, f"Non-India location: '{location or title}'"
    if platform in INDIA_PLATFORMS:
        return True, "India job board"
    loc = location.lower()
    if not loc:
        return False, "Missing location"
    if "remote" in loc or "work from home" in loc or loc in {"wfh", "anywhere"}:
        return False, f"Remote outside India: '{location}'"
    return False, f"Non-India location: '{location}'"


def is_closed_posting(job_data: dict) -> Tuple[bool, str]:
    """Has the employer stopped taking applications for this listing?"""
    blob = " ".join([
        job_data.get("jd_text") or "",
        job_data.get("title") or "",
        job_data.get("apply_note") or "",
    ]).lower()
    blob = re.sub(r"\s+", " ", blob)
    for marker in CLOSED_MARKERS:
        if marker in blob:
            return True, f"Closed listing: '{marker}'"
    return False, ""


def matches_region(job_data: dict, region: str = "india") -> bool:
    """Location (and, in Europe, a visa refusal) for rows already stored."""
    ok, _ = is_location_allowed(job_data, region=region)
    if not ok:
        return False
    if region == "europe":
        refused, _ = visa_refused(job_data.get("jd_text") or "")
        if refused:
            return False
    return True


def job_in_region(job, region: str = "india") -> bool:
    return matches_region(
        {
            "location": job.location or "",
            "title": job.title or "",
            "jd_text": job.jd_text or "",
            "platform": job.platform or "",
        },
        region,
    )


def passes_hard_filters(
    job_data: dict,
    max_years: float = MAX_JD_EXPERIENCE_YEARS,
    require_jd: bool = True,
    region: str = "india",
) -> Tuple[bool, str]:
    title = job_data.get("title") or ""
    jd = job_data.get("jd_text") or ""
    closed, why = is_closed_posting(job_data)
    if closed:
        return False, why
    if is_pure_frontend_role(title, jd):
        return False, f"Frontend role: '{title}'"
    ok, reason = is_location_allowed(job_data, region=region)
    if not ok:
        return False, reason
    if region == "europe":
        refused, why = visa_refused(jd)
        if refused:
            return False, why
    return is_job_experience_valid(job_data, max_years=max_years, require_jd=require_jd)
