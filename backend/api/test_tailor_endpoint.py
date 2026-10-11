"""
Tailoring from the extension: which description is used, and what the
extension is told.

The tailor itself is stubbed — this checks the endpoint's decisions, not the
resume pipeline (backend/resume/test_resume.py does that). Everything runs on
an in-memory database; nothing touches tempoapply.db or spends LLM quota.

Run:  python backend/api/test_tailor_endpoint.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from loguru import logger  # noqa: E402

logger.remove()

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from backend.api import autofill, main  # noqa: E402
from backend.db.models import Base, Job, get_db  # noqa: E402
from backend.resume import tailor as tailor_mod  # noqa: E402

failures: list[str] = []
EXT = {"Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"}
SAVED_JD = "Saved description from the scanner. " * 20      # > 300 chars
PAGE_JD = "Description read off the page. " * 20
SELECTED = "Text the user selected on purpose. " * 20


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name:56} {str(got)[:40]!r}")
    if not ok:
        failures.append(f"{name}: expected {want!r}, got {got!r}")


def setup():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(Job(id="known-job-0001", title="Backend Engineer", company="Acme", platform="test",
                   url="https://boards.greenhouse.io/acme/jobs/1", jd_text=SAVED_JD, status="discovered"))
        db.add(Job(id="bare-job-00002", title="SDE", company="Beta", platform="test",
                   url="https://jobs.lever.co/beta/2", jd_text="", status="discovered"))
        db.commit()

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[get_db] = override
    autofill._session_factory = Session
    return Session


def run() -> None:
    Session = setup()
    seen: list = []

    def fake_tailor(jd_text, job_title="", company="", job_id="", **_):
        seen.append(jd_text)
        time.sleep(0.2)
        return SimpleNamespace(ok=True, error="", report={
            "pdf": {"pages": 1}, "model_rewrites": 3, "rejected": [{}],
            "reordered": [{}], "skills_reordered": [], "notes": "fake",
        })

    tailor_mod.tailor, real = fake_tailor, tailor_mod.tailor
    client = TestClient(main.app, base_url="http://localhost:8000")

    def start(**body):
        body.setdefault("url", "https://boards.greenhouse.io/acme/jobs/1")
        return client.post("/api/autofill/tailor", json=body, headers=EXT)

    def wait(job_id):
        for _ in range(50):
            s = client.get(f"/api/autofill/tailor/{job_id}").json()
            if s.get("state") != "running":
                return s
            time.sleep(0.05)
        return s

    try:
        print("\nWhich description is used")
        r = start(jd_text=PAGE_JD, jd_source="page").json()
        check("a known job uses its saved description", r.get("source"), "the saved job description")
        done = wait(r["job_id"])
        check("the run finishes", done.get("state"), "done")
        check("...and the stub got the saved JD", seen[-1] == " ".join(SAVED_JD.split()), True)
        check("status names the PDF", done.get("pdf_url"), f"/api/autopilot/resume/{r['job_id']}")
        check("status reports what changed", (done.get("reworded"), done.get("refused"), done.get("pages")), (3, 1, 1))
        with Session() as db:
            check("an open job is marked tailored", db.get(Job, "known-job-0001").status, "tailored")

        r = start(jd_text=SELECTED, jd_source="selection").json()
        wait(r["job_id"])
        check("a selection beats the saved description", r.get("source"), "your selection")

        r = start(url="https://jobs.lever.co/beta/2", jd_text=PAGE_JD, jd_source="page").json()
        wait(r["job_id"])
        check("a job with no saved JD uses the page's", r.get("source"), "this page")
        with Session() as db:
            check("...and keeps it for next time", len(db.get(Job, "bare-job-00002").jd_text) >= 300, True)

        r = start(url="https://acme.wd5.myworkdayjobs.com/x/job/9/apply", title="Platform Engineer",
                  company="Acme", jd_text=PAGE_JD, jd_source="remembered").json()
        wait(r["job_id"])
        check("a remembered posting-page JD is named as such", r.get("source"), "the posting page")
        with Session() as db:
            created = db.get(Job, r["job_id"])
            check("an unknown page becomes a job", bool(created and created.platform == "extension"), True)

        print("\nRefusals")
        bad = start(url="https://example.com/careers/x", jd_text="too short", jd_source="page")
        check("no usable description -> 400", bad.status_code, 400)
        check("...with advice to select it", "Select the description" in bad.json().get("detail", ""), True)
        check("a non-http URL -> 400", start(url="file:///etc/passwd", jd_text=PAGE_JD).status_code, 400)

        # One run at a time: a second start while one is running is refused.
        slow_done = []

        def slow(jd_text, **k):
            time.sleep(0.6)
            slow_done.append(1)
            return fake_tailor(jd_text, **k)

        tailor_mod.tailor = slow
        first = start(jd_text=PAGE_JD, jd_source="page")
        second = start(jd_text=PAGE_JD, jd_source="page")
        check("a second run while one is running -> 409", second.status_code, 409)
        wait(first.json()["job_id"])

        check("unknown job status -> 404", client.get("/api/autofill/tailor/nope-nope-nope").status_code, 404)
        check("a foreign site cannot start one",
              client.post("/api/autofill/tailor", json={"url": "https://x.io/j", "jd_text": PAGE_JD},
                          headers={"Origin": "https://evil.example"}).status_code, 403)

        tailor_mod.tailor = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        crashed = wait(start(jd_text=PAGE_JD, jd_source="page").json()["job_id"])
        check("a crash is reported, not raised", (crashed.get("state"), crashed.get("ok")), ("failed", False))
    finally:
        tailor_mod.tailor = real
        autofill._session_factory = None
        main.app.dependency_overrides.clear()


if __name__ == "__main__":
    run()
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for line in failures:
            print(f"  - {line}")
        sys.exit(1)
    print("All checks passed.")
