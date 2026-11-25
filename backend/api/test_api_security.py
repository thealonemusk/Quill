"""
The API answers only this machine, and only its own clients may change it.

Every check below was a live hole: port 8000 listened on 0.0.0.0 with no
login, the resume path could name config/.env and serve it as "the resume",
POST /api/settings let a newline write any key into .env, and a manual job's
URL could be javascript:. Nothing here touches the real .env or profile —
the settings write goes to a temp file, and the profile check is refused
before anything is written.

Run:  python backend/api/test_api_security.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from loguru import logger  # noqa: E402

logger.remove()

from fastapi.testclient import TestClient  # noqa: E402

from backend.api import main  # noqa: E402
from backend.applier import profile as profile_mod  # noqa: E402

failures: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name:58} {got!r}")
    if not ok:
        failures.append(f"{name}: expected {want!r}, got {got!r}")


def _throwaway_db():
    """Every DB-backed request in this file goes to an in-memory database."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from backend.db.models import Base, get_db

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[get_db] = override


def run() -> None:
    _throwaway_db()
    local = TestClient(main.app, base_url="http://localhost:8000")
    rebound = TestClient(main.app, base_url="http://attacker.example:8000")

    print("\nHost: DNS rebinding reaches 127.0.0.1 under a foreign name")
    check("GET /api/profile via localhost", local.get("/api/autofill/ping").status_code, 200)
    check("GET /api/profile via a rebound name is refused", rebound.get("/api/profile").status_code, 403)
    render = TestClient(main.app, base_url="https://quill-ieny.onrender.com")
    saved_token = os.environ.pop("API_TOKEN", None)
    saved_setting = main.settings.api_token
    main.settings.api_token = ""
    try:
        check("GET via the deployed Render host without a token is refused",
              render.get("/api/autofill/ping").status_code, 401)
        check("localhost still answers when the public token is unset",
              local.get("/api/autofill/ping").status_code, 200)
        os.environ["API_TOKEN"] = "quill-test-token"
        check("localhost still answers when a public token is set",
              local.get("/api/autofill/ping").status_code, 200)
        check("the deployed host rejects a wrong token",
              render.get("/api/autofill/ping", headers={"Authorization": "Bearer nope"}).status_code, 401)
        auth = {"Authorization": "Bearer quill-test-token"}
        check("the deployed host accepts the token",
              render.get("/api/autofill/ping", headers=auth).status_code, 200)
        check("API docs stay hidden on the deployed host",
              render.get("/docs", headers=auth).status_code, 404)
        check("OpenAPI stays hidden on the deployed host",
              render.get("/openapi.json", headers=auth).status_code, 404)
    finally:
        if saved_token is None:
            os.environ.pop("API_TOKEN", None)
        else:
            os.environ["API_TOKEN"] = saved_token
        main.settings.api_token = saved_setting
    other = TestClient(main.app, base_url="https://other.onrender.com")
    check("GET via a different Render host is refused", other.get("/api/profile").status_code, 403)

    print("\nOrigin: CORS blocks reading, not sending")
    # An empty body to /api/jobs/manual fails validation (422) once past the
    # origin check, and writes nothing. Never probe with an endpoint that acts:
    # an earlier version of this test used purge-visited on the real database.
    probe = "/api/jobs/manual"
    evil = {"Origin": "https://evil.example"}
    check("POST from a foreign site is refused",
          local.post(probe, json={}, headers=evil).status_code, 403)
    check("POST from the dashboard reaches the handler",
          local.post(probe, json={}, headers={"Origin": "http://localhost:3000"}).status_code, 422)
    os.environ["API_TOKEN"] = "quill-test-token"
    check("POST from the deployed dashboard reaches the handler",
          render.post(
              probe,
              json={},
              headers={
                  "Origin": "https://quill-nikhil.vercel.app",
                  "Authorization": "Bearer quill-test-token",
              },
          ).status_code, 422)
    check("POST from a foreign site to the deployed host is refused",
          render.post(
              probe,
              json={},
              headers={
                  "Origin": "https://evil.example",
                  "Authorization": "Bearer quill-test-token",
              },
          ).status_code, 403)
    if saved_token is None:
        os.environ.pop("API_TOKEN", None)
    else:
        os.environ["API_TOKEN"] = saved_token
    check("POST from an extension reaches the handler",
          local.post(probe, json={},
                     headers={"Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"}).status_code, 422)
    check("a lookalike extension origin is refused",
          local.post(probe, json={}, headers={"Origin": "chrome-extension://x.evil.example/"}).status_code, 403)

    print("\nThe resume path cannot name another file")
    for bad in ("config/.env", "../config/.env", str(ROOT / "config" / ".env"),
                "resumes/../config/.env", "resumes/master_resume.tex"):
        check(f"resume_path={bad[-28:]!r} refused",
              local.put("/api/profile", json={"resume_path": bad}).status_code, 400)
    check("a stored bad path is never served",
          profile_mod.ApplicantProfile(resume_path="config/.env").resume_file() is None
          or profile_mod.ApplicantProfile(resume_path="config/.env").resume_file().suffix == ".pdf", True)
    check("safe_resume_path accepts a real resume",
          profile_mod.safe_resume_path("resumes/Ashutosh_Jha.pdf") is not None, True)

    print("\nSettings cannot inject lines into .env")
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        env.write_text("# comment kept\nGEMINI_API_KEY=keep\n\nUSER_FULL_NAME=old\n", encoding="utf-8")
        saved_path, saved_name = main._env_path, main.settings.user_full_name
        main._env_path = lambda: env
        try:
            r = local.post("/api/settings", json={"user_full_name": "x\nOPENAI_BASE_URL=https://evil/v1"})
            check("a value with a newline is refused", r.status_code, 400)
            check("...and .env is untouched", "evil" in env.read_text(encoding="utf-8"), False)
            r = local.post("/api/settings", json={"user_full_name": "Ashutosh Jha"})
            text = env.read_text(encoding="utf-8")
            check("a normal save succeeds", r.status_code, 200)
            check("comments survive a save", "# comment kept" in text, True)
            check("the new value is written", "USER_FULL_NAME=Ashutosh Jha" in text, True)
            check("the running settings pick it up", main.settings.user_full_name, "Ashutosh Jha")
        finally:
            main._env_path = saved_path
            main.settings.user_full_name = saved_name

    print("\nJob links")
    r = local.post("/api/jobs/manual", json={"title": "t", "company": "c", "jd_text": "j",
                                             "url": "javascript:fetch('//evil')"})
    check("a javascript: job URL is refused", r.status_code, 422)

    print("\nPath-taking endpoints")
    check("tailored resume with a traversal id is refused",
          local.get("/api/autofill/resume/..%5C..%5Cconfig%5Cx").status_code, 404)
    check("tailored resume with a glob id is refused",
          local.get("/api/autofill/resume/*").status_code, 404)


if __name__ == "__main__":
    run()
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for line in failures:
            print(f"  - {line}")
        sys.exit(1)
    print("All checks passed.")
