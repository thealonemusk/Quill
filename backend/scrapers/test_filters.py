"""
Job-discovery filters, dedup, company tiers and the seen ledger.

The search follows a 9-year QA Automation Lead in Delhi NCR. Every company
stays. Lead, SDET, and automation titles stay. Manual-only, junior, non-QA,
other cities, and a requirement whose floor is above 11 years do not.

Nothing here touches the network or Quill.db: DB checks run against an
in-memory SQLite database, and the apply-all check stubs out the apply run.

Run:  python backend/scrapers/test_filters.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
HERE = Path(__file__).resolve().parent
# Run as a script, this directory is sys.path[0], and its http.py then shadows
# the standard library's `http` for everything imported after it.
sys.path[:] = [p for p in sys.path if not p or Path(p).resolve() != HERE]
sys.path.insert(0, str(ROOT))

from loguru import logger  # noqa: E402

logger.remove()

from backend.scrapers.filter_utils import (  # noqa: E402
    is_job_experience_valid,
    is_location_allowed,
)
from backend.autopilot.select import Tier, company_tier, role_score  # noqa: E402

failures: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name:62} {str(got)[:40]!r}")
    if not ok:
        failures.append(f"{name}: expected {want!r}, got {got!r}")


def section(title: str) -> None:
    print(f"\n{title}")


def _loc(location: str, jd: str = "", title: str = "Software Engineer", platform: str = "") -> bool:
    return is_location_allowed(
        {"location": location, "title": title, "jd_text": jd, "platform": platform}
    )[0]


def _title_ok(title: str) -> bool:
    return is_job_experience_valid({"title": title, "jd_text": ""}, require_jd=False)[0]


def _exp_ok(jd: str, max_years=None) -> bool:
    kwargs = {"require_jd": False}
    if max_years is not None:
        kwargs["max_years"] = max_years
    return is_job_experience_valid(
        {"title": "QA Engineer", "jd_text": jd}, **kwargs
    )[0]


# ── 1. Location ──────────────────────────────────────────────────────────────


def test_europe() -> None:
    section("1b. Europe: UK, Ireland, Germany, Netherlands, Sweden; a visa refusal drops the post")
    from backend.scrapers.filter_utils import europe_countries_in, passes_hard_filters, visa_refused

    def eu(location: str, jd: str = "", title: str = "Software Engineer") -> bool:
        return is_location_allowed(
            {"location": location, "title": title, "jd_text": jd, "platform": "linkedin"},
            region="europe",
        )[0]

    def kept(location: str, jd: str) -> bool:
        return passes_hard_filters(
            {
                "location": location,
                "title": "Software Engineer",
                "company": "Stripe",
                "jd_text": jd,
                "platform": "linkedin",
            },
            region="europe",
            require_jd=False,
        )[0]

    for city in (
        "London, United Kingdom", "Manchester", "Remote, UK",
        "Dublin, Ireland", "Berlin, Germany", "Amsterdam, Netherlands",
        "Stockholm, Sweden", "Gothenburg", "Malmö", "Goteborg",
        "Edinburgh, Scotland", "Belfast, Northern Ireland",
    ):
        check(f"Europe keeps {city!r}", eu(city), True)
    check("Europe rejects Bengaluru", eu("Bengaluru, Karnataka"), False)
    check("Europe rejects Paris", eu("Paris, France"), False)
    check("Europe rejects Seattle", eu("Seattle, WA"), False)
    check("Europe rejects bare Remote", eu("Remote"), False)
    check("Europe rejects London; Seattle", eu("London; Seattle, WA"), False)
    check("Europe rejects Stockholm and Paris", eu("Stockholm; Paris, France"), False)
    check("Sweden is Sweden only",
          europe_countries_in("Stockholm, Sweden"), {"sweden"})
    check("Europe rejects Indianapolis", eu("Indianapolis, IN"), False)
    check("Northern Ireland is the UK only",
          europe_countries_in("Northern Ireland"), {"united kingdom"})

    silent = "We are hiring a software engineer to build APIs."
    check("silent description kept", kept("London", silent), True)
    check("visa refusal dropped",
          kept("London", "Candidates must already have the right to work in the UK."), False)
    check("same sentence offering sponsorship kept",
          kept("London", "You must be authorized to work in the UK, or we will sponsor a visa."), True)
    check("will not sponsor dropped",
          kept("Berlin", "We will not sponsor work visas."), False)
    check("cannot provide sponsorship dropped",
          kept("Amsterdam", "We cannot provide sponsorship for this role."), False)
    check("refusal detector ignores a silent JD", visa_refused(silent)[0], False)


def test_location() -> None:
    section("1. Location: the location field decides; JD boilerplate does not override it")
    check("Seattle + JD 'teams span US, Europe and India' rejected",
          _loc("Seattle, WA", "Our teams span the US, Europe and India."), False)
    check("Dublin, Ireland + JD naming Bengaluru rejected",
          _loc("Dublin, Ireland", "We are hiring across London, Dublin and Bengaluru."), False)
    check("Atlanta + 'Join NCR Voyix' rejected",
          _loc("Atlanta, Georgia", "Join NCR Voyix and build the future of commerce."), False)
    check("title 'NCR Voyix' is not Delhi NCR", _loc("", title="Software Engineer - NCR Voyix"), False)
    check("Dublin, Dublin rejected (CLAUDE.md)", _loc("Dublin, Dublin"), False)
    check("Stockholm is not India", _loc("Stockholm, Sweden"), False)
    check("Indianapolis is not India", _loc("Indianapolis, IN"), False)
    check("Indianapolis without a state is not India", _loc("Indianapolis"), False)

    for city in ("Delhi NCR", "NCR", "New Delhi", "Noida", "Greater Noida",
                 "Gurugram, Haryana", "Gurgaon", "Ghaziabad", "Faridabad",
                 "Noida; Seattle, WA"):
        check(f"{city!r} accepted", _loc(city), True)
    for city in ("Bengaluru, Karnataka", "Hyderabad", "Pune, Maharashtra", "Mumbai",
                 "Chennai, Tamil Nadu", "Bangalore Urban, India", "Remote - India",
                 "Bengaluru; Seattle, WA"):
        check(f"{city!r} rejected", _loc(city), False)

    section("1. Location: ambiguous location, JD decides only with an NCR city")
    check("Remote + 'based in Noida' accepted", _loc("Remote", "This role is based in Noida."), True)
    check("Remote + 'based in Pune' rejected", _loc("Remote", "This role is based in Pune, India."), False)
    check("empty + 'Location: Hyderabad' rejected", _loc("", "Location: Hyderabad (hybrid)"), False)
    check("Multiple locations + 'based in Seattle' rejected",
          _loc("Multiple locations", "This role is based in Seattle, WA."), False)
    check("Remote + generic 'India' mention rejected",
          _loc("Remote", "Our teams span the US, Europe and India."), False)
    check("empty + JD 'Join NCR Voyix' rejected", _loc("", "Join NCR Voyix today."), False)
    check("empty + JD 'Delhi NCR office' accepted", _loc("", "Work from our Delhi NCR office."), True)
    check("India board with no location rejected", _loc("", platform="naukri"), False)
    check("missing location still rejected", _loc(""), False)


# ── 2. Titles ────────────────────────────────────────────────────────────────


def test_india_excluded_roles() -> None:
    section("1c. Delhi NCR keeps QA titles and drops everything else")
    from backend.scrapers.company_careers import _expanded_search_roles, _role_matches
    from backend.scrapers.filter_utils import matches_region, passes_hard_filters
    from backend.scrapers.registry import resolve_discovery_roles

    def kept(title: str, region: str = "india") -> bool:
        location = "Noida" if region == "india" else "London, United Kingdom"
        return passes_hard_filters(
            {
                "title": title,
                "company": "Stripe",
                "location": location,
                "jd_text": "We are hiring a QA engineer to test APIs.",
                "platform": "linkedin",
            },
            region=region,
            require_jd=False,
        )[0]

    for title in (
        "SDET – I (Software Development Engineer in Test)",
        "SDET, Portworx",
        "Software Development Engineer in Test",
        "Software Engineer in Test",
        "Software Engineer Test",
        "Software Engineer, Test",
        "Software Engineer - UI Test",
        "Test Engineer",
        "Senior Test Engineer",
        "Software QA Engineer",
        "QA Engineer",
        "Senior QA Engineer",
        "QA Lead",
        "Quality Assurance Engineer",
        "QA Automation Lead",
        "Test Automation Lead",
        "Automation Architect",
    ):
        check(f"NCR keeps {title!r}", kept(title), True)
    for title in ("Manual Tester", "Manual QA Engineer", "Junior QA Engineer", "Fresher QA", "QA Intern"):
        check(f"NCR drops {title!r}", kept(title), False)
    for title in (
        "Site Reliability Engineer",
        "SRE",
        "DevOps Engineer",
        "Support Engineer",
        "Technical Support Engineer",
        "Dotnet Developer",
        ".NET Developer",
        "Data Engineer",
        "Software Engineer",
        "Software Engineer, Latest",
        "Software Engineer, Big Data",
        "Database Engineer",
        "Software Engineer, Customer Support Platform",
        "Backend Engineer",
    ):
        check(f"NCR drops {title!r}", kept(title), False)
    check("stored Noida QA row stays on the board",
          matches_region(
              {"title": "QA Engineer", "location": "Noida", "company": "Stripe",
               "jd_text": "", "platform": "linkedin"},
              "india",
          ), True)
    check("stored Noida DevOps row stays off the board",
          matches_region(
              {"title": "DevOps Engineer", "location": "Noida", "company": "Stripe",
               "jd_text": "", "platform": "linkedin"},
              "india",
          ), False)
    check("Europe keeps DevOps Engineer", kept("DevOps Engineer", "europe"), True)
    check("Europe keeps Site Reliability Engineer",
          kept("Site Reliability Engineer", "europe"), True)
    check("Europe keeps Data Engineer", kept("Data Engineer", "europe"), True)
    check("Europe keeps SDET", kept("SDET", "europe"), True)
    roles = ["QA Engineer", "SDET"]
    check("career sites do not treat DevOps as QA",
          _role_matches("DevOps Engineer", roles), False)
    check("career sites do not treat SRE as QA",
          _role_matches("Site Reliability Engineer", roles), False)
    check("career sites keep Senior QA",
          _role_matches("Senior QA Engineer", roles), True)
    discovered = [r.lower() for r in resolve_discovery_roles(roles)]
    searched = [r.lower() for r in _expanded_search_roles(discovered)]
    check("search does not ask for DevOps", "devops engineer" not in searched, True)
    check("search does not ask for Data Engineer", "data engineer" not in searched, True)
    check("search does not ask for manual testing", "manual tester" not in searched, True)
    check("search asks for QA", "qa engineer" in searched, True)
    for phrase in (
        "new grad", "university graduate", "entry level", "sde 1", "fresher",
        "junior software engineer", "early career", "associate software engineer",
        "qa manager", "automation architect",
    ):
        check(f"search does not add {phrase}", phrase not in searched, True)


def test_india_companies() -> None:
    section("1d. Every company stays when the title is QA and the city is NCR")
    from backend.autopilot.select import INDIA_COMPANY_SEARCH, Tier, company_tier
    from backend.scrapers.filter_utils import (
        india_company_block, matches_region, passes_hard_filters,
    )
    from backend.scrapers.linkedin import india_company_queries

    def kept(company: str, region: str = "india", title: str = "QA Engineer") -> bool:
        location = "Noida" if region == "india" else "London, United Kingdom"
        return passes_hard_filters(
            {
                "title": title,
                "company": company,
                "location": location,
                "jd_text": "We are hiring a QA engineer to test APIs.",
                "platform": "linkedin",
            },
            region=region,
            require_jd=False,
        )[0]

    for company in (
        "Google", "Intuit", "Flipkart", "Dream11",
        "Goldman Sachs Services Pvt Ltd", "Pixel Forge", "Square Yards",
        "Infosys", "NetM Corporate Solutions", "TalentRabbit", "EXL",
        "Rootlex Technologies Private Limited", "CXO Events", "Client of SCHS",
        "Talentica", "Teradata", "",
    ):
        check(f"NCR keeps {company!r}", kept(company), True)
    for title in (
        "Backend Engineer - SDE 1", "AI Engineer", "Data Engineer",
        "Java Developer", "Software Developer", "Software Development Engineer",
    ):
        check(f"non-QA dropped at any company: {title!r}", kept("Pixel Forge", title=title), False)
    check("Google still drops a non-QA title", kept("Google", title="Java Developer"), False)
    check("company block is empty for an unknown name", india_company_block("Pixel Forge"), "")
    check("company block is empty for a staffing firm", india_company_block("Infosys"), "")
    check("company block is empty for a blank name", india_company_block(""), "")
    check("stored unknown QA role stays on the board",
          matches_region(
              {"title": "QA Engineer", "location": "Noida", "company": "Pixel Forge",
               "jd_text": "", "platform": "linkedin"},
              "india",
          ), True)
    check("stored unknown software role stays off the board",
          matches_region(
              {"title": "Software Engineer", "location": "Noida", "company": "Pixel Forge",
               "jd_text": "", "platform": "linkedin"},
              "india",
          ), False)
    check("Europe keeps a sponsor that is not on the India lists",
          kept("Revolut", "europe"), True)
    queries = india_company_queries(["QA Engineer", "SDET"])
    check("company search is Delhi, not all of India", queries[0][1], "Delhi")
    check("one role does not multiply company searches", len(queries), len(INDIA_COMPANY_SEARCH))
    check("company search stays within forty names", len(INDIA_COMPANY_SEARCH) <= 40, True)
    check("Flipkart is searched by name", any("Flipkart" in q[0] for q in queries), True)
    for name in INDIA_COMPANY_SEARCH:
        check(f"{name} is a kept company", company_tier(name) >= Tier.KNOWN, True)


def test_titles() -> None:
    section("2. Titles: senior QA titles pass; internships do not")
    for title in (
        "Senior QA Engineer", "Sr. QA Engineer", "QA Lead", "QA Manager",
        "Staff QA Engineer", "SDET II", "SDET III", "Test Engineer 3",
        "QA Engineer | 8-10 years", "Principal SDET",
    ):
        check(f"{title!r} accepted", _title_ok(title), True)
    check("internship rejected", _title_ok("QA Engineering Intern"), False)
    ok, reason = is_job_experience_valid(
        {"title": "QA Engineer (API testing | 12-15 years | Noida)",
         "jd_text": ""},
        require_jd=False,
    )
    check("a year range above 10 is over the cap", ok, False)
    check("a year range is not read as a seniority level",
          "years of experience" in reason, True)


# ── 3. Experience ────────────────────────────────────────────────────────────


def test_experience() -> None:
    section("3. Experience: the floor of the range is the requirement, cap is 11")
    check("'1+ years' accepted", _exp_ok("Requirements: 1+ years of experience with testing."), True)
    check("'8+ years' accepted", _exp_ok("8+ years of experience."), True)
    check("'10+ years' accepted", _exp_ok("10+ years of experience."), True)
    check("'11+ years' accepted", _exp_ok("11+ years of experience."), True)
    check("'8-12 years' accepted (floor is 8)", _exp_ok("8-12 years of experience."), True)
    check("'12+ yrs' rejected", _exp_ok("Minimum 12+ yrs in testing."), False)
    check("'12-15 years' rejected", _exp_ok("12-15 years of experience."), False)
    check("'at least 15 years' rejected", _exp_ok("At least 15 years of experience."), False)

    section("3. Experience: max_years is honoured, default is 11")
    check("default cap is 11: '12 years' rejected", is_job_experience_valid(
        {"title": "QA Engineer", "jd_text": "12 years of experience."}, require_jd=False)[0], False)
    check("default cap is 11: '11 years' accepted", is_job_experience_valid(
        {"title": "QA Engineer", "jd_text": "11 years of experience."}, require_jd=False)[0], True)
    check("max_years=3 admits '3+ years'", _exp_ok("3+ years of experience.", max_years=3), True)
    check("max_years=1 rejects '2+ years'", _exp_ok("2+ years of experience.", max_years=1), False)


# ── 6. Company tiers and role fit ────────────────────────────────────────────


def test_tiers() -> None:
    section("6. Tiers: brands behind generic legal suffixes are not excluded")
    for name, tier in (
        ("Goldman Sachs Services Pvt Ltd", Tier.STRONG),
        ("Morgan Stanley Advantage Services Pvt Ltd", Tier.STRONG),
        ("Swiggy - Bundl Technologies Pvt Ltd", Tier.STRONG),
        ("Amazon Hiring", Tier.FAANG),
        ("Kratos", Tier.UNKNOWN),
        ("Square Yards", Tier.UNKNOWN),
        ("Notion Press", Tier.UNKNOWN),
        ("D.E. Shaw", Tier.STRONG),
        ("DE Shaw", Tier.STRONG),
        ("D. E. Shaw & Co.", Tier.STRONG),
        ("J.P. Morgan", Tier.STRONG),
        ("JPMorgan Chase & Co.", Tier.STRONG),
        ("Metaview", Tier.UNKNOWN),
        ("Applied Materials", Tier.STRONG),
        ("Amazon", Tier.FAANG),
        ("Google", Tier.FAANG),
        ("Microsoft", Tier.FAANG),
        ("Microsoft India (R&D) Private Limited", Tier.FAANG),
        ("Square", Tier.ELITE),
        ("Notion", Tier.ELITE),
        ("Notion Labs", Tier.ELITE),
        ("Booking.com", Tier.STRONG),
        ("Target Corporation", Tier.STRONG),
        ("Dreamplug Technologies (CRED)", Tier.STRONG),
        ("Observe.AI", Tier.KNOWN),
        ("Intuit", Tier.STRONG),
        ("Dream11", Tier.STRONG),
        ("MakeMyTrip", Tier.STRONG),
        ("Sprinklr", Tier.STRONG),
        ("Samsung Electronics", Tier.STRONG),
        ("Barclays", Tier.STRONG),
        ("NatWest Group", Tier.STRONG),
        ("TransUnion", Tier.STRONG),
        ("Teradata", Tier.UNKNOWN),
        ("Sauce Labs", Tier.KNOWN),
        ("N-able", Tier.KNOWN),
        ("Media.net", Tier.KNOWN),
        ("Motive", Tier.KNOWN),
        ("Cursor", Tier.STRONG),
        ("Harvey", Tier.STRONG),
        ("Harvey Nash", Tier.UNKNOWN),
        ("Arm", Tier.STRONG),
        ("Pixel Forge", Tier.UNKNOWN),
    ):
        check(f"{name!r} -> {tier.name}", company_tier(name), tier)

    section("6. Tiers: consultancies and intermediaries still excluded")
    for name in ("Atos", "iGate", "IGATE Global Solutions", "Infosys BPM", "Infosys",
                 "Wipro Limited", "HCLTech", "HCL Technologies", "Tata Consultancy Services",
                 "ABC Staffing Solutions", "XYZ Recruitment", "Capgemini Technology Services",
                 "Randstad India", "Acme Services Pvt Ltd", "Foo Technologies Pvt Ltd",
                 "Bar Infotech", "Quick Hiring", "EXL", "UST", "Capco", "WSP in India",
                 "Rootlex Technologies Private Limited", "NetM Corporate Solutions",
                 "MasterTech Web Applications"):
        check(f"{name!r} -> EXCLUDED", company_tier(name), Tier.EXCLUDED)

    section("6. Role fit: QA titles score, software titles do not")
    for title in ("QA Automation Lead", "Senior QA Automation Engineer", "SDET",
                  "Test Automation Lead", "QA Lead", "QA Engineer", "Test Engineer",
                  "Software Engineer in Test"):
        check(f"{title!r} scores", role_score(title) > 0, True)
    check("automation lead outranks a general QA title",
          role_score("QA Automation Lead") > role_score("QA Engineer"), True)
    for title in ("Manual Tester", "Junior QA Engineer", "Fresher QA"):
        check(f"{title!r} zero", role_score(title), 0.0)
    for title in ("Software Engineer", "Backend Engineer", "QA Intern",
                  "Software Engineering Intern", "Sales Engineer", "Staff Software Engineer",
                  "Engineering Manager", "DevOps Engineer"):
        check(f"{title!r} zero", role_score(title), 0.0)


# ── DB helpers ───────────────────────────────────────────────────────────────


def _memory_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from backend.db.models import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _client(Session):
    from fastapi.testclient import TestClient

    from backend.api import main
    from backend.db.models import get_db

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[get_db] = override
    return TestClient(main.app, base_url="http://localhost:8000")


def _ledger_status(db, url: str) -> str:
    from backend import seen_ledger
    from backend.db.models import SeenJob

    row = db.query(SeenJob).filter(SeenJob.url_key == seen_ledger.url_key(url)).first()
    return row.status if row else ""


# ── 4. Ledger precedence ─────────────────────────────────────────────────────


def test_ledger() -> None:
    from backend import seen_ledger
    from backend.db.models import Job

    section("4. Ledger: mark() never downgrades a decision")
    Session = _memory_db()
    db = Session()
    seen_ledger.mark(db, "https://x.example/j/1", "dismissed", commit=True)
    seen_ledger.mark(db, "https://x.example/j/1", "cleared", commit=True)
    check("dismissed survives cleared", _ledger_status(db, "https://x.example/j/1"), "dismissed")
    seen_ledger.mark(db, "https://x.example/j/2", "visited", commit=True)
    seen_ledger.mark(db, "https://x.example/j/2", "expired", commit=True)
    check("visited survives expired", _ledger_status(db, "https://x.example/j/2"), "visited")
    seen_ledger.mark(db, "https://x.example/j/3", "applied", commit=True)
    seen_ledger.mark(db, "https://x.example/j/3", "visited", commit=True)
    check("applied survives visited", _ledger_status(db, "https://x.example/j/3"), "applied")
    seen_ledger.mark(db, "https://x.example/j/3", "rejected", commit=True)
    check("applied -> rejected allowed (same application)", _ledger_status(db, "https://x.example/j/3"), "rejected")
    seen_ledger.mark(db, "https://x.example/j/4", "filtered", commit=True)
    seen_ledger.mark(db, "https://x.example/j/4", "visited", commit=True)
    check("filtered upgrades to visited", _ledger_status(db, "https://x.example/j/4"), "visited")
    seen_ledger.unblock(db, "https://x.example/j/1")
    check("unblock still releases", _ledger_status(db, "https://x.example/j/1"), "seen")
    db.close()

    section("4. Ledger: Clear / PATCH / purge-stale / purge-experienced keep decisions")
    from backend.config import settings

    Session = _memory_db()
    client = _client(Session)
    db = Session()
    now = datetime.utcnow()
    old = now - timedelta(days=30)
    saved_region = settings.target_region
    settings.target_region = "india"
    # Clear and the purges only touch the active region, so these rows have
    # to be India jobs or the endpoint leaves them alone.
    rows = [
        Job(id="ign", title="QA Engineer", company="Google", platform="p", url="https://a.example/ign",
            location="Noida", status="ignored"),
        Job(id="rej", title="QA Engineer", company="Google", platform="p", url="https://a.example/rej",
            location="Noida", status="rejected"),
        Job(id="vis", title="QA Engineer", company="Google", platform="p", url="https://a.example/vis",
            location="Noida", status="discovered", visited_at=now),
        Job(id="new", title="QA Engineer", company="Google", platform="p", url="https://a.example/new",
            location="Noida", status="discovered"),
        Job(id="app", title="QA Engineer", company="Google", platform="p", url="https://a.example/app",
            location="Noida", status="applied"),
    ]
    db.add_all(rows)
    db.commit()
    # A dismiss recorded earlier by the extension must also survive Clear.
    seen_ledger.mark(db, "https://a.example/new", "dismissed", reason="autopilot review", commit=True)
    check("clear endpoint answers", client.delete("/api/jobs/clear?include_applied=true").status_code, 200)
    db.expire_all()
    check("Clear: ignored row -> dismissed", _ledger_status(db, "https://a.example/ign"), "dismissed")
    check("Clear: rejected row -> rejected", _ledger_status(db, "https://a.example/rej"), "rejected")
    check("Clear: visited row -> visited", _ledger_status(db, "https://a.example/vis"), "visited")
    check("Clear: prior dismissal kept", _ledger_status(db, "https://a.example/new"), "dismissed")
    check("Clear: applied row -> applied", _ledger_status(db, "https://a.example/app"), "applied")
    blocked, _ = seen_ledger.load_blocklist(db)
    check("all five block the next scan", len(blocked), 5)

    db.add(Job(id="p1", title="SE", company="B", platform="p", url="https://b.example/p1", status="discovered"))
    db.commit()
    r = client.patch("/api/jobs/p1/status", json={"status": "rejected"})
    check("PATCH answers", r.status_code, 200)
    db.expire_all()
    check("PATCH rejected is recorded", _ledger_status(db, "https://b.example/p1"), "rejected")
    db.add(Job(id="p2", title="SE", company="B", platform="p", url="https://b.example/p2", status="discovered"))
    db.commit()
    client.patch("/api/jobs/p2/status", json={"status": "ignored"})
    db.expire_all()
    check("PATCH ignored is recorded as dismissed", _ledger_status(db, "https://b.example/p2"), "dismissed")

    db.add(Job(id="st", title="QA Engineer", company="Google", platform="p", url="https://c.example/st",
               location="Noida", status="discovered", discovered_at=old, visited_at=old))
    db.add(Job(id="st2", title="QA Engineer", company="Google", platform="p", url="https://c.example/st2",
               location="Noida", status="discovered", discovered_at=old))
    db.commit()
    check("purge-stale answers", client.post("/api/jobs/purge-stale").status_code, 200)
    db.expire_all()
    check("purge-stale: opened row stays visited", _ledger_status(db, "https://c.example/st"), "visited")
    check("purge-stale: untouched row is expired", _ledger_status(db, "https://c.example/st2"), "expired")

    senior = dict(title="QA Engineer", company="Google", platform="p",
                  location="Noida", jd_text="Requires 15 years of experience in testing.")
    db.add(Job(id="ex1", url="https://d.example/ex1", status="rejected", **senior))
    db.add(Job(id="ex2", url="https://d.example/ex2", status="ignored", **senior))
    db.add(Job(id="ex3", url="https://d.example/ex3", status="discovered", **senior))
    db.commit()
    check("purge-experienced answers", client.post("/api/jobs/purge-experienced").status_code, 200)
    db.expire_all()
    check("purge-experienced: rejected kept", _ledger_status(db, "https://d.example/ex1"), "rejected")
    check("purge-experienced: ignored kept as dismissed", _ledger_status(db, "https://d.example/ex2"), "dismissed")
    check("purge-experienced: undecided is filtered", _ledger_status(db, "https://d.example/ex3"), "filtered")
    settings.target_region = saved_region
    db.close()


# ── 5. Dedup ─────────────────────────────────────────────────────────────────


def test_dedup() -> None:
    from backend import pipeline
    from backend.db.models import Job

    section("5. Dedup: same title, different requisitions survive")
    saved = pipeline.passes_hard_filters, pipeline.score_job
    pipeline.passes_hard_filters = lambda job, max_years=2, region="india": (True, "")
    pipeline.score_job = lambda job, **kw: (90.0, "stub")
    try:
        Session = _memory_db()
        db = Session()
        sde = "Software Development Engineer"
        batch = [
            {"title": sde, "company": "Amazon", "location": "Bengaluru",
             "url": "https://www.amazon.jobs/en/jobs/1001", "platform": "company_careers"},
            {"title": sde, "company": "Amazon", "location": "Hyderabad",
             "url": "https://www.amazon.jobs/en/jobs/1002", "platform": "company_careers"},
            {"title": sde, "company": "Amazon", "location": "Bengaluru",
             "url": "https://www.amazon.jobs/en/jobs/1003", "platform": "company_careers"},
            # The same Bengaluru req mirrored on another site: a real duplicate.
            {"title": sde, "company": "Amazon", "location": "Bengaluru",
             "url": "https://www.linkedin.com/jobs/view/555", "platform": "linkedin"},
            # Same URL twice with a tracking param: one row.
            {"title": sde, "company": "Amazon", "location": "Pune",
             "url": "https://www.amazon.jobs/en/jobs/1004?utm_source=x", "platform": "company_careers"},
            {"title": sde, "company": "Amazon", "location": "Pune",
             "url": "https://www.amazon.jobs/en/jobs/1004", "platform": "company_careers"},
        ]
        added = pipeline.upsert_jobs(batch, db)
        check("distinct reqs kept, mirror + repeat dropped", added, 4)
        check("two Amazon reqs in one city both stay",
              db.query(Job).filter(Job.location == "Bengaluru").count(), 2)

        # Against the DB: a new req with the same title is not a duplicate,
        # a mirror from another host is.
        more = [
            {"title": sde, "company": "Amazon", "location": "Chennai",
             "url": "https://www.amazon.jobs/en/jobs/1005", "platform": "company_careers"},
            {"title": sde, "company": "Amazon", "location": "Hyderabad",
             "url": "https://www.naukri.com/job/abc", "platform": "naukri"},
        ]
        check("DB: new req kept, cross-host mirror dropped", pipeline.upsert_jobs(more, db), 1)

        # "_" and "%" are literal, not LIKE wildcards.
        db.add(Job(id="w1", title="SE_Platform", company="Acme", platform="p",
                   url="https://acme.example/1", location="Pune"))
        db.commit()
        wild = [{"title": "SE%Platform", "company": "Acme", "location": "Pune",
                 "url": "https://other.example/2", "platform": "p"}]
        check("'%' in a title is not a wildcard", pipeline.upsert_jobs(wild, db), 1)

        reposts = [
            {"title": "Application Developer-Java & Web Technologies", "company": "IBM",
             "location": "Bengaluru, Karnataka, India",
             "url": "https://in.linkedin.com/jobs/view/application-developer-java-web-technologies-at-ibm-4488217274",
             "platform": "linkedin"},
            {"title": "Application Developer-Java & Web Technologies", "company": "IBM",
             "location": "Bengaluru, Karnataka, India",
             "url": "https://in.linkedin.com/jobs/view/application-developer-java-web-technologies-at-ibm-4487854366",
             "platform": "linkedin"},
        ]
        check("LinkedIn reposts of one opening collapse", pipeline.upsert_jobs(reposts, db), 1)
        shown = pipeline.collapse_linkedin_reposts([
            type("J", (), row)()
            for row in reposts + [
                {"title": sde, "company": "Amazon", "location": "Bengaluru",
                 "url": "https://www.amazon.jobs/en/jobs/1001"},
                {"title": sde, "company": "Amazon", "location": "Bengaluru",
                 "url": "https://www.amazon.jobs/en/jobs/1003"},
            ]
        ])
        check("the list keeps one LinkedIn repost",
              sum(1 for job in shown if job.company == "IBM"), 1)
        check("the list keeps both Amazon reqs",
              sum(1 for job in shown if job.company == "Amazon"), 2)
        db.close()
    finally:
        pipeline.passes_hard_filters, pipeline.score_job = saved


# ── 7. Apply-all selection ───────────────────────────────────────────────────


def test_apply_all() -> None:
    from backend.api import main
    from backend.applier import engine
    from backend.db.models import Job

    section("7. Apply-all: no ids means the fresh set GET /api/jobs shows, never stale rows")
    Session = _memory_db()
    client = _client(Session)
    db = Session()
    now = datetime.utcnow()
    db.add(Job(id="fresh", title="QA Engineer", company="Google", platform="p", url="https://a.example/f",
               location="Noida", status="discovered", discovered_at=now))
    db.add(Job(id="stale", title="QA Engineer", company="Google", platform="p", url="https://a.example/s",
               location="Noida", status="discovered", discovered_at=now - timedelta(days=30)))
    db.commit()

    saved_region = main.settings.target_region
    main.settings.target_region = "india"
    shown = {j["id"] for j in client.get("/api/jobs").json()}
    captured: dict = {}

    async def fake_run(job_ids=None, auto_submit=False, headless=False):
        captured["job_ids"] = job_ids
        return {"stub": True}

    class _Profile:
        def missing_required(self):
            return []

    saved_run, saved_profile = engine.run_apply_pipeline, main.load_profile
    engine.run_apply_pipeline = fake_run
    main.load_profile = lambda: _Profile()
    main._apply_status["running"] = False
    try:
        r = client.post("/api/apply", json={"job_ids": None})
        check("apply-all answers", r.status_code, 200)
        check("apply-all runs exactly what the list shows", set(captured.get("job_ids") or []), shown)
        check("stale row not applied to", "stale" in (captured.get("job_ids") or []), False)
        main._apply_status["running"] = False
        r = client.post("/api/apply", json={"job_ids": ["fresh"]})
        check("explicit ids passed through", captured.get("job_ids"), ["fresh"])
        main._apply_status["running"] = False
        check("empty id list refused (engine reads [] as all)",
              client.post("/api/apply", json={"job_ids": []}).status_code, 400)
    finally:
        engine.run_apply_pipeline = saved_run
        main.load_profile = saved_profile
        main.settings.target_region = saved_region
        main._apply_status["running"] = False
        db.close()

    section("7. Dashboard sends the ids it counted")
    page = (ROOT / "dashboard" / "app" / "(dashboard)" / "page.tsx").read_text(encoding="utf-8")
    check("handleApplyAll passes eligible ids", "api.startApply(eligible.map((j) => j.id))" in page, True)


def test_clear_region() -> None:
    from backend.config import settings
    from backend.db.models import Job

    section("4b. Clear only removes the active region")
    Session = _memory_db()
    client = _client(Session)
    db = Session()
    db.add(Job(id="in", title="QA Engineer", company="Google", platform="linkedin",
               url="https://in.example/1", location="Noida", status="discovered"))
    db.add(Job(id="eu", title="SE", company="Stripe", platform="linkedin",
               url="https://eu.example/1", location="London, United Kingdom", status="discovered"))
    db.add(Job(id="eu-vis", title="SE", company="Stripe", platform="linkedin",
               url="https://eu.example/2", location="Berlin, Germany",
               status="discovered", visited_at=datetime.utcnow()))
    db.commit()
    saved = settings.target_region
    try:
        settings.target_region = "india"
        check("india clear answers",
              client.delete("/api/jobs/clear?include_applied=true").status_code, 200)
        db.expire_all()
        left = {j.id for j in db.query(Job).all()}
        check("india clear removes Noida", "in" in left, False)
        check("india clear keeps London", "eu" in left, True)
        check("india clear keeps Berlin", "eu-vis" in left, True)
        check("remove-visited in India leaves Berlin",
              client.post("/api/jobs/purge-visited").status_code, 200)
        db.expire_all()
        left = {j.id for j in db.query(Job).all()}
        check("Berlin still there after India remove-visited", "eu-vis" in left, True)

        settings.target_region = "europe"
        check("europe clear answers",
              client.delete("/api/jobs/clear?include_applied=true").status_code, 200)
        db.expire_all()
        left = {j.id for j in db.query(Job).all()}
        check("europe clear removes London", "eu" in left, False)
        check("europe clear removes Berlin", "eu-vis" in left, False)
    finally:
        settings.target_region = saved
        db.close()


def test_europe_sponsors() -> None:
    from backend.autopilot.select import cap_jobs_per_company, sponsors_europe_visa
    from backend.scrapers.filter_utils import passes_hard_filters
    from backend.scrapers.linkedin import europe_sponsor_queries

    check("Amazon sponsors", sponsors_europe_visa("Amazon"), True)
    check("Stripe sponsors", sponsors_europe_visa("Stripe"), True)
    check("Helsing sponsors", sponsors_europe_visa("Helsing"), True)
    check("Booking.com sponsors", sponsors_europe_visa("Booking.com"), True)
    check("a recruiter is not a sponsor", sponsors_europe_visa("Harnham"), False)
    check("CHECK24 is not on the sponsor list", sponsors_europe_visa("CHECK24"), False)
    dropped = passes_hard_filters(
        {
            "title": "Software Engineer",
            "company": "Harnham",
            "location": "London, United Kingdom",
            "jd_text": "We are hiring.",
            "platform": "linkedin",
        },
        region="europe",
        require_jd=False,
    )
    check("recruiter dropped in Europe", dropped[0], False)
    rows = [{"company": "Amazon", "score": 100 - i} for i in range(10)]
    rows.append({"company": "Stripe", "score": 1})
    capped = cap_jobs_per_company(rows)
    check("eight Amazon jobs at most", sum(1 for r in capped if r["company"] == "Amazon"), 8)
    check("Stripe survives the Amazon cap", any(r["company"] == "Stripe" for r in capped), True)
    queries = europe_sponsor_queries("Software Engineer")
    check("sponsor search covers the list", len(queries) > 72, True)
    check("IBM is searched, not only the first page of names", any("ibm" in q[0] for q in queries), True)
    check("Amazon is not a LinkedIn query", all("amazon" not in q[0] for q in queries), True)
    check("Booking is searched", any("booking" in q[0] for q in queries), True)
    check("Sweden is searched", any(q[1] == "Sweden" for q in queries), True)
    both = europe_sponsor_queries(["Software Engineer", "Backend Engineer"])
    check("a second role doubles the search", len(both), len(queries) * 2)


def test_europe_scan_bounds() -> None:
    from backend.scrapers.company_careers import _role_matches
    from backend.scrapers.linkedin import linkedin_page_count
    from backend.scrapers.registry import resolve_roles

    check("europe linkedin is one page", linkedin_page_count("europe"), 1)
    check("india linkedin stays three pages", linkedin_page_count("india"), 3)
    roles = ["Software Engineer", "Backend Engineer"]
    check(
        "pricing analyst is not a software title",
        _role_matches("Pricing Analyst, AWS Monetization", roles),
        False,
    )
    check(
        "cloud support is not a software title",
        _role_matches("Cloud Support Associate", roles),
        False,
    )
    check(
        "software dev engineer matches",
        _role_matches("Software Dev Engineer - Big Data Processing", roles),
        True,
    )
    short = resolve_roles(["Software Engineer"])
    check("target role is kept", "Software Engineer" in short, True)
    check("discovery extras stay off the short list", "Data Engineer" not in short, True)


def main() -> None:
    test_europe()
    test_europe_sponsors()
    test_europe_scan_bounds()
    test_location()
    test_india_excluded_roles()
    test_india_companies()
    test_titles()
    test_experience()
    test_tiers()
    test_ledger()
    test_clear_region()
    test_dedup()
    test_apply_all()
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("All filter checks passed.")


if __name__ == "__main__":
    main()
