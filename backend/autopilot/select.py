"""
Pick the N jobs worth applying to, and say why.

The selection criteria are the user's: a good company, a well-known company, the
right role, the right location. Each is scored separately so the reason a job
was chosen — or passed over — is legible rather than a single opaque number.

Two rules matter more than the scoring:

  * A job that cannot be applied to is not a candidate. `backend.applier.routing`
    decides that, and it is the difference between a list that looks good and a
    list that converts: only 5% of a LinkedIn-sourced pool has a reachable
    application form.
  * Staffing agencies and body shops are excluded outright. They dominate a
    naive scan — the current pool is a third Infosys, Capgemini, Cognizant and
    consulting middlemen — and they are not what the user is aiming at.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, Iterable, List, Optional, Sequence

from backend.applier.routing import Route, Routing, route_job

# ── Company tiers ────────────────────────────────────────────────────────────


class Tier(IntEnum):
    """How much a name on the resume is worth, highest first."""

    FAANG = 4          # the set the user named explicitly
    ELITE = 3          # global product companies with a comparable hiring bar
    STRONG = 2         # well-known product companies, incl. Indian unicorns
    KNOWN = 1          # a real product company, recognisable
    UNKNOWN = 0        # everything not on a list
    EXCLUDED = -1      # staffing, consultancies, body shops


FAANG = {
    "google", "alphabet", "meta", "facebook", "amazon", "apple", "netflix",
    "microsoft", "nvidia",
}

ELITE = {
    "openai", "anthropic", "stripe", "databricks", "airbnb", "uber", "linkedin",
    "salesforce", "adobe", "atlassian", "snowflake", "coinbase", "datadog",
    "cloudflare", "mongodb", "confluent", "hashicorp", "figma", "canva",
    "dropbox", "pinterest", "reddit", "discord", "twilio", "elastic", "gitlab",
    "github", "palantir", "roblox", "spotify", "square", "block", "robinhood",
    "plaid", "notion", "vercel", "samsara", "rippling",
}

STRONG = {
    "flipkart", "swiggy", "zomato", "razorpay", "cred", "phonepe", "paytm",
    "meesho", "zerodha", "groww", "postman", "browserstack", "freshworks",
    "zoho", "innovaccer", "hackerrank", "druva", "inmobi", "mixpanel", "okta",
    "zscaler", "nutanix", "arista", "palo alto", "vmware", "intel", "qualcomm",
    "cisco", "micron", "texas instruments", "sap", "oracle", "ibm", "dell",
    "goldman sachs", "morgan stanley", "jpmorgan", "j.p. morgan", "visa",
    "mastercard", "paypal", "walmart", "target", "expedia", "booking",
    "thoughtworks", "tower research", "d. e. shaw", "de shaw", "optiver",
    "jane street", "millennium", "graviton", "quadeye", "rubrik", "pure storage",
}

KNOWN = {
    "observe.ai", "yellow.ai", "darwinbox", "whatfix", "hevo", "harness",
    "sigmoid", "turing", "chime", "brex", "affirm", "instacart", "coursera",
    "asana", "airtable", "amplitude", "fastly", "new relic", "sumo logic",
    "cockroach labs", "scale ai", "crunchyroll", "agoda", "adyen", "quince",
    "tripadvisor", "lyft", "sofi", "ripple", "khan academy", "deutsche bank",
    "bnp paribas", "nielseniq", "wex", "gojek", "shadowfax", "capillary",
}

# Names that mark an employer as an intermediary rather than the employer.
# These exclude whatever else the name says. Stems ("consultanc", "recruit",
# "outsourc") match at a word start; everything else is a whole word, so
# "atos" no longer excludes Kratos.
EXCLUDED_STEMS = ("staffing", "consultanc", "recruit", "manpower", "placement",
                  "resourcing", "outsourc")
EXCLUDED_MARKERS = (
    "consulting services", "hr solutions", "talent solutions",
    "infosys", "wipro", "cognizant", "capgemini", "accenture",
    "tech mahindra", "hcl", "hcltech", "ltimindtree", "mindtree", "mphasis", "zensar",
    "ntt data", "birlasoft", "coforge", "hexaware", "persistent systems",
    "cgi", "dxc", "atos", "virtusa", "syntel", "iGate", "quess", "randstad",
    "adecco", "teamlease", "huntingcube", "crescendo", "antal", "michael page",
)
# Legal-entity boilerplate that body shops happen to use, and so do the Indian
# subsidiaries of the companies we want: "Goldman Sachs Services Pvt Ltd",
# "Swiggy - Bundl Technologies Pvt Ltd", "Amazon Hiring". These exclude only a
# name that matched no tier.
GENERIC_EXCLUDED_MARKERS = ("hiring", "services pvt", "technologies pvt ltd",
                            "solutions pvt", "infotech")

# Tier names that are also ordinary words ("Square Yards", "Notion Press",
# "Target"), accepted only as the whole name — after legal suffixes are
# stripped — or as one whole part of it ("Dreamplug Technologies (CRED)").
AMBIGUOUS_NAMES = {
    "square", "block", "notion", "elastic", "discord", "plaid", "target",
    "booking", "visa", "millennium", "turing", "chime", "harness", "ripple",
    "affirm", "sigmoid", "amplitude", "cred",
}

_SUFFIX_RE = re.compile(
    r"\b(pvt|private|ltd|limited|inc|llc|llp|corp|corporation|co|company|plc|nv|"
    r"gmbh|ag|bv|pte|holdings|group|com|technologies|technology|labs|india|"
    r"software|systems|solutions)\b"
)


def _clean(name: str) -> str:
    """Lowercase, punctuation to spaces, initialisms joined: "D. E. Shaw" -> "de shaw"."""
    text = (name or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    # Runs of single letters are an initialism split by the dots just removed.
    text = re.sub(r"\b[a-z](?: [a-z]\b)+", lambda m: m.group(0).replace(" ", ""), text)
    return re.sub(r"\s+", " ", text).strip()


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", _SUFFIX_RE.sub(" ", _clean(name))).strip()


def _has_word(text: str, word: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text))


def _prep(names: Iterable[str]) -> List[str]:
    """Normalize a name list the same way company names are, at load."""
    return sorted({_norm(n) for n in names if _norm(n)})


_FAANG, _ELITE, _STRONG, _KNOWN = (_prep(s) for s in (FAANG, ELITE, STRONG, KNOWN))
_AMBIGUOUS = set(_prep(AMBIGUOUS_NAMES))
_EXCLUDED_STEMS = tuple(_clean(m) for m in EXCLUDED_STEMS)
_EXCLUDED_MARKERS = tuple(_clean(m) for m in EXCLUDED_MARKERS)
_GENERIC_EXCLUDED = tuple(_clean(m) for m in GENERIC_EXCLUDED_MARKERS)


def _in_set(name: str, names: Iterable[str]) -> bool:
    """Whole-name or whole-word containment, never a loose substring."""
    clean = _norm(name)
    parts = {_norm(p) for p in re.split(r"[-–—|/(),]", name or "")}
    for candidate in names:
        if clean == candidate or candidate in parts:
            return True
        if candidate in _AMBIGUOUS:
            continue
        if _has_word(clean, candidate):
            return True
    return False


def company_tier(company: str) -> Tier:
    raw = _clean(company)
    if any(re.search(rf"(?<![a-z0-9]){re.escape(s)}", raw) for s in _EXCLUDED_STEMS):
        return Tier.EXCLUDED
    if any(_has_word(raw, m) for m in _EXCLUDED_MARKERS):
        return Tier.EXCLUDED
    for tier, names in ((Tier.FAANG, _FAANG), (Tier.ELITE, _ELITE),
                        (Tier.STRONG, _STRONG), (Tier.KNOWN, _KNOWN)):
        if _in_set(company, names):
            return tier
    if any(_has_word(raw, m) for m in _GENERIC_EXCLUDED):
        return Tier.EXCLUDED
    return Tier.UNKNOWN


# ── Role and location fit ────────────────────────────────────────────────────

ROLE_STRONG = ("backend", "back end", "software engineer", "software development engineer",
               "sde", "full stack", "fullstack", "platform engineer", "distributed systems",
               "infrastructure engineer", "systems engineer", "server")
ROLE_OK = ("software", "engineer", "developer", "programmer")
# Whole words (see `_bad_role`). As bare substrings "intern" zeroed "Internal
# Tools", "International Payments" and "Internet Infrastructure", and "sales"
# zeroed "Salesforce Platform".
ROLE_BAD = ("frontend only", "ui/ux", "designer", "qa", "test engineer", "sdet",
            "recruiter", "intern", "interns", "internship",
            "data entry", "analyst", "manager", "director", "principal", "staff",
            "senior staff", "lead", "architect", "head of", "vp")
# Functions that disqualify a title only when it is not also plainly a
# software-engineering title: "Sales Engineer" and "Technical Support
# Engineer" are out, "Software Engineer, Customer Support Platform" is in.
ROLE_BAD_UNLESS_SWE = ("support", "sales", "marketing", "hr")
SWE_TITLES = ("software engineer", "software development engineer", "software developer",
              "backend engineer", "backend developer", "sde", "full stack engineer",
              "full stack developer", "platform engineer")


def _bad_role(t: str) -> bool:
    if any(_has_word(t, bad) for bad in ROLE_BAD):
        return True
    if any(_has_word(t, bad) for bad in ROLE_BAD_UNLESS_SWE):
        return not any(_has_word(t, swe) for swe in SWE_TITLES)
    return False

# "ii" belongs here with "iii" and "iv": `filter_utils` already rejects a
# "Software Engineer II" title outright, so a job carrying one into selection
# was scoring as though it were entry level.
SENIOR_MARKERS = ("senior", "sr.", "sr ", "staff", "principal", "lead", "manager",
                  "director", "head", "architect", "vp", "ii", "iii", "iv")


def role_score(title: str) -> float:
    t = (title or "").lower()
    if _bad_role(t):
        return 0.0
    if any(s in t for s in ROLE_STRONG):
        base = 30.0
    elif any(s in t for s in ROLE_OK):
        base = 18.0
    else:
        return 0.0
    # A 2-year profile should not be spending applications on senior reqs.
    if any(re.search(rf"(?<![a-z]){re.escape(m.strip())}(?![a-z])", t) for m in SENIOR_MARKERS):
        base -= 14.0
    return max(0.0, base)


def location_score(location: str, preferred: Sequence[str]) -> float:
    loc = (location or "").lower()
    if not loc:
        return 4.0                      # unknown, not disqualifying
    for want in preferred:
        w = (want or "").strip().lower()
        if w and re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", loc):
            return 15.0
    if "remote" in loc:
        return 12.0
    if "india" in loc:
        return 9.0
    return 0.0


# ── Selection ────────────────────────────────────────────────────────────────

TIER_POINTS = {
    Tier.FAANG: 45.0,
    Tier.ELITE: 38.0,
    Tier.STRONG: 28.0,
    Tier.KNOWN: 16.0,
    Tier.UNKNOWN: 4.0,
}

ROUTE_POINTS = {Route.AUTO: 12.0, Route.LOGIN: 4.0, Route.MANUAL: 0.0}


@dataclass
class Candidate:
    job_id: str
    title: str
    company: str
    location: str
    url: str
    tier: Tier
    routing: Routing
    score: float
    reasons: List[str] = field(default_factory=list)
    rejected: str = ""

    @property
    def ok(self) -> bool:
        return not self.rejected

    def as_dict(self) -> Dict[str, object]:
        return {
            "job_id": self.job_id,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
            "apply_url": self.routing.apply_url,
            "tier": self.tier.name,
            "tier_value": int(self.tier),
            "route": self.routing.route.value,
            "route_reason": self.routing.reason,
            "ats": self.routing.ats,
            "score": round(self.score, 1),
            "reasons": self.reasons,
            "rejected": self.rejected,
        }


def evaluate(job, preferred_locations: Sequence[str]) -> Candidate:
    """Score one job against every criterion, keeping the reasoning."""
    tier = company_tier(job.company or "")
    routing = route_job(job)
    reasons: List[str] = []
    rejected = ""

    if tier is Tier.EXCLUDED:
        rejected = "staffing agency or IT services company"

    r_score = role_score(job.title or "")
    if not rejected and r_score <= 0:
        rejected = "title is not an eligible engineering role"

    l_score = location_score(job.location or "", preferred_locations)
    if not rejected and l_score <= 0:
        rejected = f"location not in scope ({job.location or 'unknown'})"

    if not rejected and routing.route is Route.MANUAL:
        rejected = "no reachable application form"

    t_points = TIER_POINTS.get(tier, 0.0)
    score = t_points + r_score + l_score + ROUTE_POINTS.get(routing.route, 0.0)
    # Keep a little of the scan's own relevance signal without letting it dominate.
    score += min(float(getattr(job, "relevance_score", 0) or 0), 100.0) * 0.08

    if tier >= Tier.STRONG:
        reasons.append(f"{tier.name.lower()} company")
    if r_score >= 30:
        reasons.append("strong role match")
    elif r_score > 0:
        reasons.append("role match")
    if l_score >= 15:
        reasons.append("preferred location")
    elif l_score >= 12:
        reasons.append("remote")
    if routing.route is Route.AUTO:
        reasons.append(f"auto-appliable ({routing.ats})")
    elif routing.route is Route.LOGIN:
        reasons.append(f"needs {routing.ats} sign-in")

    return Candidate(
        job_id=job.id,
        title=job.title or "",
        company=job.company or "",
        location=job.location or "",
        url=job.url or "",
        tier=tier,
        routing=routing,
        score=score,
        reasons=reasons,
        rejected=rejected,
    )


def select(
    jobs: Iterable,
    preferred_locations: Sequence[str],
    limit: int = 30,
    min_tier: Tier = Tier.UNKNOWN,
    auto_only: bool = False,
) -> tuple[List[Candidate], List[Candidate]]:
    """
    Rank every job and return (chosen, passed_over).

    Both halves are returned because the ones left out are how the user tells
    whether the criteria are doing what they meant.
    """
    evaluated = [evaluate(job, preferred_locations) for job in jobs]

    eligible = [c for c in evaluated if c.ok and c.tier >= min_tier]
    if auto_only:
        eligible = [c for c in eligible if c.routing.route is Route.AUTO]

    eligible.sort(key=lambda c: (-c.score, -int(c.tier), c.company.lower()))
    chosen = eligible[:limit]
    chosen_ids = {c.job_id for c in chosen}
    passed = [c for c in evaluated if c.job_id not in chosen_ids]
    passed.sort(key=lambda c: -c.score)
    return chosen, passed
