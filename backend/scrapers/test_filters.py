"""
Job-discovery filters, dedup, company tiers and the seen ledger.

Every "must pass" case below was a real job the filters threw away, and every
"must reject" case is a guard that the fix did not loosen what the filters are
for: 0-2 years, no senior titles, India only, no body shops.

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


def _exp_ok(jd: str, max_years=2) -> bool:
    return is_job_experience_valid(
        {"title": "Software Engineer", "jd_text": jd}, max_years=max_years, require_jd=False
    )[0]


# ── 1. Location ──────────────────────────────────────────────────────────────


def test_europe() -> None:
    section("1b. Europe: UK, Ireland, Germany, Netherlands; a visa refusal drops the post")
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
        "Edinburgh, Scotland", "Belfast, Northern Ireland",
    ):
        check(f"Europe keeps {city!r}", eu(city), True)
    check("Europe rejects Bengaluru", eu("Bengaluru, Karnataka"), False)
    check("Europe rejects Paris", eu("Paris, France"), False)
    check("Europe rejects Seattle", eu("Seattle, WA"), False)
    check("Europe rejects bare Remote", eu("Remote"), False)
    check("Europe rejects London; Seattle", eu("London; Seattle, WA"), False)
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
    check("Indianapolis is not India", _loc("Indianapolis, IN"), False)
    check("Indianapolis without a state is not India", _loc("Indianapolis"), False)

    for city in ("Bengaluru, Karnataka", "Hyderabad", "Pune, Maharashtra", "Delhi NCR",
                 "Gurugram, Haryana", "NCR", "Bangalore Urban, India"):
        check(f"{city!r} accepted", _loc(city), True)
    check("multi-city 'Bengaluru; Seattle, WA' accepted", _loc("Bengaluru; Seattle, WA"), True)
    check("Chennai still excluded", _loc("Chennai, Tamil Nadu"), False)

    section("1. Location: ambiguous location, JD decides only with India-specific phrasing")
    check("Remote + 'based in Pune' accepted", _loc("Remote", "This role is based in Pune, India."), True)
    check("empty + 'Location: Hyderabad' accepted", _loc("", "Location: Hyderabad (hybrid)"), True)
    check("Multiple locations + 'based in Seattle' rejected",
          _loc("Multiple locations", "This role is based in Seattle, WA."), False)
    check("Remote + generic 'India' mention rejected",
          _loc("Remote", "Our teams span the US, Europe and India."), False)
    check("empty + JD 'Join NCR Voyix' rejected", _loc("", "Join NCR Voyix today."), False)
    check("empty + JD 'Delhi NCR office' accepted", _loc("", "Work from our Delhi NCR office."), True)
    check("India board, no location still allowed", _loc("", platform="naukri"), True)
    check("missing location still rejected", _loc(""), False)


# ── 2. Titles ────────────────────────────────────────────────────────────────


def test_titles() -> None:
    section("2. Titles: entry-level roles pass")
    for title in (
        "Software Engineer, University Graduate, 2026",
        "Software Engineer - 2026 New Grad",
        "Software Engineer, 5G RAN",
        "Software Engineer, 3D Graphics",
        "Software Engineer - VPN",
        "Software Engineer, Headless Commerce",
        "Backend Engineer, Leadership Tools",
        "Software Engineer, Cloud Architecture",
        "Software Engineer I",
        "SDE 1",
    ):
        check(f"{title!r} accepted", _title_ok(title), True)

    section("2. Titles: senior / levelled roles still rejected")
    for title in (
        "Senior Software Engineer", "Sr. Software Engineer", "Sr Software Engineer",
        "Staff Engineer", "Principal Engineer", "SDE III", "SDE 2", "SDE-2",
        "Software Engineer II", "Software Engineer III", "Software Engineer 2",
        "Software Engineer - 3", "Engineer L5", "Tech Lead", "Lead Engineer",
        "Team Leader - Backend", "Head of Engineering", "Engineering Manager",
        "VP Engineering", "Vice President, Technology", "Solutions Architect",
        "Software Engineering Intern",
    ):
        check(f"{title!r} rejected", _title_ok(title), False)


# ── 3. Experience ────────────────────────────────────────────────────────────


def test_experience() -> None:
    section("3. Experience: 'N+ years' is a minimum")
    check("'1+ years' accepted", _exp_ok("Requirements: 1+ years of experience with Java."), True)
    check("'0+ years' accepted", _exp_ok("0+ years of experience."), True)
    check("'2+ years' accepted (cap 2)", _exp_ok("2+ years of experience."), True)
    check("'0-2 years' accepted", _exp_ok("0-2 years of experience."), True)
    check("'3+ years' rejected", _exp_ok("3+ years of experience."), False)
    check("'5+ yrs' rejected", _exp_ok("Minimum 5+ yrs in backend."), False)
    check("'2-4 years' still rejected (top above cap)", _exp_ok("2-4 years of experience."), False)
    check("'at least 3 years' rejected", _exp_ok("At least 3 years of experience."), False)

    section("3. Experience: max_years is honoured, default unchanged")
    check("default cap is 2: '3 years' rejected", is_job_experience_valid(
        {"title": "Software Engineer", "jd_text": "3 years of experience."}, require_jd=False)[0], False)
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
        ("Applied Materials", Tier.UNKNOWN),
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
    ):
        check(f"{name!r} -> {tier.name}", company_tier(name), tier)

    section("6. Tiers: consultancies and intermediaries still excluded")
    for name in ("Atos", "iGate", "IGATE Global Solutions", "Infosys BPM", "Infosys",
                 "Wipro Limited", "HCLTech", "HCL Technologies", "Tata Consultancy Services",
                 "ABC Staffing Solutions", "XYZ Recruitment", "Capgemini Technology Services",
                 "Randstad India", "Acme Services Pvt Ltd", "Foo Technologies Pvt Ltd",
                 "Bar Infotech", "Quick Hiring"):
        check(f"{name!r} -> EXCLUDED", company_tier(name), Tier.EXCLUDED)

    section("6. Role fit: word-bounded ROLE_BAD")
    for title in ("Software Engineer, Internal Tools", "Software Engineer, International Payments",
                  "Software Engineer, Internet Infrastructure", "Software Engineer, Salesforce Platform",
                  "Software Engineer, Customer Support Platform", "Backend Engineer, Leadership Tools"):
        check(f"{title!r} scores", role_score(title) > 0, True)
    for title in ("Software Engineering Intern", "Software Engineer Internship", "Sales Engineer",
                  "Technical Support Engineer", "Customer Support Executive", "Marketing Manager",
                  "Engineering Manager", "Tech Lead", "Staff Software Engineer", "QA Engineer",
                  "Head of Engineering", "VP Engineering"):
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
        Job(id="ign", title="SE", company="A", platform="p", url="https://a.example/ign",
            location="Bengaluru", status="ignored"),
        Job(id="rej", title="SE", company="A", platform="p", url="https://a.example/rej",
            location="Bengaluru", status="rejected"),
        Job(id="vis", title="SE", company="A", platform="p", url="https://a.example/vis",
            location="Bengaluru", status="discovered", visited_at=now),
        Job(id="new", title="SE", company="A", platform="p", url="https://a.example/new",
            location="Bengaluru", status="discovered"),
        Job(id="app", title="SE", company="A", platform="p", url="https://a.example/app",
            location="Bengaluru", status="applied"),
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

    db.add(Job(id="st", title="SE", company="C", platform="p", url="https://c.example/st",
               location="Bengaluru", status="discovered", discovered_at=old, visited_at=old))
    db.add(Job(id="st2", title="SE", company="C", platform="p", url="https://c.example/st2",
               location="Bengaluru", status="discovered", discovered_at=old))
    db.commit()
    check("purge-stale answers", client.post("/api/jobs/purge-stale").status_code, 200)
    db.expire_all()
    check("purge-stale: opened row stays visited", _ledger_status(db, "https://c.example/st"), "visited")
    check("purge-stale: untouched row is expired", _ledger_status(db, "https://c.example/st2"), "expired")

    senior = dict(title="Senior Software Engineer", company="D", platform="p",
                  location="Bengaluru", jd_text="")
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
    db.add(Job(id="fresh", title="SE", company="A", platform="p", url="https://a.example/f",
               location="Bengaluru", status="discovered", discovered_at=now))
    db.add(Job(id="stale", title="SE", company="A", platform="p", url="https://a.example/s",
               location="Bengaluru", status="discovered", discovered_at=now - timedelta(days=30)))
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
    db.add(Job(id="in", title="SE", company="A", platform="linkedin",
               url="https://in.example/1", location="Bengaluru", status="discovered"))
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
        check("india clear removes Bengaluru", "in" in left, False)
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
    check("sponsor search stays bounded", len(queries) <= 72, True)
    check("Amazon is not a LinkedIn query", all("amazon" not in q[0] for q in queries), True)
    check("Booking is searched", any("booking" in q[0] for q in queries), True)


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
