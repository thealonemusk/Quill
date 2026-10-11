"""
Tests for the autofill extension.

Default run (no flags) needs nothing but Playwright's chromium: it loads the
local harness, injects `scrape.js` and `fill.js`, resolves through the real
backend resolver in-process, and asserts on the resulting DOM.

`--integration` loads the actual unpacked extension into Chrome and drives the
widget, which is the only way to cover the service worker, chrome.runtime
messaging and the cross-frame fan-out that Glassdoor's embedded form needs. It
requires the API server to be running (`python run.py`).

    python extension/test/run_tests.py
    python extension/test/run_tests.py --integration
"""
from __future__ import annotations

import asyncio
import functools
import http.server
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
EXT = ROOT / "extension"
sys.path.insert(0, str(ROOT))

PORT = 8765
# The API refuses any Host that is not localhost (DNS-rebinding guard in
# backend/api/main.py), and TestClient's default host is "testserver".
LOCAL_API = "http://localhost:8000"
failures: list[str] = []


def check(name: str, got, want) -> None:
    ok = (want in got) if isinstance(want, str) and isinstance(got, str) and want else got == want
    if isinstance(want, str) and want.startswith("!"):
        ok = want[1:] != got
    print(f"  {'PASS' if ok else 'FAIL'}  {name:26} {str(got)[:52]!r}")
    if not ok:
        failures.append(f"{name}: expected {want!r}, got {got!r}")


# ── Unit: scraper + filler against the real resolver ─────────────────────────

async def run_unit() -> None:
    from fastapi.testclient import TestClient
    from playwright.async_api import async_playwright

    from backend.api.main import app

    client = TestClient(app, base_url=LOCAL_API)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        await page.goto((EXT / "test" / "harness.html").as_uri())
        for name in ("react-bridge.js", "scrape.js", "fill.js"):
            await page.add_script_tag(path=str(EXT / "src" / name))

        fields = await page.evaluate("() => { window.__S = window.__TA.scrape(); return window.__S.fields; }")
        print(f"\nScraped {len(fields)} fields from the Workday/Glassdoor/Greenhouse harness")

        data = client.post(
            "/api/autofill/resolve",
            json={
                "url": "https://acme.wd5.myworkdayjobs.com/en-US/careers/job/Backend-Engineer_R-1/apply",
                "page_title": await page.title(),
                "fields": fields,
            },
        ).json()
        resume = client.get("/api/autofill/resume").json()

        await page.evaluate("() => window.__TA.injectStyles()")
        result = await page.evaluate(
            """async ([fills, resume]) => {
                 const file = window.__TA.base64ToFile(resume.b64, resume.filename, resume.mime);
                 const out = await window.__TA.applyFills(fills, window.__S.elements, file);
                 return { applied: out.applied.length, failed: out.failed.map(f => `${f.action}:${f.label}`) };
               }""",
            [data["fills"], resume],
        )
        print(f"Applied {result['applied']}, failed {len(result['failed'])} {result['failed']}")

        dom = await page.evaluate(
            """() => {
                 const v = (s) => { const e = document.querySelector(s); return e ? e.value : '<missing>'; };
                 const t = (s) => { const e = document.querySelector(s); return e ? e.textContent.trim() : '<missing>'; };
                 const f = (s) => { const e = document.querySelector(s); return e && e.files.length ? e.files[0].name : ''; };
                 const r = (n) => { const e = document.querySelector(`input[name="${n}"]:checked`); return e ? e.value : ''; };
                 return {
                   wd_first: v('#wd-first'), wd_city: v('#wd-city'), wd_phone: v('#wd-phone'),
                   wd_country: t('[data-automation-id="countryDropdown"]'),
                   wd_phone_type: t('[data-automation-id="phone-device-type"]'),
                   wd_source: t('[data-automation-id="multiSelectContainer"]'),
                   wd_sponsor: t('[data-automation-id="sponsorshipDropdown"]'),
                   wd_auth: t('[data-automation-id="workAuthDropdown"]'),
                   wd_resume: f('#wd-resume'),
                   gd_email: v('#gd-email'), gd_relocate: r('relocate'), gd_sponsor: r('sponsor'),
                   gh_school: v('#gh-school'), gh_degree: v('#gh-degree'), gh_gender: v('#gh-gender'),
                   gh_hear: t('.select__control'), gh_start: v('#gh-start'), gh_why: v('#gh-why'),
                   gh_resume: f('#gh-resume'), gh_cover: f('#gh-cover'),
                   gh_consent: document.querySelector('input[name="consent"]').checked,
                   honeypot: v('input[name="website_url"]'),
                 };
               }"""
        )

        print("\nWorkday — custom dropdowns, no <select> anywhere")
        check("first name", dom["wd_first"], "Ashutosh")
        check("city", dom["wd_city"], "Noida")
        # National, not E.164: the form has its own country-code control, and
        # a "+91" typed into the number box on top of that is either rejected
        # by validation or submitted as +91+91.
        check("phone has no country code", dom["wd_phone"], "9939964663")
        check("phone country code is separate", dom["wd_phone"].startswith("+"), False)
        check("country dropdown", dom["wd_country"], "India")
        check("phone device type", dom["wd_phone_type"], "Mobile")
        check("source multiselect", dom["wd_source"], "Company Website")
        check("sponsorship", dom["wd_sponsor"], "No")
        check("work authorization", dom["wd_auth"], "Yes")
        check("resume upload", dom["wd_resume"], "!")

        print("\nGlassdoor — fieldset/legend radio groups")
        check("email", dom["gd_email"], "@")
        check("relocate radio", dom["gd_relocate"], "yes")
        check("sponsorship radio", dom["gd_sponsor"], "no")

        print("\nGreenhouse — native selects and a react-select combobox")
        check("school select", dom["gh_school"], "National Institute")
        check("degree select", dom["gh_degree"], "B.Tech")
        check("gender select", dom["gh_gender"], "Decline")
        check("how-heard combobox", dom["gh_hear"], "Company website")
        check("date input is ISO", len(dom["gh_start"]), 10)
        check("consent checkbox", dom["gh_consent"], True)
        check("resume upload", dom["gh_resume"], "!")

        print("\nMust NOT be touched")
        check("spam trap stays empty", dom["honeypot"], "")
        check("cover-letter input", dom["gh_cover"], "")
        check("subjective question", dom["gh_why"], "")
        check("flagged for the human", "Why do you want to work here?" in " ".join(data["unresolved"]), True)

        if errors:
            failures.append(f"page errors: {errors[:3]}")
        await browser.close()


# ── Unit: Workday's repeating My Experience step ─────────────────────────────

async def run_sections() -> None:
    """Work Experience / Education / Websites start empty behind an Add button."""
    from fastapi.testclient import TestClient
    from playwright.async_api import async_playwright

    from backend.api.main import app

    client = TestClient(app, base_url=LOCAL_API)
    url = "https://acme.wd5.myworkdayjobs.com/en-US/careers/job/Backend-Engineer_R-1/apply"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        await page.goto((EXT / "test" / "harness-experience.html").as_uri())
        for name in ("react-bridge.js", "scrape.js", "fill.js"):
            await page.add_script_tag(path=str(EXT / "src" / name))

        before = await page.evaluate("() => window.__TA.scrape().fields.length")
        first = client.post(
            "/api/autofill/resolve",
            json={"url": url, "page_title": "My Experience",
                  "fields": await page.evaluate("() => window.__TA.scrape().fields")},
        ).json()

        added = await page.evaluate("async (n) => window.__TA.expandSections(n)", first["sections_needed"])
        fields = await page.evaluate("() => { window.__S = window.__TA.scrape(); return window.__S.fields; }")

        data = client.post(
            "/api/autofill/resolve",
            json={"url": url, "page_title": "My Experience", "fields": fields},
        ).json()
        resume = client.get("/api/autofill/resume").json()
        await page.evaluate("() => window.__TA.injectStyles()")
        out = await page.evaluate(
            """async ([fills, resume]) => {
                 const file = window.__TA.base64ToFile(resume.b64, resume.filename, resume.mime);
                 const r = await window.__TA.applyFills(fills, window.__S.elements, file);
                 return { applied: r.applied.length, failed: r.failed.map(f => `${f.action}:${f.label}`) };
               }""",
            [data["fills"], resume],
        )

        dom = await page.evaluate(
            """() => {
                 const at = (auto, sel) => {
                   const p = document.querySelector(`[data-automation-id="${auto}"]`);
                   if (!p) return '<no entry>';
                   const e = p.querySelector(sel);
                   return e ? (e.value ?? e.textContent.trim()) : '<missing>';
                 };
                 const checked = (auto, sel) => {
                   const p = document.querySelector(`[data-automation-id="${auto}"]`);
                   const e = p && p.querySelector(sel);
                   return e ? e.checked : false;
                 };
                 return {
                   e1_title: at('workExperience-1','[data-automation-id="jobTitle"]'),
                   e1_company: at('workExperience-1','[data-automation-id="company"]'),
                   e1_desc: at('workExperience-1','[data-automation-id="description"]'),
                   e1_current: checked('workExperience-1','[data-automation-id="currentlyWorkHere"]'),
                   e2_title: at('workExperience-2','[data-automation-id="jobTitle"]'),
                   e2_company: at('workExperience-2','[data-automation-id="company"]'),
                   school: at('education-1','[data-automation-id="school"]'),
                   degree: at('education-1','[data-automation-id="multiSelectContainer"]'),
                   field: at('education-1','[data-automation-id="fieldOfStudy"]'),
                   gpa: at('education-1','[data-automation-id="gpa"]'),
                   w1: at('websitePanelSet-1','[data-automation-id="website"]'),
                   w3: at('websitePanelSet-3','[data-automation-id="website"]'),
                   skills: document.querySelectorAll('[data-chips] .chip').length,
                 };
               }"""
        )

        print(
            f"\nWorkday My Experience — {before} fields before, {added} entries opened, "
            f"{len(fields)} after; applied {out['applied']}, failed {out['failed']}"
        )
        check("nothing to fill at first", before <= 2, True)
        check("opened every entry", added, 6)
        check("entry 1 job title", dom["e1_title"], "Software Engineer")
        check("entry 1 company", dom["e1_company"], "Paytm")
        check("entry 1 description", dom["e1_desc"], "Indonesia IoT")
        check("entry 1 currently here", dom["e1_current"], True)
        check("entry 2 is the 2nd job", dom["e2_title"], "Software Developer Intern")
        check("entry 2 company", dom["e2_company"], "Denr")
        check("education school", dom["school"], "National Institute")
        check("education degree", dom["degree"], "B.Tech")
        check("education field", dom["field"], "Computer Science")
        check("education gpa", dom["gpa"], "7.79")
        check("website 1 = linkedin", dom["w1"], "linkedin.com")
        check("website 3 = portfolio", dom["w3"], "is-a.dev")
        check("skills picked one by one", dom["skills"] >= 5, True)
        check("no field refused", len(out["failed"]), 0)

        if errors:
            failures.append(f"page errors: {errors[:3]}")
        await browser.close()


# ── Repeating entries with unrecognisable container ids ──────────────────────

UNKNOWN_IDS_HTML = """
<h3>Work Experience</h3>
<div data-automation-id="panelSet-abc-1">
  <label for="t1">Job Title</label><input id="t1">
  <label for="c1">Company</label><input id="c1">
  <label for="d1">Role Description</label><textarea id="d1"></textarea>
</div>
<div data-automation-id="panelSet-abc-2">
  <label for="t2">Job Title</label><input id="t2">
  <label for="c2">Company</label><input id="c2">
  <label for="d2">Role Description</label><textarea id="d2"></textarea>
</div>
<h3>Websites</h3>
<div data-automation-id="panelSet-xyz-1"><label for="u1">URL</label><input id="u1"></div>
<div data-automation-id="panelSet-xyz-2"><label for="u2">URL</label><input id="u2"></div>
"""


async def run_unknown_containers() -> None:
    """
    The real failure: a tenant whose entry containers are not named
    workExperience-1. Untagged fields fall through to the flat resolver, which
    has no alias for a bare "Company" or "Role Description", so they stay empty.
    Entry membership has to be recoverable from repetition order alone.
    """
    from fastapi.testclient import TestClient
    from playwright.async_api import async_playwright

    from backend.api.main import app

    client = TestClient(app, base_url=LOCAL_API)
    print("\nRepeating entries when the container ids are unknown")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(UNKNOWN_IDS_HTML)
        await page.add_script_tag(path=str(EXT / "src" / "scrape.js"))
        fields = await page.evaluate("() => window.__TA.scrape().fields")

        def tagged(label, index):
            return next(
                (f for f in fields
                 if f["label"].strip().lower() == label.lower()
                 and f.get("section_index") == index),
                None,
            )

        check("first Company is entry 1", bool(tagged("Company", 1)), True)
        check("second Company is entry 2", bool(tagged("Company", 2)), True)
        check("first Role Description is entry 1", bool(tagged("Role Description", 1)), True)
        check("second Role Description is entry 2", bool(tagged("Role Description", 2)), True)
        check("Company tagged as experience",
              (tagged("Company", 1) or {}).get("section_kind"), "experience")
        check("URL tagged as website", (tagged("URL", 1) or {}).get("section_kind"), "website")
        check("second URL is entry 2", bool(tagged("URL", 2)), True)

        # "Fill a section" on employer 2's block. Numbered inside the block
        # alone it came back as entry 1 and was overwritten with employer 1.
        scoped = await page.evaluate(
            """() => window.__TA.scrape(document.querySelector('[data-automation-id="panelSet-abc-2"]'))
                        .fields.map(f => ({ label: f.label, kind: f.section_kind, index: f.section_index,
                                            idx: f.idx, tag: f.tag, type: f.type }))"""
        )
        check("section run holds only its block", len(scoped), 3)
        check("section run: Company is entry 2",
              next((f["index"] for f in scoped if f["label"] == "Company"), None), 2)
        section_fill = client.post("/api/autofill/resolve", json={
            "url": "https://acme.wd5.myworkdayjobs.com/x/apply", "page_title": "My Experience",
            "overwrite": True,
            "fields": [{"idx": f["idx"], "label": f["label"], "tag": f["tag"], "type": f["type"],
                        "section_kind": f["kind"], "section_index": f["index"]} for f in scoped],
        }).json()
        company_idx = next(f["idx"] for f in scoped if f["label"] == "Company")
        check("section run writes employer 2",
              next((f["value"] for f in section_fill["fills"] if f["idx"] == company_idx), ""), "Denr")

        data = client.post("/api/autofill/resolve", json={
            "url": "https://acme.wd5.myworkdayjobs.com/x/apply",
            "page_title": "My Experience", "fields": fields,
        }).json()
        values = {f["label"]: f["value"] for f in data["fills"] if f["action"] != "skip"}
        filled = [f for f in data["fills"] if f["action"] != "skip"]

        # 2 entries x (Job Title, Company, Role Description) + 2 URLs
        check("every field resolved", len(filled), 8)
        by_idx = {}
        for f in data["fills"]:
            if f["action"] != "skip" and f["key"]:
                by_idx[f["key"]] = by_idx.get(f["key"], 0) + 1
        check("employer 1 is Paytm",
              any(f["value"] == "Paytm" for f in filled), True)
        check("employer 2 is the internship",
              any("Denr" in f["value"] for f in filled), True)
        check("websites are three different links",
              len({f["value"] for f in filled if f["value"].startswith("http")}), 2)

        await browser.close()


async def run_tenant_variations() -> None:
    """
    Answers that differ per Workday tenant, all reported from live forms.

    No browser needed — these are resolver decisions, and every one of them
    silently left a required field blank rather than erroring.
    """
    from fastapi.testclient import TestClient

    from backend.api.main import app

    client = TestClient(app, base_url=LOCAL_API)
    print("\nTenant variations — device type, phone format, named links, split dates")

    def resolve(fields, url="https://acme.wd5.myworkdayjobs.com/x/apply"):
        data = client.post("/api/autofill/resolve", json={
            "url": url, "page_title": "Application", "fields": fields,
        }).json()
        return {f["idx"]: f for f in data["fills"]}

    def field(idx, label, **kw):
        base = {"idx": idx, "label": label, "tag": "input", "type": "text"}
        base.update(kw)
        return base

    # Phone Device Type: the taxonomy is per tenant. "Mobile" is preferred
    # where it exists; a tenant offering only Phone/Main must still answer.
    out = resolve([
        field("1", "Phone Device Type", tag="select", options=["Select", "Home", "Mobile", "Pager"]),
        field("2", "Phone Device Type", tag="select", options=["Select", "Phone", "Main", "Fax"]),
        field("3", "Phone Device Type", tag="button", role="combobox"),
    ])
    check("device type prefers Mobile", out["1"]["value"], "Mobile")
    check("device type falls back to Phone", out["2"]["value"], "Phone")
    check("device type not skipped", out["2"]["action"], "select")
    check("listbox gets the fallback list", "Main" in out["3"]["values"], True)
    check("listbox tries Mobile first", out["3"]["values"][0], "Mobile")

    # Phone number: the country code lives in its own control.
    out = resolve([
        field("1", "Phone Number", type="tel"),
        field("2", "Country Phone Code", tag="button", role="combobox"),
    ])
    check("phone number is national", out["1"]["value"], "9939964663")
    check("phone number has no +", out["1"]["value"].startswith("+"), False)
    check("country code is separate", out["2"]["value"], "+91")

    # A link box that names the network it wants is answered by name, not by
    # the position of its panel.
    out = resolve([
        field("1", "Please provide your LinkedIn profile",
              section_kind="website", section_index=2),
        field("2", "URL", section_kind="website", section_index=2),
        field("3", "Social Network URLs"),
    ])
    check("named LinkedIn box gets LinkedIn", "linkedin.com" in out["1"]["value"], True)
    check("unnamed panel 2 still positional", "github.com" in out["2"]["value"], True)
    check("Social Network URLs resolves", "linkedin.com" in out["3"]["value"], True)

    # Split date boxes. Workday labels them "Month"/"Year"; the scraper
    # qualifies them with their From/To group, and a current job leaves To blank.
    out = resolve([
        field("1", "From Month", section_kind="experience", section_index=1),
        field("2", "From Year", section_kind="experience", section_index=1),
        field("3", "To Month", section_kind="experience", section_index=1),
        field("4", "I currently work here", tag="input", type="checkbox",
              section_kind="experience", section_index=1),
        field("5", "To Month", section_kind="experience", section_index=2),
        field("6", "I currently work here", tag="input", type="checkbox",
              section_kind="experience", section_index=2),
    ])
    check("Paytm starts 01", out["1"]["value"], "01")
    check("Paytm starts 2025", out["2"]["value"], "2025")
    check("current job leaves To blank", out["3"]["action"], "skip")
    check("Paytm is ticked current", out["4"]["action"], "checkbox")
    check("past job keeps its end month", out["5"]["value"], "06")
    check("internship is NOT ticked current", out["6"]["action"], "skip")


ENTER_SKILLS_HTML = """
<!doctype html><meta charset="utf-8"><body>
  <div id="blockA">
    <label for="fn">First Name</label><input id="fn">
    <label for="ln">Last Name</label><input id="ln">
  </div>
  <div id="blockB">
    <label for="em">Email</label><input id="em">
    <label for="ph">Phone Number</label><input id="ph" type="tel">
  </div>
  <div data-automation-id="formField-skills">
    <label id="sk-label">Skills</label>
    <div data-automation-id="multiSelectContainer">
      <span class="chips"></span>
      <input id="sk" role="combobox" data-automation-id="searchBox" aria-labelledby="sk-label">
    </div>
  </div>
  <script>
    // A tag input with no listbox whatsoever: the only way in is type + Enter,
    // which is exactly how the failing Workday tenant behaves.
    var inp = document.getElementById('sk');
    inp.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      var v = inp.value.trim();
      if (!v) return;
      var chip = document.createElement('span');
      chip.setAttribute('data-automation-id', 'selectedItem');
      chip.textContent = v;
      document.querySelector('.chips').appendChild(chip);
      inp.value = '';
    });
  </script>
</body>
"""


async def run_enter_commit_and_sections() -> None:
    """
    Two things the Workday skills picker forced.

    A picker with no listbox has to be driven by Enter, and a long form has to
    be fillable one block at a time.
    """
    from playwright.async_api import async_playwright

    print("\nEnter-commit pickers and section-scoped scraping")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(ENTER_SKILLS_HTML)
        await page.add_script_tag(path=str(EXT / "src" / "scrape.js"))
        await page.add_script_tag(path=str(EXT / "src" / "react-bridge.js"))
        await page.add_script_tag(path=str(EXT / "src" / "fill.js"))

        # Section scoping: the same page, three different answers.
        counts = await page.evaluate(
            """() => ({
                 all: window.__TA.scrape().fields.length,
                 a: window.__TA.scrape(document.querySelector('#blockA')).fields.length,
                 b: window.__TA.scrape(document.querySelector('#blockB')).fields.length,
                 bLabels: window.__TA.scrape(document.querySelector('#blockB'))
                            .fields.map(f => f.label),
               })"""
        )
        check("whole page sees every field", counts["all"] >= 5, True)
        check("section A is just its two", counts["a"], 2)
        check("section B is just its two", counts["b"], 2)
        check("section B holds Email", any("Email" in l for l in counts["bLabels"]), True)
        check("section B excludes First Name",
              any("First Name" in l for l in counts["bLabels"]), False)

        # Enter-commit: no listbox exists, so only a keypress can land a value.
        committed = await page.evaluate(
            """async () => {
                 const { fields, elements } = window.__TA.scrape();
                 const skill = fields.find(f => (f.label || '').toLowerCase().includes('skill'));
                 const res = await window.__TA.applyFills(
                   [{ idx: skill.idx, action: 'multiselect', value: 'Java',
                      values: ['Java', 'Python', 'Docker'], label: 'Skills', confidence: 'low' }],
                   elements, null);
                 return {
                   chips: Array.from(document.querySelectorAll('[data-automation-id="selectedItem"]'))
                            .map(c => c.textContent),
                   applied: res.applied.length,
                   failed: res.failed.length,
                   leftover: document.getElementById('sk').value,
                 };
               }"""
        )
        check("every skill committed", committed["chips"], ["Java", "Python", "Docker"])
        check("picker reported success", committed["applied"], 1)
        check("nothing failed", committed["failed"], 0)
        check("search box left clean", committed["leftover"], "")

        await browser.close()


# A picker whose options come from a server, which is what Workday's Skills and
# Degree prompts really are. The menu never goes blank between values: it holds
# the PREVIOUS query's rows until the new ones arrive. Deciding on the first
# non-empty render therefore reads a stale list and answers "no match" — the
# failure that looks like "manually searching and clicking works, the extension
# does not".
ASYNC_PROMPT_HTML = """
<!doctype html><meta charset="utf-8"><body>
  <div data-automation-id="formField-skills">
    <label id="sk-label">Skills</label>
    <div data-automation-id="multiSelectContainer">
      <span class="chips"></span>
      <input id="sk" role="combobox" data-automation-id="searchBox" aria-labelledby="sk-label">
    </div>
  </div>
  <div id="menu"></div>
  <script>
    var TAXONOMY = {
      java: ['Java', 'JavaScript', 'Java EE'],
      python: ['Python', 'Python 3'],
      sql: ['SQL', 'SQL Server']
    };
    var inp = document.getElementById('sk');
    var menu = document.getElementById('menu');
    var timer = null;
    window.__latency = 500;

    function render(rows) {
      menu.innerHTML = '';
      rows.forEach(function (text) {
        var row = document.createElement('div');
        row.setAttribute('data-automation-id', 'promptOption');
        row.textContent = text;
        row.addEventListener('click', function () {
          var chip = document.createElement('span');
          chip.setAttribute('data-automation-id', 'selectedItem');
          chip.textContent = text;
          document.querySelector('.chips').appendChild(chip);
          inp.value = '';
          // The menu keeps the rows it was showing, exactly as Workday does.
        });
        menu.appendChild(row);
      });
    }

    inp.addEventListener('input', function () {
      var q = inp.value.trim().toLowerCase();
      if (timer) clearTimeout(timer);
      if (!q) return;
      // Nothing changes on screen yet: the old rows stay put while the
      // request is in flight.
      timer = setTimeout(function () {
        render(TAXONOMY[q] || ['No matching results']);
      }, window.__latency);
    });

    // Enter commits nothing here: a taxonomy prompt only accepts its own rows.
  </script>
</body>
"""


async def run_async_prompt() -> None:
    """
    A server-backed picker must be given time to answer.

    The menu holds the previous value's rows while the new ones are fetched, so
    a picker that decides on the first non-empty render picks nothing from the
    second value onwards.
    """
    from playwright.async_api import async_playwright

    print("\nServer-backed prompt (stale rows before the real answer)")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.set_content(ASYNC_PROMPT_HTML)
        await page.add_script_tag(path=str(EXT / "src" / "scrape.js"))
        await page.add_script_tag(path=str(EXT / "src" / "react-bridge.js"))
        await page.add_script_tag(path=str(EXT / "src" / "fill.js"))

        out = await page.evaluate(
            """async () => {
                 const started = Date.now();
                 const { fields, elements } = window.__TA.scrape();
                 const skill = fields.find(f => (f.label || '').toLowerCase().includes('skill'));
                 const res = await window.__TA.applyFills(
                   [{ idx: skill.idx, action: 'multiselect', value: 'Java',
                      values: ['Java', 'Python', 'SQL'], label: 'Skills', confidence: 'low' }],
                   elements, null);
                 return {
                   chips: Array.from(document.querySelectorAll('[data-automation-id="selectedItem"]'))
                            .map(c => c.textContent),
                   applied: res.applied.length,
                   leftover: document.getElementById('sk').value,
                   ms: Date.now() - started,
                 };
               }"""
        )
        # The second and third values are the ones that regress: the first is
        # typed into an empty menu, so even the old code waited for it.
        check("every skill committed", out["chips"], ["Java", "Python", "SQL"])
        check("picker reported success", out["applied"], 1)
        check("search box left clean", out["leftover"], "")
        check("no page errors", errors, [])

        # A value the taxonomy does not hold must not hang on the deadline, and
        # must not click the "No matching results" row.
        miss = await page.evaluate(
            """async () => {
                 document.querySelector('.chips').innerHTML = '';
                 const started = Date.now();
                 const { fields, elements } = window.__TA.scrape();
                 const skill = fields.find(f => (f.label || '').toLowerCase().includes('skill'));
                 const res = await window.__TA.applyFills(
                   [{ idx: skill.idx, action: 'multiselect', value: 'Cobol',
                      values: ['Cobol'], label: 'Skills', confidence: 'low' }],
                   elements, null);
                 return {
                   chips: document.querySelectorAll('[data-automation-id="selectedItem"]').length,
                   failed: res.failed.length,
                   ms: Date.now() - started,
                 };
               }"""
        )
        check("unknown skill committed nothing", miss["chips"], 0)
        check("unknown skill reported failure", miss["failed"], 1)
        check("a miss does not run out the clock", miss["ms"] < 4000, True)

        await browser.close()


# Workday as it actually behaves, which no other fixture here models:
#
#   * the Skills picker is React controlled — typing into its input changes
#     nothing, the value is only accepted through React's own onKeyDown prop;
#   * its menu is a popup at body level, tied back by data-associated-widget,
#     with a second widget's popup open at the same time to prove the tie is
#     used;
#   * a date is two spinbuttons that only commit on ArrowUp;
#   * a checkbox reports through aria-checked, a tick after the click.
#
# Shapes and mechanisms from job_app_filler by Berel Levy (BSD-3-Clause).
WORKDAY_REACT_HTML = """
<!doctype html><meta charset="utf-8"><body>
  <div data-automation-id="formField-skills">
    <label id="sk-label">Skills</label>
    <div id="skillsWidget" data-automation-id="multiSelectContainer"
         role="combobox" aria-labelledby="sk-label">
      <ul data-automation-id="selectedItemList"></ul>
      <input id="sk">
    </div>
  </div>

  <div data-automation-id="formField-startDate">
    <label id="from-label">From</label>
    <div data-automation-id="dateWidget" aria-labelledby="from-label">
      <input aria-label="Month" data-automation-id="dateSectionMonth-input" data-committed="">
      <input aria-label="Year" data-automation-id="dateSectionYear-input" data-committed="">
    </div>
  </div>

  <div data-automation-id="formField-currentlyWorkHere">
    <input id="cwh" type="checkbox" aria-checked="false">
    <label for="cwh">I currently work here</label>
  </div>

  <script>
    var TAXONOMY = { python: 'Python', sql: 'SQL' };
    var AMBIGUOUS = { java: ['Java', 'JavaScript', 'Java EE'] };
    var list = document.querySelector('[data-automation-id="selectedItemList"]');

    function commit(text) {
      var li = document.createElement('li');
      li.textContent = text;
      list.appendChild(li);
      killPopups();
    }
    function killPopups() {
      var old = document.querySelectorAll('[data-automation-widget="wd-popup"]');
      for (var i = 0; i < old.length; i++) old[i].remove();
    }
    function openPopup(rows, owner) {
      var pop = document.createElement('div');
      pop.setAttribute('data-automation-widget', 'wd-popup');
      pop.setAttribute('data-associated-widget', owner);
      rows.forEach(function (text) {
        var row = document.createElement('div');
        row.setAttribute('data-automation-id', 'promptOption');
        row.textContent = text;
        row.addEventListener('click', function () { commit(text); });
        pop.appendChild(row);
      });
      document.body.appendChild(pop);
    }

    // A decoy: another control's menu, open at the same time, offering a row
    // with the same text. Picking from this one would be the page-wide scan.
    openPopup(['Java'], 'someOtherWidget');
    document.querySelector('[data-associated-widget="someOtherWidget"]')
            .firstChild.addEventListener('click', function () {
              window.__pickedWrongPopup = true;
            });

    // React's props, exactly where React puts them.
    var input = document.getElementById('sk');
    input['__reactProps$tempoapply'] = {
      onKeyDown: function (e) {
        if (!e || e.key !== 'Tab') return;
        var v = ((e.target && e.target.value) || '').trim();
        var k = v.toLowerCase();
        if (TAXONOMY[k]) { commit(TAXONOMY[k]); return; }
        if (AMBIGUOUS[k]) {
          // The search runs on the server: the rows land well after the
          // keystroke. This is the "it just searches and never selects" case.
          setTimeout(function () { openPopup(AMBIGUOUS[k], 'skillsWidget'); }, 700);
          return;
        }
        // Unknown: nothing commits and no menu opens.
      }
    };
    // Typing does nothing at all, which is the point.
    input.addEventListener('input', function () { window.__typedAt = Date.now(); });

    // Spinbuttons: only ArrowUp moves committed state.
    document.querySelectorAll('[data-automation-id^="dateSection"]').forEach(function (el) {
      el.addEventListener('keydown', function (e) {
        if (e.key !== 'ArrowUp') return;
        var next = parseInt(el.value || '0', 10) + 1;
        el.value = String(next);
        el.setAttribute('data-committed', String(next));
      });
    });

    // React-controlled checkbox: the click is owned, state lands a tick later.
    var cb = document.getElementById('cwh');
    cb.addEventListener('click', function (e) {
      e.preventDefault();
      setTimeout(function () {
        cb.setAttribute('aria-checked', 'true');
        cb.checked = true;
      }, 250);
    });
  </script>
</body>
"""


async def run_workday_react_widgets() -> None:
    """
    The real Workday widgets: React-controlled prompt, portal menu, spinbutton
    dates, a checkbox that answers late.
    """
    from playwright.async_api import async_playwright

    print("\nWorkday React widgets (prompt portal, spinbuttons, late checkbox)")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.set_content(WORKDAY_REACT_HTML)
        await page.add_script_tag(path=str(EXT / "src" / "scrape.js"))
        await page.add_script_tag(path=str(EXT / "src" / "react-bridge.js"))
        await page.add_script_tag(path=str(EXT / "src" / "fill.js"))

        out = await page.evaluate(
            """async () => {
                 const { fields, elements } = window.__TA.scrape();
                 const find = (needle) => fields.find(
                   f => (f.label || '').toLowerCase().includes(needle));
                 const skills = find('skill');
                 const month = fields.find(f => /month/i.test(f.label || ''));
                 const year = fields.find(f => /year/i.test(f.label || ''));
                 const here = find('currently work');
                 const res = await window.__TA.applyFills([
                   { idx: skills.idx, action: 'multiselect', value: 'Java',
                     values: ['Java', 'Python'], label: 'Skills' },
                   { idx: month.idx, action: 'text', value: '01', label: 'From Month' },
                   { idx: year.idx, action: 'text', value: '2025', label: 'From Year' },
                   { idx: here.idx, action: 'checkbox', value: 'Yes',
                     label: 'I currently work here' },
                 ], elements, null);
                 const cb = document.getElementById('cwh');
                 const part = (sel) => document.querySelector(sel).getAttribute('data-committed');
                 return {
                   chips: Array.from(document.querySelectorAll(
                            '[data-automation-id="selectedItemList"] > li')).map(li => li.textContent),
                   wrongPopup: !!window.__pickedWrongPopup,
                   month: part('[data-automation-id="dateSectionMonth-input"]'),
                   year: part('[data-automation-id="dateSectionYear-input"]'),
                   checked: cb.getAttribute('aria-checked'),
                   applied: res.applied.length,
                   failed: res.failed.map(f => f.label),
                 };
               }"""
        )

        # Java is ambiguous, so it has to come off this widget's own popup;
        # Python commits straight through React.
        check("both skills committed", out["chips"], ["Java", "Python"])
        check("did not pick from another widget's menu", out["wrongPopup"], False)
        check("month committed via spinbutton", out["month"], "1")
        check("year committed via spinbutton", out["year"], "2025")
        check("checkbox waited for aria-checked", out["checked"], "true")
        check("everything applied", out["applied"], 4)
        check("nothing failed", out["failed"], [])
        check("no page errors", errors, [])

        await browser.close()


async def run_legal_questions() -> None:
    """
    Work authorization, sponsorship and the other yes/no legal questions.

    Checked twice: once normally, and once with `custom_answers` emptied. The
    second pass is the point — several of these only ever resolved because a
    custom answer happened to cover them, so a profile edit would have
    silently turned them back into blanks or, worse, into an address.
    """
    from backend.applier.fields import resolve_value
    from backend.applier.profile import load_profile

    profile = load_profile()
    bare = load_profile()
    bare.custom_answers = {}

    print("\nLegal questions — must hold without custom_answers")
    cases = [
        ("Are you legally authorized to work in the listed work location?", "Yes"),
        ("Are you legally authorised to work in the country of the job location?", "Yes"),
        ("Do you have the legal right to work in the listed location?", "Yes"),
        ("Will you now or in future require a sponsorship?", "No"),
        ("Will you require company sponsorship now or at any time in the future?", "No"),
        ("Do you now or will you in the future require immigration sponsorship?", "No"),
        ("Have you ever been employed by this company?", "No"),
        ("Are you a current or former employee of the company?", "No"),
        ("Are you subject to any employment agreements with your current employer?", "No"),
        ("Do you require any accommodation during the interview process?", "No"),
        ("What are your total years of professional experience?", "2"),
    ]
    # These questions name no country: they mean the job's. An application to
    # an India-located job answers them; an unknown page leaves them blank.
    from backend.applier.fields import JOB_LOCATION

    token = JOB_LOCATION.set("Bengaluru, Karnataka")
    try:
        for label, want in cases:
            check(label[:26], resolve_value(bare, label), want)
    finally:
        JOB_LOCATION.reset(token)
    check("no job known -> blank",
          resolve_value(bare, "Will you now or in future require a sponsorship?"), "")

    # The precedence bug: a noun alias inside a yes/no question used to win.
    check("legal question is not an address",
          "Noida" in resolve_value(bare, "Do you have the legal right to work in the listed location?"),
          False)
    check("agreements question is not an employer",
          resolve_value(bare, "Are you subject to any employment agreements with your current employer?"),
          "No")

    # ...but a question-shaped label with no rule still falls back to the field,
    # and a plain field label is untouched by any of this.
    check("preferred work location", resolve_value(profile, "What is your preferred work location?"), "Noida")
    check("current company", resolve_value(profile, "Current Company"), "Paytm")
    check("email", resolve_value(profile, "Email Address"), "@")
    check("expected ctc", resolve_value(profile, "Expected CTC"), "2100000")
    check("linkedin", resolve_value(profile, "LinkedIn Profile"), "linkedin.com")


# ── Regressions: option matching, commit evidence, React state, orchestration ─
#
# Each fixture below was written to fail against the code it guards: revert
# the behaviour named in the check and it goes red. A check that passes both
# ways has not tested anything.

# A generic listbox (Workday selectWidget / any role=option menu) and a native
# <select>, both offering the sponsorship pair whose "Yes" row contains "now".
SPONSOR_LISTBOX_HTML = """
<!doctype html><meta charset="utf-8"><body>
  <div class="field">
    <label id="sp-l">Will you now or in the future require sponsorship?</label>
    <button type="button" id="sp" aria-haspopup="listbox" aria-labelledby="sp-l"
      data-options="Yes, I will now or in the future require sponsorship|No, I will not require sponsorship">Select One</button>
  </div>
  <div class="field">
    <label for="sp-sel">Sponsorship</label>
    <select id="sp-sel">
      <option value="">Select...</option>
      <option value="y">Yes, I will now or in the future require sponsorship</option>
      <option value="n">No, I will not require sponsorship</option>
    </select>
  </div>
  <script>
    var openList = null;
    function closeList() { if (openList) { openList.remove(); openList = null; } }
    document.querySelectorAll('[data-options]').forEach(function (t) {
      t.addEventListener('click', function () {
        closeList();
        var list = document.createElement('div');
        list.setAttribute('role', 'listbox');
        t.dataset.options.split('|').forEach(function (text) {
          var o = document.createElement('div');
          o.setAttribute('role', 'option');
          o.textContent = text;
          o.addEventListener('click', function () { t.textContent = text; closeList(); });
          list.appendChild(o);
        });
        document.body.appendChild(list);
        openList = list;
      });
    });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeList(); });
  </script>
</body>
"""

# react-select as it behaves on a value its menu does not hold: the menu keeps
# showing a row anyway ("Other"), and Enter commits whichever row is
# highlighted. `data-multi` makes it a tag picker whose commits are chips.
REACT_SELECT_HTML = """
<!doctype html><meta charset="utf-8"><body>
  <div class="field">
    <label id="rs-l">%s</label>
    <div class="select__control" role="combobox" id="rs" aria-labelledby="rs-l" %s>
      <span class="val">Select...</span><span class="tags"></span>
      <input class="rs-in" role="combobox">
    </div>
  </div>
  <script>
    var ctl = document.getElementById('rs');
    var openList = null;
    window.__committed = [];
    function closeList() { if (openList) { openList.remove(); openList = null; } }
    function commit(text) {
      if (ctl.hasAttribute('data-multi')) {
        var c = document.createElement('span');
        c.setAttribute('data-automation-id', 'selectedItem');
        c.textContent = text;
        ctl.querySelector('.tags').appendChild(c);
      } else {
        ctl.querySelector('.val').textContent = text;
      }
      window.__committed.push(text);
      closeList();
    }
    function open() {
      if (openList) return;
      var list = document.createElement('div');
      list.setAttribute('role', 'listbox');
      var o = document.createElement('div');
      o.setAttribute('role', 'option');
      o.textContent = 'Other';
      o.addEventListener('mousedown', function (e) { e.preventDefault(); });
      o.addEventListener('click', function () { commit('Other'); });
      list.appendChild(o);
      document.body.appendChild(list);
      openList = list;
    }
    ctl.addEventListener('click', open);
    var inp = ctl.querySelector('.rs-in');
    inp.addEventListener('input', open);
    inp.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && openList) commit(openList.querySelector('[role=option]').textContent);
    });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeList(); });
  </script>
</body>
"""


async def run_chip_count() -> None:
    """An empty chip container is not a chip — the selectedItemList bug's twin."""
    from playwright.async_api import async_playwright

    print("\nChip count — containers and empty shells are not values")
    html = """
      <div data-automation-id="formField-skills">
        <input id="empty" data-automation-id="searchBox">
        <div class="chips-container"></div>
      </div>
      <div data-automation-id="formField-langs">
        <input id="one" data-automation-id="searchBox">
        <div class="chips-container"><span class="chip">Java</span></div>
      </div>"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html)
        await page.add_script_tag(path=str(EXT / "src" / "scrape.js"))
        await page.add_script_tag(path=str(EXT / "src" / "react-bridge.js"))
        await page.add_script_tag(path=str(EXT / "src" / "fill.js"))
        counts = await page.evaluate(
            """() => ({
                 empty: window.__TA.chipCount(document.getElementById('empty')),
                 one: window.__TA.chipCount(document.getElementById('one')),
               })"""
        )
        check("empty chip container counts 0", counts["empty"], 0)
        check("one chip counts 1", counts["one"], 1)
        await browser.close()


async def run_option_matching() -> None:
    """Fixes 1 and 10: whole-word matching, and no commit without a match."""
    from playwright.async_api import async_playwright

    print("\nOption matching — whole words, polarity, no lone-row commits")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        async def load(html):
            await page.set_content(html)
            await page.add_script_tag(path=str(EXT / "src" / "scrape.js"))
            await page.add_script_tag(path=str(EXT / "src" / "react-bridge.js"))
            await page.add_script_tag(path=str(EXT / "src" / "fill.js"))

        await load(SPONSOR_LISTBOX_HTML)
        unit = await page.evaluate(
            """() => {
                 const b = window.__TA.bestOptionIndex;
                 return {
                   no: b(['Yes, I will now or in the future require sponsorship',
                          'No, I will not require sponsorship'], 'No'),
                   yes: b(['No, I will not', 'Yes, I will'], 'Yes'),
                   c: b(['C#', 'CSS', 'Cloud', 'Objective-C', 'C++'], 'C'),
                   cExact: b(['C#', 'C', 'CSS'], 'C'),
                   phone: b(['British Indian Ocean Territory (+246)', 'India (+91)'], '+91'),
                   transient: b(['No results found'], 'No'),
                   polarity: b(['Not a protected veteran'], 'Protected veteran'),
                   none: b(['None of the above'], 'No'),
                 };
               }"""
        )
        check("No picks 'No, I will not'", unit["no"], 1)
        check("Yes picks 'Yes, I will'", unit["yes"], 1)
        check("C matches none of C#/CSS/…", unit["c"], -1)
        check("C still matches C", unit["cExact"], 1)
        check("+91 matches India (+91)", unit["phone"], 1)
        check("No never picks 'No results'", unit["transient"], -1)
        check("negated option rejected", unit["polarity"], -1)
        check("No does not match None", unit["none"], -1)

        out = await page.evaluate(
            """async () => {
                 const S = window.__TA.scrape();
                 const idx = (id) => [...S.elements].find(([, e]) => e.id === id)[0];
                 const res = await window.__TA.applyFills([
                   { idx: idx('sp'), action: 'combobox', value: 'No', values: ['No'], label: 'sponsor' },
                   { idx: idx('sp-sel'), action: 'select', value: 'No', label: 'sponsor native' },
                 ], S.elements, null);
                 const sel = document.getElementById('sp-sel');
                 return {
                   combo: document.getElementById('sp').textContent.trim(),
                   native: sel.options[sel.selectedIndex].text,
                   applied: res.applied.length,
                 };
               }"""
        )
        # Reverting to substring matching clicks the "Yes, I will now…" row.
        check("listbox sponsorship = No", out["combo"].startswith("No, I will not"), True)
        check("native sponsorship = No", out["native"].startswith("No, I will not"), True)
        check("both applied", out["applied"], 2)

        # Fix 10: a single-value picker whose menu holds a different answer.
        await load(REACT_SELECT_HTML % ("How did you hear about us?", ""))
        combo = await page.evaluate(
            """async () => {
                 const S = window.__TA.scrape();
                 const idx = [...S.elements].find(([, e]) => e.id === 'rs')[0];
                 const res = await window.__TA.applyFills([
                   { idx, action: 'combobox', value: 'Company Website',
                     values: ['Company Website'], label: 'source' },
                 ], S.elements, null);
                 return { committed: window.__committed, failed: res.failed.length };
               }"""
        )
        # Reverting the settled lone-row click commits "Other".
        check("lone 'Other' row not clicked", combo["committed"], [])
        check("mismatch reported failed", combo["failed"], 1)

        # Fix 10: a tag picker — Enter would commit the highlighted "Other".
        await load(REACT_SELECT_HTML % ("Skills", "data-multi"))
        multi = await page.evaluate(
            """async () => {
                 const S = window.__TA.scrape();
                 const idx = [...S.elements].find(([, e]) => e.id === 'rs')[0];
                 const res = await window.__TA.applyFills([
                   { idx, action: 'multiselect', value: 'Kotlin', values: ['Kotlin'], label: 'Skills' },
                 ], S.elements, null);
                 return { committed: window.__committed, failed: res.failed.length };
               }"""
        )
        # Reverting the Enter guard lets react-select commit its first row.
        check("Enter did not commit 'Other'", multi["committed"], [])
        check("unheld skill reported failed", multi["failed"], 1)
        check("no page errors", errors, [])

        await browser.close()


# A React-controlled Workday prompt whose Tab keydown either opens this
# widget's own popup a beat later (mode "owned") or opens nothing, while some
# other widget's popup is already up (mode "unowned").
WORKDAY_PROMPT_EVIDENCE_HTML = """
<!doctype html><meta charset="utf-8"><body>
  <div data-automation-id="formField-skills">
    <label id="sk-label">Skills</label>
    <div id="skillsWidget" data-automation-id="multiSelectContainer"
         role="combobox" aria-labelledby="sk-label">
      <ul data-automation-id="selectedItemList"></ul>
      <input id="sk">
    </div>
  </div>
  <script>
    var list = document.querySelector('[data-automation-id="selectedItemList"]');
    window.__clicked = [];
    window.__mode = 'owned';
    window.__rows = [];
    function killPopups() {
      document.querySelectorAll('[data-automation-widget="wd-popup"]').forEach(function (p) { p.remove(); });
    }
    window.openPopup = function (rows, owner) {
      var pop = document.createElement('div');
      pop.setAttribute('data-automation-widget', 'wd-popup');
      pop.setAttribute('data-associated-widget', owner);
      rows.forEach(function (text) {
        var row = document.createElement('div');
        row.setAttribute('data-automation-id', 'promptOption');
        row.textContent = text;
        row.addEventListener('click', function () {
          window.__clicked.push(owner + ':' + text);
          if (owner === 'skillsWidget') {
            var li = document.createElement('li');
            li.textContent = text;
            list.appendChild(li);
          }
          // Either way the menu closes, which is all the old check looked at.
          killPopups();
        });
        pop.appendChild(row);
      });
      document.body.appendChild(pop);
    };
    window.killPopups = killPopups;
    document.getElementById('sk')['__reactProps$tempoapply'] = {
      onKeyDown: function (e) {
        if (!e || e.key !== 'Tab') return;
        if (window.__mode === 'owned') {
          setTimeout(function () { window.openPopup(window.__rows, 'skillsWidget'); }, 300);
        }
      }
    };
  </script>
</body>
"""


async def run_workday_prompt_evidence() -> None:
    """Fixes 1 and 2 on the React prompt path: whole tokens, and evidence tied to the widget."""
    from playwright.async_api import async_playwright

    print("\nWorkday prompt — whole-token rows, success read off this widget")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.set_content(WORKDAY_PROMPT_EVIDENCE_HTML)
        await page.add_script_tag(path=str(EXT / "src" / "scrape.js"))
        await page.add_script_tag(path=str(EXT / "src" / "react-bridge.js"))
        await page.add_script_tag(path=str(EXT / "src" / "fill.js"))

        run = """async ([mode, rows, decoy, value]) => {
                   window.killPopups();
                   window.__clicked = [];
                   document.querySelector('[data-automation-id="selectedItemList"]').innerHTML = '';
                   window.__mode = mode;
                   window.__rows = rows;
                   if (decoy) window.openPopup(decoy, 'someOtherWidget');
                   const S = window.__TA.scrape();
                   const f = S.fields.find(x => (x.label || '').toLowerCase().includes('skill'));
                   const res = await window.__TA.applyFills(
                     [{ idx: f.idx, action: 'multiselect', value, values: [value], label: 'Skills' }],
                     S.elements, null);
                   return {
                     chips: [...document.querySelectorAll('[data-automation-id="selectedItemList"] > li')]
                              .map(li => li.textContent),
                     clicked: window.__clicked,
                     applied: res.applied.length,
                     failed: res.failed.length,
                   };
                 }"""

        # Skill "C" against a taxonomy holding only C#, CSS, Cloud Computing.
        c = await page.evaluate(run, ["owned", ["C#", "CSS", "Cloud Computing"], None, "C"])
        check("skill C did not commit C#", c["chips"], [])
        check("skill C reported failed", c["failed"], 1)

        # This widget opens nothing; another widget's menu offers "Java".
        # Clicking there closes a popup, which the old check read as success.
        wrong = await page.evaluate(run, ["unowned", [], ["Java"], "Java"])
        check("other popup's row is no success", wrong["applied"], 0)
        check("reported failed, not green", wrong["failed"], 1)

        # An unowned popup's row is only ever clicked on an exact match.
        near = await page.evaluate(run, ["unowned", [], ["JavaScript"], "Java"])
        check("unowned near-miss not clicked", near["clicked"], [])

        # And the happy path still commits through its own popup.
        ok = await page.evaluate(run, ["owned", ["Java EE", "Java", "JavaScript"], ["Java"], "Java"])
        check("owned exact row committed", ok["chips"], ["Java"])
        check("owned commit applied", ok["applied"], 1)
        check("no page errors", errors, [])

        await browser.close()


# React-owned controls that revert what they did not set.
REACT_STATE_HTML = """
<!doctype html><meta charset="utf-8"><body>
  <div class="field"><input type="radio" id="r-never" name="rn" value="yes"><label for="r-never">Yes</label></div>
  <div class="field"><input type="radio" id="r-late" name="rl" value="yes"><label for="r-late">Yes</label></div>
  <div data-automation-id="formField-from">
    <input id="m-rev" aria-label="Month" data-committed="">
  </div>
  <script>
    // Never accepts: the click is cancelled, and a hand-set checked state is
    // reverted on the next tick, as React does on its next render.
    var never = document.getElementById('r-never');
    never.addEventListener('click', function (e) { e.preventDefault(); });
    never.addEventListener('change', function () { setTimeout(function () { never.checked = false; }, 0); });
    // Accepts, a tick late.
    var late = document.getElementById('r-late');
    late.addEventListener('click', function (e) {
      e.preventDefault();
      setTimeout(function () { late.checked = true; late.setAttribute('aria-checked', 'true'); }, 250);
    });
    // A date part that reverts any value it did not set itself, and ignores
    // ArrowUp: nothing written here survives.
    var m = document.getElementById('m-rev');
    m.addEventListener('input', function () { setTimeout(function () { m.value = m.dataset.committed; }, 30); });
  </script>
</body>
"""


async def run_react_state() -> None:
    """Fixes 5 and 6: a radio or date part only counts once React keeps it."""
    from playwright.async_api import async_playwright

    print("\nReact-owned state — radios and date parts must hold")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.set_content(REACT_STATE_HTML)
        await page.add_script_tag(path=str(EXT / "src" / "scrape.js"))
        await page.add_script_tag(path=str(EXT / "src" / "react-bridge.js"))
        await page.add_script_tag(path=str(EXT / "src" / "fill.js"))

        out = await page.evaluate(
            """async () => {
                 const S = window.__TA.scrape();
                 const idx = (id) => [...S.elements].find(([, e]) => e.id === id)[0];
                 const one = async (fill) => {
                   const r = await window.__TA.applyFills([fill], S.elements, null);
                   return r.applied.length === 1;
                 };
                 const never = await one({ idx: idx('r-never'), action: 'radio', value: 'Yes', label: 'never' });
                 const late = await one({ idx: idx('r-late'), action: 'radio', value: 'Yes', label: 'late' });
                 const month = await one({ idx: idx('m-rev'), action: 'text', value: '01', label: 'From Month' });
                 await new Promise(r => setTimeout(r, 100));
                 return {
                   never, late, month,
                   neverChecked: document.getElementById('r-never').checked,
                   lateChecked: document.getElementById('r-late').checked,
                   monthValue: document.getElementById('m-rev').value,
                 };
               }"""
        )
        # Reverting to `el.checked = true` reports the never-accepting radio applied.
        check("reverted radio reported failed", out["never"], False)
        check("reverted radio really unticked", out["neverChecked"], False)
        check("late radio waited for", out["late"], True)
        check("late radio ticked", out["lateChecked"], True)
        # Reverting to an immediate read-back reports the reverted month applied.
        check("reverted date part failed", out["month"], False)
        check("reverted date part is empty", out["monthValue"], "")
        check("no page errors", errors, [])

        await browser.close()


NAMELESS_RADIOS_HTML = """
<!doctype html><meta charset="utf-8"><body>
  <fieldset>
    <legend>Have you ever been employed by this company?</legend>
    <label><input type="radio" required> Yes</label>
    <label><input type="radio" required> No</label>
  </fieldset>
  <fieldset>
    <legend>Are you willing to relocate to Bengaluru?</legend>
    <label><input type="radio" required> Yes</label>
    <label><input type="radio" required> No</label>
  </fieldset>
</body>
"""


async def run_nameless_radios() -> None:
    """Fix 4: radios with no name are grouped by their question, and never answered blindly."""
    from fastapi.testclient import TestClient
    from playwright.async_api import async_playwright

    from backend.api.main import app

    client = TestClient(app, base_url=LOCAL_API)
    url = "https://acme.wd5.myworkdayjobs.com/x/apply"
    print("\nNameless radios — grouped by question, own label must be the answer")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(NAMELESS_RADIOS_HTML)
        await page.add_script_tag(path=str(EXT / "src" / "scrape.js"))
        fields = await page.evaluate("() => window.__TA.scrape().fields")
        await browser.close()

    radios = [f for f in fields if f["type"] == "radio"]
    keys = [f.get("group_key", "") for f in radios]
    check("four nameless radios scraped", len(radios), 4)
    check("same question shares a key", bool(keys[0]) and keys[0] == keys[1], True)
    check("different questions differ", keys[0] != keys[2] and keys[2] == keys[3], True)

    data = client.post("/api/autofill/resolve", json={
        "url": url, "page_title": "Application", "fields": fields,
    }).json()
    fills = [f for f in data["fills"] if f["action"] == "radio"]
    chosen = {f["label"]: next(r["option_label"] for r in radios if r["idx"] == f["idx"]) for f in fills}
    check("one fill per question", len(fills), 2)
    check("employed-here radio = No",
          chosen.get("Have you ever been employed by this company?"), "No")
    check("relocate radio = Yes", chosen.get("Are you willing to relocate to Bengaluru?"), "Yes")
    # With radios grouped one-by-one, the unanswered sibling of an answered
    # question still reported that question as needing the human.
    check("answered question not flagged",
          any("employed" in u for u in data["unresolved"]), False)

    # A radio that stands alone is only clicked when its own label is the answer.
    lone = client.post("/api/autofill/resolve", json={
        "url": url, "page_title": "Application", "fields": [{
            "idx": "9", "tag": "input", "type": "radio",
            "label": "Have you ever been employed by this company?",
            "group_label": "Have you ever been employed by this company?",
            "option_label": "Yes, I am now or was previously employed here",
        }],
    }).json()
    check("lone 'Yes…' radio not clicked for No",
          [f["idx"] for f in lone["fills"] if f["action"] == "radio"], [])

    # The gate must not depend on pick_option's own behaviour: the version this
    # was written against returned the sole option of a one-item list whatever
    # was asked. Stand that in and the radio must still not be clicked.
    from unittest import mock

    from backend.api import autofill as autofill_api
    from backend.applier.profile import load_profile

    lone_field = autofill_api.ScrapedField(
        idx="9", tag="input", type="radio",
        label="Have you ever been employed by this company?",
        group_label="Have you ever been employed by this company?",
        option_label="Yes, I am now or was previously employed here",
    )
    with mock.patch.object(autofill_api, "pick_option", lambda options, desired: options[0] if options else None):
        blind = autofill_api._resolve_radio_group(load_profile(), [lone_field], "", "")
    check("radio gate ignores a blind pick", [f.idx for f in blind], [])


# ── content.js / widget.js with a stubbed chrome.runtime ─────────────────────
#
# The real messaging only exists in the integration suite, but the logic that
# decides when a tally closes, what reaches innerHTML, and whether a panel can
# come back after a close is all in these two files, so a stub that records
# what they send is enough to pin it down.

CHROME_STUB_JS = """
(function () {
  window.__sent = [];
  window.__listeners = [];
  window.__resolveDelay = 0;
  window.__resolveReply = { ok: true, data: {
    fills: [], unresolved: [], sections_needed: {}, job_title: '', company: '',
    ats: 'unknown', known_job: false, has_resume: true } };
  function reply(msg) {
    if (msg.type === 'resolve') {
      return new Promise(function (r) { setTimeout(function () { r(window.__resolveReply); }, window.__resolveDelay); });
    }
    if (msg.type === 'ping') return { ok: true, data: { name: 'Test', ready: true, has_resume: true, missing: [] } };
    // Per-type replies a test sets: a value, or a function of the message.
    var custom = window.__replies && window.__replies[msg.type];
    if (custom) return typeof custom === 'function' ? custom(msg) : custom;
    return { ok: true };
  }
  window.chrome = { runtime: {
    id: 'test-extension', lastError: undefined,
    sendMessage: function (msg, cb) {
      window.__sent.push(msg);
      Promise.resolve(reply(msg)).then(function (r) { if (cb) cb(r); });
    },
    onMessage: { addListener: function (fn) { window.__listeners.push(fn); } },
  } };
  window.__deliver = function (msg) {
    window.__listeners.forEach(function (fn) { fn(msg, {}, function () {}); });
  };
  window.__count = function (type) { return window.__sent.filter(function (m) { return m.type === type; }).length; };
})();
"""

CONTENT_FORM_HTML = """
<!doctype html><meta charset="utf-8"><title>Apply</title><body>
  <form>
    <label for="e">Email</label><input id="e" type="email" name="email">
    <label for="a">A</label><input id="a">
    <label for="b">B</label><input id="b">
    <label for="c">C</label><input id="c">
  </form>
</body>
"""


async def _load_content(frame) -> None:
    await frame.add_script_tag(content=CHROME_STUB_JS)
    for name in ("react-bridge.js", "scrape.js", "fill.js", "widget.js", "content.js"):
        await frame.add_script_tag(path=str(EXT / "src" / name))


async def run_widget_lifecycle() -> None:
    """Fix 8: mount is idempotent, and a closed panel can be shown again."""
    from playwright.async_api import async_playwright

    print("\nWidget lifecycle — idempotent mount, close then show")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content("<!doctype html><body></body>")
        await page.add_script_tag(path=str(EXT / "src" / "widget.js"))
        out = await page.evaluate(
            """() => {
                 let clicks = 0;
                 // Fresh closures per mount, exactly as content.js passes them —
                 // addEventListener would silently dedupe one shared function.
                 const handlers = () => ({ onFill: () => { clicks += 1; }, onRefill() {}, onApplied() {},
                                           onTodoClick() {} });
                 const W = window.__TA.widget;
                 const first = W.mount(handlers()), second = W.mount(handlers());
                 W.mount(handlers());
                 const h = handlers();
                 const press = (sel) => {
                   const host = document.getElementById('tempoapply-widget-host');
                   if (host) host.shadowRoot.querySelector(sel).click();
                 };
                 press('[data-fill]');
                 const afterMounts = clicks;
                 press('[data-close]');
                 const closed = W.isMounted();
                 const again = W.mount(h);
                 const reopened = W.isMounted();
                 press('[data-fill]');
                 return { first, second, afterMounts, closed, again, reopened, total: clicks,
                          hosts: document.querySelectorAll('#tempoapply-widget-host').length };
               }"""
        )
        # Reverting: three mounts wire three click listeners, and a closed
        # panel never comes back because `host` is still set.
        check("first mount wires", out["first"], True)
        check("second mount is a no-op", out["second"], False)
        check("one click, one handler run", out["afterMounts"], 1)
        check("close unmounts", out["closed"], False)
        check("show after close rebuilds", out["reopened"], True)
        check("rebuilt panel is wired once", out["total"], 2)
        check("one panel on the page", out["hosts"], 1)
        await browser.close()


JD_TEXT = "We are looking for a backend engineer to build distributed payment systems. " * 6
WORKDAY_POSTING_HTML = f"""
<!doctype html><meta charset="utf-8"><title>Backend Engineer</title><body>
  <div data-automation-id="jobPostingDescription"><p>{JD_TEXT}</p></div>
  {CONTENT_FORM_HTML.split('<body>')[1].split('</body>')[0]}
</body>"""
GENERIC_MAIN_HTML = f"""
<!doctype html><body><main><p>{"Application form instructions and field help. " * 10}</p></main></body>"""


async def run_tailor_button() -> None:
    """Tailor resume: where the description comes from, and what the panel says."""
    from playwright.async_api import async_playwright

    print("\nTailor resume — finding the description, starting and polling a run")
    shadow = "document.getElementById('tempoapply-widget-host').shadowRoot"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # ── Finding the description ──
        await page.set_content(WORKDAY_POSTING_HTML)
        await page.add_script_tag(path=str(EXT / "src" / "scrape.js"))
        jd = await page.evaluate("() => window.__TA.jobDescription()")
        check("Workday posting container found", (jd or {}).get("source"), "page")
        check("...and it is an ATS container", (jd or {}).get("specific"), True)
        picked = await page.evaluate(
            """() => { const r = document.createRange();
                       r.selectNodeContents(document.querySelector('[data-automation-id="jobPostingDescription"] p'));
                       const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
                       return window.__TA.jobDescription(); }"""
        )
        check("a selection wins", (picked or {}).get("source"), "selection")
        await page.set_content(GENERIC_MAIN_HTML)
        await page.add_script_tag(path=str(EXT / "src" / "scrape.js"))
        generic = await page.evaluate("() => window.__TA.jobDescription()")
        check("a generic <main> is not trusted as the JD", (generic or {}).get("specific"), False)
        await page.set_content("<!doctype html><body><main>Too short.</main></body>")
        await page.add_script_tag(path=str(EXT / "src" / "scrape.js"))
        check("too little text is no description", await page.evaluate("() => window.__TA.jobDescription()"), None)

        # ── The run, on a posting page with a form below ──
        await page.set_content(WORKDAY_POSTING_HTML)
        await _load_content(page)
        await page.evaluate(
            """() => {
                 window.__TA.tailorPollMs = 50;
                 let polls = 0;
                 window.__replies = {
                   tailor: { ok: true, data: { job_id: 'job-1234abcd', state: 'running', source: 'this page' } },
                   tailorStatus: () => (++polls < 3)
                     ? { ok: true, data: { state: 'running' } }
                     : { ok: true, data: { state: 'done', ok: true, reworded: 3, refused: 1, pages: 1,
                                            pdf_url: 'http://localhost:8000/api/autopilot/resume/job-1234abcd' } },
                 };
               }"""
        )
        await page.wait_for_timeout(1300)            # watch() -> remember + mount
        check("the posting's JD is remembered for later steps", await page.evaluate("() => window.__count('rememberJd')"), 1)
        await page.evaluate(f"() => {shadow}.querySelector('[data-tailor]').click()")
        await page.wait_for_timeout(900)
        out = await page.evaluate(
            f"""() => {{
                 const sent = window.__sent.find((m) => m.type === 'tailor');
                 const m = {shadow}.querySelector('[data-msg]');
                 const a = m.querySelector('a');
                 return {{ source: sent && sent.payload.jd_source, chars: sent ? sent.payload.jd_text.length : 0,
                           text: m.textContent, href: a && a.getAttribute('href'),
                           button: {shadow}.querySelector('[data-tailor]').disabled }};
               }}"""
        )
        check("the page's JD is sent", out["source"], "page")
        check("...in full", out["chars"] >= 300, True)
        check("the panel reports the result", "3 line(s) reworded, 1 refused" in out["text"], True)
        check("...with a link to the PDF", out["href"], "http://localhost:8000/api/autopilot/resume/job-1234abcd")
        check("the button is usable again", out["button"], False)

        # ── A form step with no description: the remembered one is used ──
        await page.set_content(CONTENT_FORM_HTML)
        await _load_content(page)
        await page.evaluate(
            f"""() => {{
                 window.__TA.tailorPollMs = 50;
                 window.__replies = {{
                   recallJd: {{ ok: true, data: {{ text: {JD_TEXT!r} }} }},
                   tailor: {{ ok: false, error: '<img src=x onerror="window.__xss=1">' }},
                 }};
               }}"""
        )
        await page.wait_for_timeout(1300)
        await page.evaluate(f"() => {shadow}.querySelector('[data-tailor]').click()")
        await page.wait_for_timeout(400)
        out = await page.evaluate(
            f"""() => {{
                 const sent = window.__sent.find((m) => m.type === 'tailor');
                 const m = {shadow}.querySelector('[data-msg]');
                 return {{ source: sent && sent.payload.jd_source, imgs: m.querySelectorAll('img').length,
                           xss: !!window.__xss, text: m.textContent }};
               }}"""
        )
        check("no JD on the step -> the remembered one", out["source"], "remembered")
        check("a backend error is shown", "Could not tailor" in out["text"], True)
        check("...as text, not markup", (out["imgs"], out["xss"]), (0, False))
        check("no page errors", errors, [])
        await browser.close()


async def run_content_orchestration() -> None:
    """Fixes 7, 8 and 9 through content.js itself."""
    from playwright.async_api import async_playwright

    print("\ncontent.js — frame tally, busy frames, escaping, close/show")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # ── Top frame ──
        await page.set_content(CONTENT_FORM_HTML)
        await _load_content(page)
        await page.wait_for_timeout(1300)          # watch() -> mount after its quiet spell

        shadow = "document.getElementById('tempoapply-widget-host').shadowRoot"
        state = await page.evaluate(
            f"""() => ({{ mounted: window.__TA.widget.isMounted(), pings: window.__count('ping') }})"""
        )
        check("panel mounted on its own", state["mounted"], True)
        check("one ping for one panel", state["pings"], 1)

        # Close, then let the page mutate: the panel stays closed, no re-ping.
        await page.evaluate(f"() => {shadow}.querySelector('[data-close]').click()")
        for _ in range(2):
            await page.evaluate("() => document.body.appendChild(document.createElement('div'))")
            await page.wait_for_timeout(900)
        state = await page.evaluate(
            """() => ({ mounted: window.__TA.widget.isMounted(), pings: window.__count('ping'),
                        hosts: document.querySelectorAll('#tempoapply-widget-host').length })"""
        )
        check("closed panel stays closed", state["mounted"], False)
        check("no ping per DOM mutation", state["pings"], 1)

        await page.evaluate("() => window.__deliver({ type: 'TA_SHOW' })")
        await page.wait_for_timeout(200)
        state = await page.evaluate(
            """() => ({ mounted: window.__TA.widget.isMounted(), pings: window.__count('ping'),
                        hosts: document.querySelectorAll('#tempoapply-widget-host').length })"""
        )
        # Reverting the host reset: Show after a close does nothing.
        check("Show brings the panel back", state["mounted"], True)
        check("still exactly one panel", state["hosts"], 1)

        # Fix 9: a page-controlled label in the failed list is text, not HTML.
        await page.evaluate(
            """() => {
                 window.__resolveReply = { ok: true, data: {
                   fills: [{ idx: '999', action: 'text', value: 'x',
                             label: '<img src=x onerror=__xss=1>' }],
                   unresolved: [], sections_needed: {}, job_title: '', company: '',
                   ats: 'unknown', known_job: false, has_resume: true } };
               }"""
        )
        await page.evaluate(f"() => {shadow}.querySelector('[data-fill]').click()")
        await page.wait_for_timeout(1500)
        msg = await page.evaluate(
            f"""() => {{ const m = {shadow}.querySelector('[data-msg]');
                        return {{ text: m.textContent, imgs: m.querySelectorAll('img').length,
                                  xss: !!window.__xss }}; }}"""
        )
        check("failed label shown as text", "<img" in msg["text"], True)
        check("failed label is not markup", msg["imgs"], 0)
        check("page label ran no script", msg["xss"], False)

        # Fix 9, backend error path.
        await page.evaluate(
            """() => { window.__resolveReply = { ok: false, error: '<img src=x onerror="window.__xss2=1">' }; }"""
        )
        await page.evaluate(f"() => {shadow}.querySelector('[data-fill]').click()")
        await page.wait_for_timeout(900)
        err = await page.evaluate(
            f"""() => ({{ imgs: {shadow}.querySelector('[data-msg]').querySelectorAll('img').length,
                         xss: !!window.__xss2 }})"""
        )
        check("backend error is not markup", err["imgs"], 0)
        check("backend error ran no script", err["xss"], False)

        # Fix 7: a frame that said "starting" and never reports is named.
        await page.evaluate(
            """() => {
                 window.__resolveReply = { ok: true, data: {
                   fills: [], unresolved: [], sections_needed: {}, job_title: '', company: '',
                   ats: 'unknown', known_job: false, has_resume: true } };
                 window.__TA.frameReportCapMs = 1200;
               }"""
        )
        await page.evaluate(f"() => {shadow}.querySelector('[data-fill]').click()")
        await page.evaluate("() => window.__deliver({ type: 'TA_FRAME_STARTING' })")
        await page.wait_for_timeout(700)
        early = await page.evaluate(f"() => {shadow}.querySelector('[data-fill]').disabled")
        await page.wait_for_timeout(1600)
        late = await page.evaluate(
            f"""() => ({{ busy: {shadow}.querySelector('[data-fill]').disabled,
                         text: {shadow}.querySelector('[data-msg]').textContent }})"""
        )
        check("tally waits for the frame", early, True)
        check("silent frame named, not dropped", "did not report" in late["text"], True)
        check("tally closed at the cap", late["busy"], False)

        # Fix 7: a stale report from another run is not counted; this run's is.
        await page.evaluate("() => { window.__TA.frameReportCapMs = 20000; }")
        await page.evaluate(f"() => {shadow}.querySelector('[data-fill]').click()")
        await page.evaluate("() => window.__deliver({ type: 'TA_FRAME_STARTING' })")
        await page.wait_for_timeout(300)
        await page.evaluate(
            """() => {
                 const run = window.__sent.filter(m => m.type === 'broadcast'
                   && m.message.type === 'TA_RUN_FRAME').pop().message.opts.runId;
                 window.__deliver({ type: 'TA_REPORT', result: { runId: 'stale', scraped: 9, filled: 9,
                                    low: 0, failed: 0, unresolved: [] } });
                 window.__deliver({ type: 'TA_REPORT', result: { runId: run, scraped: 3, filled: 2,
                                    low: 0, failed: 0, unresolved: [] } });
               }"""
        )
        await page.wait_for_timeout(900)
        n_ok = await page.evaluate(f"() => {shadow}.querySelector('[data-n-ok]').textContent")
        check("only this run's report counted", n_ok, "2")

        # ── Child frame: a second run while the first is still filling ──
        child_src = CONTENT_FORM_HTML.replace('"', "&quot;")
        await page.set_content(f'<!doctype html><body><iframe id="f" srcdoc="{child_src}"></iframe></body>')
        await page.wait_for_timeout(300)
        frame = page.frames[1]
        await _load_content(frame)
        child = await frame.evaluate(
            """async () => {
                 window.__resolveDelay = 800;
                 window.__deliver({ type: 'TA_RUN_FRAME', opts: { runId: 'r1' } });
                 window.__deliver({ type: 'TA_RUN_FRAME', opts: { runId: 'r2' } });
                 await new Promise(r => setTimeout(r, 1600));
                 return {
                   resolves: window.__count('resolve'),
                   starting: window.__count('starting'),
                   reports: window.__sent.filter(m => m.type === 'report')
                              .map(m => `${m.result.runId}:${m.result.busyFrame ? 'busy' : 'done'}`),
                 };
               }"""
        )
        # Reverting the busy guard starts a second concurrent fill: 2 resolves.
        check("second run did not start a fill", child["resolves"], 1)
        check("both runs acked", child["starting"], 2)
        check("busy frame said so", child["reports"], ["r2:busy", "r1:done"])
        check("no page errors", errors, [])

        await browser.close()


# ── Integration: the real extension in Chrome ────────────────────────────────

def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


# Every other suite injects fill.js with a <script> tag, which puts it in the
# page's JS world, where Workday's `__reactProps$…` keys are visible. The real
# content script runs in an isolated world and cannot see them, so the React
# commit path passed here for weeks and never ran on a live tenant. This runs
# the same fixture through the unpacked extension and evaluates inside the
# content script's own world.
_ISOLATED_PROBE = """(async () => {
  const input = document.querySelector('[data-automation-id="multiSelectContainer"] input');
  const TA = window.__TA;
  if (!TA) return { hasTA: false };
  const { fields, elements } = TA.scrape();
  const skills = fields.find(f => (f.label || '').toLowerCase().includes('skill'));
  const res = await TA.applyFills([{ idx: skills.idx, action: 'multiselect', value: 'Java',
      values: ['Java', 'Python'], label: 'Skills' }], elements, null);
  return {
    hasTA: true,
    seesReactKeys: Object.keys(input).some(k => k.startsWith('__react')),
    chips: Array.from(document.querySelectorAll(
      '[data-automation-id="selectedItemList"] > li')).map(li => li.textContent),
    applied: res.applied.length,
  };
})()"""


async def run_real_extension_worlds() -> None:
    from playwright.async_api import async_playwright

    print("\nIntegration — real extension, Workday prompt from the isolated world")
    serve_dir = Path(tempfile.mkdtemp(prefix="ta-iso-"))
    (serve_dir / "wd.html").write_text(WORKDAY_REACT_HTML, encoding="utf-8")
    # Its own ephemeral port, so it never contends with PORT for the suite
    # after it (Windows refuses a rebind while the old one sits in TIME_WAIT).
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(serve_dir))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=tempfile.mkdtemp(prefix="ta-chrome-"),
            headless=False,  # the bundled headless shell does not load extensions
            args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}",
                  "--no-first-run", "--no-default-browser-check"],
        )
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            cdp = await ctx.new_cdp_session(page)
            contexts: list[dict] = []
            cdp.on("Runtime.executionContextCreated", lambda e: contexts.append(e["context"]))
            await cdp.send("Runtime.enable")
            await page.goto(f"http://127.0.0.1:{port}/wd.html")
            await page.wait_for_timeout(2500)
            ours = [c for c in contexts
                    if c.get("auxData", {}).get("type") == "isolated" and "TempoApply" in c.get("name", "")]
            check("content script world found", bool(ours), True)
            if not ours:
                return
            r = await cdp.send("Runtime.evaluate", {"expression": _ISOLATED_PROBE, "contextId": ours[-1]["id"],
                                                    "awaitPromise": True, "returnByValue": True})
            out = r.get("result", {}).get("value") or {}
            check("fill.js loaded", out.get("hasTA"), True)
            # The premise: if this ever reads True the probe is in the wrong world.
            check("React keys invisible here", out.get("seesReactKeys"), False)
            check("skills committed", out.get("chips"), ["Java", "Python"])
            check("skills applied", out.get("applied"), 1)
        finally:
            await ctx.close()
            httpd.shutdown()
            httpd.server_close()


async def run_integration() -> None:
    import urllib.error
    import urllib.request

    from playwright.async_api import async_playwright

    try:
        urllib.request.urlopen("http://localhost:8000/api/autofill/ping", timeout=3).read()
    except (urllib.error.URLError, OSError) as exc:
        print(f"\nSKIP integration: backend not reachable ({exc}). Start it with `python run.py`.")
        return

    httpd = _serve(EXT / "test")
    profile = Path(tempfile.mkdtemp(prefix="ta-chrome-"))

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            args=[
                f"--disable-extensions-except={EXT}",
                f"--load-extension={EXT}",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(f"http://127.0.0.1:{PORT}/harness-iframe.html")
            await page.wait_for_timeout(3000)

            print("\nIntegration — real extension, form inside an iframe")
            check("service worker running", len(ctx.service_workers), 1)
            check("widget mounted from iframe", await page.locator("#tempoapply-widget-host").count(), 1)

            panel = page.locator("#tempoapply-widget-host").locator("css=.panel")
            check("backend connected", await panel.locator("[data-dot]").get_attribute("class"), "up")

            await panel.locator("[data-fill]").click()
            await page.wait_for_timeout(6000)

            n_ok = await panel.locator("[data-n-ok]").inner_text()
            n_low = await panel.locator("[data-n-low]").inner_text()
            n_todo = await panel.locator("[data-n-todo]").inner_text()
            msg = (await panel.locator("[data-msg]").inner_text()) if await panel.locator("[data-msg]").is_visible() else ""
            print(f"    panel counters: filled={n_ok} check={n_low} you={n_todo} msg={msg!r}")
            check("fields reported filled", int(n_ok) >= 7, True)
            check("top frame filled", await page.locator("#top-email").input_value(), "@")

            frame = page.frames[1]
            check("iframe first name", await frame.locator("#f").input_value(), "Ashutosh")
            check("iframe phone", await frame.locator("#p").input_value(), "9939964663")
            check(
                "iframe radio group",
                await frame.evaluate(
                    "() => { const e = document.querySelector('input[name=\"auth\"]:checked'); return e ? e.value : ''; }"
                ),
                "yes",
            )
            check(
                "iframe resume upload",
                await frame.evaluate("() => document.querySelector('#r').files.length"),
                1,
            )
        finally:
            await ctx.close()
            httpd.shutdown()


# The harness forms stand in for real postings the scanner found, which are
# India-located. Their URLs are registered as such jobs in a throwaway
# database, so "Will you require sponsorship?" resolves exactly as it would on
# a real application — and the suite never reads or writes tempoapply.db.
HARNESS_JOBS = (
    ("https://acme.wd5.myworkdayjobs.com/en-US/careers/job/Backend-Engineer_R-1/apply", "Bengaluru, Karnataka"),
    ("https://acme.wd5.myworkdayjobs.com/x/apply", "Bengaluru, Karnataka"),
)


def install_test_db() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from backend.api.main import app
    from backend.db.models import Base, Job, get_db

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        for i, (url, location) in enumerate(HARNESS_JOBS):
            db.add(Job(id=f"harness-job-{i:04d}", title="Backend Engineer", company="Acme",
                       platform="test", url=url, location=location, status="discovered"))
        db.commit()

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override


async def main() -> None:
    install_test_db()
    await run_unit()
    await run_sections()
    await run_unknown_containers()
    await run_tenant_variations()
    await run_legal_questions()
    await run_enter_commit_and_sections()
    await run_async_prompt()
    await run_workday_react_widgets()
    await run_option_matching()
    await run_chip_count()
    await run_workday_prompt_evidence()
    await run_react_state()
    await run_nameless_radios()
    await run_widget_lifecycle()
    await run_content_orchestration()
    await run_tailor_button()
    if "--integration" in sys.argv:
        await run_real_extension_worlds()
        await run_integration()

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for line in failures:
            print(f"  - {line}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
