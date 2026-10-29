"""Map form labels to applicant profile values. Deterministic — no LLM."""
from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Dict, List, Optional, Tuple

from backend.applier.profile import ApplicantProfile, Education

# Longer aliases first so "first name" wins over "name".
FIELD_ALIASES: List[Tuple[str, Tuple[str, ...]]] = [
    ("first_name", ("first name", "firstname", "given name", "legal first", "preferred first")),
    ("last_name", ("last name", "lastname", "surname", "family name", "legal last")),
    ("full_name", ("full name", "legal name", "applicant name", "your name", "candidate name",
                   "preferred name")),
    ("email", ("email address", "e-mail", "email")),
    # Must precede "phone": Workday asks for a *device type* ("Mobile"), not a number.
    ("phone_device_type", ("phone device type", "phone type", "device type")),
    ("phone", ("phone number", "mobile number", "telephone", "mobile phone", "cell phone", "phone", "mobile", "cell")),
    ("phone_country", ("phone country", "country code", "dialing code")),
    # Workday's Websites step asks under a "Social Network URLs" heading and
    # phrases the question as prose ("Please provide your LinkedIn profile"),
    # neither of which contains a bare "linkedin url".
    ("linkedin", ("linkedin url", "linkedin profile link", "linkedin profile", "linkedin",
                  "social network url", "social network")),
    ("github", ("github url", "github profile", "github", "git hub")),
    ("portfolio", ("portfolio url", "personal website", "personal url", "website url", "portfolio", "website")),
    ("salary_expectation", (
        "salary expectation", "expected salary", "expected ctc", "current ctc",
        "current/last base salary", "current last base salary", "base salary",
        "total cash entitlement", "total cash", "compensation", "desired salary", "pay expectation",
    )),
    ("notice_period", ("notice period", "notice")),
    ("address_line1", ("address line 1", "street address", "address 1", "home address")),
    ("address_line2", ("address line 2", "apartment", "suite", "unit")),
    ("city", ("city", "town")),
    ("state", ("state / province", "state/province", "province", "region", "state")),
    ("postal_code", ("postal code", "zip code", "zip/postal", "pincode", "pin code", "zip")),
    ("country", ("country / region", "country/region", "country")),
    ("location", ("current location", "location", "city, state", "where are you based")),
    ("current_company", ("current company", "current employer", "company name", "employer", "organization")),
    ("current_title", ("current title", "job title", "headline", "most recent title")),
    ("years_experience", ("years of professional experience", "years of work experience",
                          "years of relevant experience", "total years of experience",
                          "years of experience", "years experience", "total experience",
                          "total years")),
    ("earliest_start", ("earliest start", "start date", "available from", "availability date", "when can you start", "date available")),
    ("cover_letter", ("cover letter", "additional information", "additional details", "comments", "message to hiring")),
    ("school", ("school name", "university", "college", "institution", "school")),
    ("degree", ("degree", "qualification")),
    ("major", ("major", "field of study", "discipline", "specialization")),
    ("skills", ("technical skills", "key skills", "relevant skills", "core skills",
                "primary skills", "skill set", "skills")),
    ("how_heard", ("how did you hear", "where did you hear", "how did you find", "referral source", "source")),
    ("gpa", ("cgpa", "gpa", "grade point")),
]

YES_TRUE = {"yes", "true", "y", "1"}

# Acceptable substitutes when a dropdown does not offer the profile's answer,
# most preferred first. Workday's Phone Device Type taxonomy is per tenant:
# some offer "Mobile", others only "Phone" and "Main", and a field skipped for
# "no matching option" is a required field left blank at submit time.
OPTION_FALLBACKS: Dict[str, Tuple[str, ...]] = {
    "phone_device_type": ("Mobile", "Cell", "Cell Phone", "Mobile Phone", "Phone", "Main", "Home"),
}

# British spellings are not a variant we can skip: half of the Workday tenants
# aimed at India write "authorised", and without these the label falls through
# to the "country"/"location" field aliases and answers a yes/no question with
# an address.
WORK_AUTH_NEEDLES: Tuple[str, ...] = (
    "legally authorized", "legally authorised", "authorized to work", "authorised to work",
    "eligible to work", "right to work", "work authorization", "work authorisation",
    "legal right to work", "work permit", "work visa",
)
# "require a sponsorship" and "require company sponsorship" both miss an exact
# "require sponsorship", so the bare noun is the last needle here.
SPONSORSHIP_NEEDLES: Tuple[str, ...] = (
    "require sponsorship", "require a sponsorship", "need sponsorship", "need a sponsorship",
    "visa sponsorship", "require visa", "future sponsorship", "immigration sponsorship",
    "company sponsorship", "sponsorship for employment", "sponsorship",
)

# Countries a work-authorisation question may name, canonical name first.
# "the us"/"in us" rather than a bare "us": "tell us", "join us" and "sponsor
# us" are pronouns, not a country.
COUNTRY_NAMES: Dict[str, Tuple[str, ...]] = {
    "india": ("india", "bharat"),
    "united states": ("united states", "united states of america", "usa", "u.s.a", "u.s",
                      "the us", "in us"),
    "united kingdom": ("united kingdom", "the uk", "in uk", "uk", "u.k", "great britain",
                       "britain", "england", "scotland", "wales", "northern ireland"),
    "ireland": ("ireland", "republic of ireland"),
    "canada": ("canada",), "singapore": ("singapore",), "germany": ("germany",),
    "australia": ("australia",), "new zealand": ("new zealand",),
    "netherlands": ("netherlands", "the netherlands", "holland"),
    "france": ("france",), "spain": ("spain",), "italy": ("italy",), "portugal": ("portugal",),
    "poland": ("poland",), "switzerland": ("switzerland",), "sweden": ("sweden",),
    "norway": ("norway",), "denmark": ("denmark",), "finland": ("finland",),
    "belgium": ("belgium",), "austria": ("austria",), "luxembourg": ("luxembourg",),
    "czech republic": ("czech republic", "czechia"), "romania": ("romania",),
    "european union": ("european union", "eu", "eea"),
    "israel": ("israel",), "united arab emirates": ("united arab emirates", "uae"),
    "saudi arabia": ("saudi arabia",), "qatar": ("qatar",),
    "japan": ("japan",), "china": ("china",), "hong kong": ("hong kong",),
    "taiwan": ("taiwan",), "south korea": ("south korea", "korea"),
    "indonesia": ("indonesia",), "malaysia": ("malaysia",), "philippines": ("philippines",),
    "vietnam": ("vietnam",), "mexico": ("mexico",), "brazil": ("brazil",),
    "south africa": ("south africa",),
}

QUESTION_RULES: List[Tuple[Tuple[str, ...], str]] = [
    (("how did you hear", "where did you hear", "how did you find this", "source of hire", "where did you see the vacancy"), "how_heard"),
    (("country of residence", "current country of residence", "current country"), "country"),
    (("employment agreement", "post-employment", "restrictive covenant"), "no"),
    (("previously worked", "have you previously worked", "consulted for gitlab", "consulted for",
      "ever been employed", "previously been employed", "former employee",
      "current or former employee", "ever worked for"), "no"),
    # Interview accommodation is a yes/no logistics question, not the EEO
    # disability self-identification below it.
    (("accommodation", "accomodation"), "no"),
    (("valid passport",), "yes"),
    # Answered country-aware by `work_auth_answer`, never from these keys
    # alone: see there. Kept here so a label carrying them is still known to be
    # a legal question and never falls through to a field alias.
    (WORK_AUTH_NEEDLES, "work_auth"),
    (SPONSORSHIP_NEEDLES, "sponsorship"),
    (("over 18", "18 years of age", "at least 18"), "yes"),
    (("willing to relocate", "open to relocate", "can you relocate", "ready to relocate"), "relocate"),
    (("attached a custom cover letter", "have you attached a custom cover"), "no"),
    (("willing to work remotely", "remote work"), "yes"),
    (("current (or most recent) employer", "most recent employer", "last employer you have been", "name and location of the last employer", "who is your current"), "current_company"),
    (("previously applied", "have you applied"), "no"),
    (("related to anyone", "know anyone who works", "employee referral name"), "no"),
    (("non-compete", "noncompete"), "no"),
    (("conflict of interest",), "no"),
    (("gender identity", "gender", "sex"), "gender"),
    (("hispanic", "latino", "ethnicity", "race", "racial"), "ethnicity"),
    (("veteran status", "protected veteran", "veteran"), "veteran"),
    (("disability", "disabled"), "disability"),
    (("pronoun",), "decline"),
    (("i identify as",), "decline"),
    (("i certify", "information is true", "accurate and complete", "acknowledge that"), "yes"),
    (("privacy policy", "terms and conditions", "terms of use", "i agree", "i consent", "consent to"), "yes"),
    (("receive updates", "marketing emails", "sms notifications", "text messages"), "no"),
    (("export control", "deemed export", "itir", "itar"), "no"),
]


def _norm(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("*", " ")
    text = re.sub(r"\(optional\)", " ", text)
    text = re.sub(r"[^a-z0-9\s+/.-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _has_phrase(text: str, phrase: str, plural: bool = False) -> bool:
    """
    `phrase` occurs in `text` as whole words.

    Bare substring matching is this repo's recurring bug class, and here it
    answers employers: "race" in *embrace* asked for ethnicity, "itar" in
    *military* answered an export-control "No", "state" in *statement* sent
    his state, "unit" in *United States* sent an address line. `plural` lets a
    needle take a plural ending ("pronoun" -> "pronouns") and nothing else.
    """
    if not phrase:
        return False
    tail = r"(?:s|es)?" if plural else ""
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}{tail}(?![a-z0-9])", text) is not None


def _bool_text(value: bool) -> str:
    return "Yes" if value else "No"


def _education(profile: ApplicantProfile) -> Education:
    for row in profile.education:
        if row.school or row.degree:
            return row
    return Education()


def value_for_key(profile: ApplicantProfile, key: str, job_title: str = "", company: str = "") -> str:
    edu = _education(profile)
    mapping = {
        "first_name": profile.first_name,
        "last_name": profile.last_name,
        "full_name": profile.full_name,
        "email": profile.email,
        "phone": profile.phone_e164() or profile.phone,
        "phone_national": profile.phone_national(),
        "phone_country": profile.phone_country,
        "phone_device_type": "Mobile",
        "linkedin": profile.linkedin,
        "github": profile.github,
        "portfolio": profile.portfolio or profile.github,
        "address_line1": profile.address_line1,
        "address_line2": profile.address_line2,
        "city": profile.city,
        "state": profile.state,
        "postal_code": profile.postal_code,
        "country": profile.country,
        "location": profile.location_string(),
        "current_company": profile.current_company,
        "current_title": profile.current_title,
        "years_experience": profile.years_experience,
        "notice_period": profile.notice_period,
        "earliest_start": profile.earliest_start,
        "salary_expectation": profile.salary_expectation,
        "cover_letter": profile.cover_letter(job_title, company),
        "skills": profile.skills,
        "school": edu.school,
        "degree": edu.degree,
        "major": edu.major,
        "gpa": (profile.custom_answers or {}).get("gpa") or (profile.custom_answers or {}).get("cgpa") or "",
        "how_heard": profile.how_heard,
        "gender": profile.gender,
        "ethnicity": profile.ethnicity,
        "veteran": profile.veteran,
        "disability": profile.disability,
        "yes": "Yes",
        "no": "No",
        "decline": "Decline to self-identify",
        # Country-blind: true only of his own country. `resolve_value` answers
        # these through `work_auth_answer` and never reaches this mapping.
        "work_auth": _bool_text(profile.authorized_to_work),
        "sponsorship": _bool_text(profile.require_sponsorship),
        "relocate": "Yes",
    }
    return mapping.get(key, "") or ""


def custom_answer(profile: ApplicantProfile, label: str) -> str:
    """
    The most specific custom answer matching this label.

    Longest needle wins, because several can match one question and dict order
    is meaningless. "Are you subject to any employment agreements ... with your
    current employer?" contains both "employment agreements" (-> No) and
    "current employer" (-> Paytm); answering a yes/no legal question with an
    employer name is the kind of mistake that reaches a real application.
    """
    n = _norm(label)
    best_answer = ""
    best_len = 0
    for needle, answer in (profile.custom_answers or {}).items():
        needle_n = _norm(needle)
        # Whole words only: a saved "visa" must not answer "Visakhapatnam".
        if needle_n and _has_phrase(n, needle_n, plural=True) and len(needle_n) > best_len:
            best_answer, best_len = str(answer), len(needle_n)
    return best_answer


# A label opening with one of these is a question, whatever nouns it contains.
_QUESTION_SHAPE = re.compile(
    r"^(do|does|did|are|is|was|were|will|would|have|has|had|can|could|should|may|must)\b"
)
# Open questions: they ask for something, and may name the field they want.
_ASK_SHAPE = re.compile(r"^(what|which|where|when|how|why|describe|tell|explain|please describe)\b")

# One-word aliases that mean the field wherever they occur. Every other
# one-word alias ("state", "mobile", "unit", "source", "degree") is also
# ordinary English, and inside a sentence it is usually that: "Do you have
# experience in mobile app development?" is not asking for a phone number.
_DISTINCT_WORDS = {
    "email", "e-mail", "linkedin", "github", "firstname", "lastname", "surname",
    "telephone", "pincode", "gpa", "cgpa",
}


def _weak_alias_fits(n: str, alias: str, raw_label: str) -> bool:
    """
    May an ordinary one-word alias answer this label?

    Only where the label is plainly *about* that field: short ("State",
    "Mobile", "Current city"), or ending in it ("Enter your current city"), or
    an open question naming it ("What is your city?", "Which state ...").
    Never a yes/no question — its nouns are the subject of the question, not
    the field being asked for.
    """
    if _QUESTION_SHAPE.match(n):
        return False
    ends = re.search(rf"(?<![a-z0-9]){re.escape(alias)}[\s./-]*$", n) is not None
    if _ASK_SHAPE.match(n):
        named = re.match(rf"^(what|which)( is| are)?( your| the)?( current)? {re.escape(alias)}\b", n)
        return ends or named is not None
    if raw_label.strip().endswith("?"):
        return ends
    return ends or len(n.split()) <= 4


def match_field_key(label: str) -> Optional[str]:
    n = _norm(label)
    if not n:
        return None
    for key, aliases in FIELD_ALIASES:
        for alias in aliases:
            if not _has_phrase(n, alias):
                continue
            if " " not in alias and alias not in _DISTINCT_WORDS and not _weak_alias_fits(n, alias, label):
                continue
            if key == "full_name" and ("first" in n or "last" in n):
                continue
            if key == "country" and "phone" in n:
                return "phone_country"
            return key
    return None


def match_question_key(label: str) -> Optional[str]:
    n = _norm(label)
    for needles, key in QUESTION_RULES:
        if any(_has_phrase(n, needle, plural=True) for needle in needles):
            return key
    return None


def is_yes_no_question(label: str) -> bool:
    return bool(_QUESTION_SHAPE.match(_norm(label)))


def _countries_in(n: str) -> set:
    return {
        country for country, aliases in COUNTRY_NAMES.items()
        if any(_has_phrase(n, alias) for alias in aliases)
    }


def _home_country(profile: ApplicantProfile) -> str:
    home = _norm(profile.country)
    if not home:
        return ""
    named = _countries_in(home)
    return next(iter(named)) if len(named) == 1 else home


# Where the job being filled is, when the caller knows. Set once per job by the
# apply engine and once per request by the autofill API, so every resolver on
# the path sees it without threading a parameter through a dozen signatures.
JOB_LOCATION: ContextVar[str] = ContextVar("job_location", default="")
# india | europe. Set with JOB_LOCATION. Default india, so a question about
# the UK stays blank on sponsorship until Europe mode is actually on.
APPLY_REGION: ContextVar[str] = ContextVar("apply_region", default="india")


def _job_countries(location: str) -> set:
    """
    The country a job's location names. "Bengaluru, Karnataka" names no
    country, so the scanner's own India test decides — the same one that
    admitted the job in the first place.
    """
    n = _norm(location)
    if not n:
        return set()
    named = _countries_in(n)
    if named:
        return named
    from backend.scrapers.filter_utils import _abroad_in, _india_in

    return {"india"} if _india_in(n) and not _abroad_in(n) else set()


def work_auth_answer(profile: ApplicantProfile, label: str) -> Optional[str]:
    """
    The answer to a work-authorisation or sponsorship question, or None if
    `label` is not one. An empty string means "leave it for him".

    The profile records authorisation for one country: his own. It used to
    answer every "authorized to work in <anywhere>" from that single flag, so
    "Are you legally authorized to work in the United States?" went out as
    "Yes" for a candidate living in India — a false statement on a legal
    question, on a real application. So:

    - his own country named: the profile's flags;
    - only other countries named: not authorised there ("No"); whether he
      would need sponsorship there is not something the profile records, so
      that is left blank rather than guessed;
    - no country named ("the country where this job is located"), or his and
      others mixed: blank. A blank legal question costs him a click; a wrong
      one cannot be recalled.

    Runs before custom answers: a saved "authorized to work": "Yes" was
    written about India and says nothing about Canada. Only a saved answer
    whose own key names the same country is honoured.
    """
    n = _norm(label)
    auth = any(_has_phrase(n, x) for x in WORK_AUTH_NEEDLES)
    sponsor = any(_has_phrase(n, x) for x in SPONSORSHIP_NEEDLES)
    if not (auth or sponsor):
        return None
    named = _countries_in(n)
    home = _home_country(profile)
    # "Will you require sponsorship?" and "…in the country where this job is
    # located?" name no country — they mean the job's. When the job is known,
    # its location answers that; when it is not, the question stays blank.
    if not named:
        named = _job_countries(JOB_LOCATION.get())
    # "London" names no country in COUNTRY_NAMES. Europe mode maps the city.
    if APPLY_REGION.get() == "europe" and not named:
        from backend.scrapers.filter_utils import europe_countries_in

        named = europe_countries_in(JOB_LOCATION.get())
    if not named or not home:
        return ""

    best, best_len = None, 0
    for needle, answer in (profile.custom_answers or {}).items():
        needle_n = _norm(needle)
        if (_has_phrase(n, needle_n) and _countries_in(needle_n) == named
                and len(needle_n) > best_len):
            best, best_len = str(answer), len(needle_n)
    if best is not None:
        return best

    # Europe mode is his instruction that he needs a visa in these four
    # countries. A US question is not one of them, and stays blank.
    if APPLY_REGION.get() == "europe":
        from backend.scrapers.filter_utils import EUROPE_COUNTRIES

        if named <= set(EUROPE_COUNTRIES):
            if auth and sponsor:
                if _has_phrase(n, "without"):
                    return "No"
                return ""
            if sponsor:
                return "Yes"
            return "No"

    if named == {home}:
        if auth and sponsor:
            # "authorized to work in India without sponsorship" is one yes/no
            # fact; "authorized ..., or will you require sponsorship?" is two.
            if _has_phrase(n, "without"):
                return _bool_text(profile.authorized_to_work and not profile.require_sponsorship)
            return ""
        if sponsor:
            return _bool_text(profile.require_sponsorship)
        return _bool_text(profile.authorized_to_work)
    if home in named or sponsor:
        return ""
    return "No"


def resolve_value(profile: ApplicantProfile, label: str, job_title: str = "", company: str = "") -> str:
    label_n = _norm(label)
    title_n = _norm(job_title)
    relocate_q = any(x in label_n for x in ("relocate", "ready to relocate", "open to relocate", "willing to relocate"))
    if relocate_q or ("based in" in label_n and "chennai" in label_n):
        if "chennai" in label_n or "chennai" in title_n:
            return "No"
        if relocate_q:
            return "Yes"
    # Before custom answers and field aliases: an unresolved legal question
    # must stay blank, not fall through to "country" and answer "India".
    auth = work_auth_answer(profile, label)
    if auth is not None:
        return auth
    custom = custom_answer(profile, label)
    if custom:
        return custom

    key = match_field_key(label)
    qkey = match_question_key(label)

    # A yes/no question is answered by its question rule, even when a field
    # alias happens to occur inside it. "Do you have the legal right to work in
    # the listed location?" contains "location", and answering a legal question
    # with "Noida, Uttar Pradesh, India" is the same class of mistake as
    # answering "employment agreements" with "Paytm" — it reaches the employer.
    if qkey and (is_yes_no_question(label) or not key):
        return value_for_key(profile, qkey, job_title, company)
    if key:
        return value_for_key(profile, key, job_title, company)
    if qkey:
        return value_for_key(profile, qkey, job_title, company)
    return ""


def pick_option(options: List[str], desired: str) -> Optional[str]:
    if not options or not desired:
        return None
    d = _norm(desired)
    cleaned = [(opt, _norm(opt)) for opt in options if _norm(opt) and _norm(opt) not in {"select", "select an option", "please select", "-"}]
    if not cleaned:
        return None
    for opt, n in cleaned:
        if n == d:
            return opt

    # A decline is answered by a decline or not at all. It used to fall into
    # the partial match below, where "no" inside "I do *no*t want to answer"
    # picked "No" — a factual answer to a disability question he declined.
    if _is_decline(d):
        declines = [(opt, n) for opt, n in cleaned if _is_decline(n)]
        if not declines:
            return None
        want = _tokens(d)
        return max(declines, key=lambda pair: len(_tokens(pair[1]) & want))[0]

    # Everything below compares whole words, and only between answers of the
    # same polarity: "I am not a protected veteran" shares two words with
    # "Protected Veteran" and means the opposite.
    negated = _is_negated(d)
    candidates = [(opt, n) for opt, n in cleaned
                  if not _is_decline(n) and _is_negated(n) == negated]

    # Among partial matches, prefer the most specific rather than the first in
    # document order. A phone-country list matches "India" against "British
    # Indian Ocean Territory" long before it reaches "India +91"; taking the
    # earliest hit picks the wrong country.
    partials = [(opt, n) for opt, n in candidates if _has_phrase(n, d) or _has_phrase(d, n)]
    if partials:
        def rank(pair: Tuple[str, str]) -> Tuple[int, int]:
            _, n = pair
            starts_with = 0 if n.startswith(d) else 1
            return (starts_with, abs(len(n) - len(d)))

        return min(partials, key=rank)[0]

    # The same answer worded differently: every meaningful word of one is in
    # the other ("Not a Veteran" for "I am not a protected veteran").
    want = _tokens(d)
    if want:
        subsets = []
        for opt, n in candidates:
            have = _tokens(n)
            # Sharing only the negation is not the same answer ("No" vs "None").
            if (have & want) - {"not"} and (have <= want or want <= have):
                subsets.append((len(have & want), -len(have ^ want), opt))
        if subsets:
            return max(subsets)[2]

    if d in YES_TRUE or d == "yes":
        for opt, n in cleaned:
            if n in {"yes", "y", "true"} or n.startswith("yes "):
                return opt
    if d in {"no", "false", "n"}:
        for opt, n in cleaned:
            if n in {"no", "n", "false"} or n.startswith("no "):
                return opt
    # No "only one option, so take it": a lone option is still the wrong
    # answer when it does not match.
    return None


# Phrases that decline to answer. Not "not listed" ("my gender is not listed"
# is an answer) and not "i do not" ("I do not have a disability" is one too).
_DECLINE_PHRASES = (
    "decline", "declined", "prefer not", "rather not", "choose not", "not to answer",
    "not to say", "not to disclose", "not disclose", "not wish", "don t wish", "do not wish",
    "not want to", "not to self", "not to self-identify",
)
_NEGATIONS = {"not", "no", "non", "never", "none", "dont", "cannot"}
# `_norm` turns "don't" into "don t"; fold every such contraction to "not"
# before splitting, so "can" (as in "I can relocate") stays positive.
_CONTRACTION_RE = re.compile(r"(?<![a-z0-9])(don|can|won|isn|aren|doesn|didn|wouldn) t(?![a-z0-9])")
_NEGATION_RE = re.compile(
    r"(?<![a-z0-9])(not|no|non|never|none|dont|cannot"
    r"|(don|can|won|isn|aren|doesn|didn|wouldn) t)(?![a-z0-9])"
)
_STOPWORDS = {
    "i", "am", "a", "an", "the", "to", "of", "my", "me", "is", "are", "you", "your",
    "have", "has", "be", "s", "t", "and", "or", "as", "any", "one", "do", "will",
}


def _is_decline(n: str) -> bool:
    return any(_has_phrase(n, x) for x in _DECLINE_PHRASES)


def _is_negated(n: str) -> bool:
    return _NEGATION_RE.search(n) is not None


def _tokens(n: str) -> set:
    """Meaningful words, every negation spelled "not" ("Non-Veteran" = "not a veteran")."""
    out = set()
    for word in re.findall(r"[a-z0-9+]+", _CONTRACTION_RE.sub(" not ", n)):
        if word in _NEGATIONS:
            out.add("not")
        elif word not in _STOPWORDS:
            out.add(word)
    return out


def pick_option_for_key(options: List[str], desired: str, key: str = "") -> Optional[str]:
    """
    `pick_option`, then the substitutes this field accepts.

    Only consulted when the profile's own answer is genuinely absent from the
    list, so a tenant that does offer "Mobile" still gets "Mobile".
    """
    choice = pick_option(options, desired)
    if choice:
        return choice
    for alternative in OPTION_FALLBACKS.get(key, ()):
        if _norm(alternative) == _norm(desired):
            continue
        choice = pick_option(options, alternative)
        if choice:
            return choice
    return None


_CONSENT_RE = re.compile(
    r"\b(i agree|i consent|i certify|i acknowledge|acknowledge|privacy policy|"
    r"terms and conditions|terms of use|accurate|truthful)\b"
)
# A consent box is ticked without asking. One that opts into marketing, a
# talent pool or job alerts is a choice, not a formality — never ticked here.
_OPT_IN_RE = re.compile(
    r"\b(marketing|newsletter|promotional|talent (community|network|pool)|job alerts?|"
    r"future (opportunities|roles|openings)|other opportunities|similar roles|"
    r"keep me (updated|informed)|contact me about)\b"
)


def is_consent_label(label: str) -> bool:
    n = _norm(label)
    if _OPT_IN_RE.search(n) or re.search(r"\b(do not|don't|disagree)\b", n):
        return False
    return bool(_CONSENT_RE.search(n))


# Bot traps. Workday ships one on every Create Account step: a real, rendered,
# visible input whose label tells a human not to touch it (Intel's is
# data-automation-id="beecatcher"). Filling one gets the application discarded
# without any error, so these are matched before anything else.
TRAP_PHRASES = (
    "honeypot", "bee catcher", "beecatcher",
    "for robots only", "robots only", "only for robots",
    "do not enter if you", "if you are human", "if you re human",
    "leave this blank", "leave this field blank", "do not fill",
)


def is_skip_field(label: str, name: str, autocomplete: str, automation: str = "") -> bool:
    raw = f"{label} {name} {autocomplete} {automation}".lower()
    blob = _norm(raw)
    if any(phrase in blob or phrase in raw for phrase in TRAP_PHRASES):
        return True
    # `_norm` turns underscores into spaces, so trap names have to be matched raw.
    # Only a field with no label of its own is the spam trap — a labeled one is a
    # genuine portfolio question. A "label" echoing the name is no label at all.
    label_n, name_n = _norm(label), _norm(name)
    if (not label_n or label_n == name_n) and re.search(
        r"\b(website_url|url_website|homepage_url)\b", raw
    ):
        return True
    if name.startswith("utf8") or name in {"_method", "authenticity_token"}:
        return True
    return False
