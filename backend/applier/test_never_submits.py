"""
Quill never submits — proved in a real browser, not asserted in a comment.

Each page below records a submission in `window.__submitted` and cancels it,
so a test can tell "the bot pressed Submit" from "the bot pressed something
else". The pages are shaped after the three ways a submit was reachable before:

  * `click_apply` matched "Submit application" by name, and "Apply" matched it
    as a substring, so opening a form could send it blank;
  * `finish_application` clicked Submit whenever auto_submit was on — and it was
    on by default in the dashboard, the API and the committed profile;
  * cookie and Next buttons are matched by substring too ("Agree", "Next").

Run:  python backend/applier/test_never_submits.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright  # noqa: E402

from backend.applier import filler, workday  # noqa: E402
from backend.applier.profile import ApplicantProfile  # noqa: E402

failures: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name:52} {got!r}")
    if not ok:
        failures.append(f"{name}: expected {want!r}, got {got!r}")


GUARD = """<script>
window.__submitted = false;
document.addEventListener('submit', e => { window.__submitted = true; e.preventDefault(); }, true);
</script>"""

FORM_WITH_SUBMIT_APPLICATION = GUARD + """
<form><input type=email name=email><textarea name=why></textarea><input type=file name=resume>
<button>Submit Application</button></form>"""

FORM_WHOSE_SUBMIT_SAYS_APPLY = GUARD + """
<form><input type=email name=email><input type=file name=resume>
<input type=submit value="Apply"></form>"""

POSTING_PAGE = GUARD + """
<h1>Software Engineer</h1><a href="#form" onclick="window.__opened = true">Apply now</a>
<form><input type=email name=email><button>Submit application</button></form>"""

CONSENT_THAT_SUBMITS = GUARD + """
<form><input type=email name=email><textarea></textarea>
<button>I agree and submit my application</button></form>"""

WORKDAY_REVIEW_STEP = GUARD + """
<form><textarea></textarea>
<button type=submit data-automation-id="bottom-navigation-next-button">Submit</button></form>"""

FILLED_FORM = GUARD + """
<form><input type=email name=email value="a@b.co"><button>Submit</button></form>"""


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        async def load(html: str) -> None:
            await page.set_content(html)

        async def submitted() -> bool:
            return await page.evaluate("window.__submitted")

        print("\nOpening a form must never send it")
        await load(FORM_WITH_SUBMIT_APPLICATION)
        clicked = await filler.click_apply(page)
        check("'Submit Application' is not an Apply button", await submitted(), False)
        check("click_apply reports it found nothing", clicked, False)

        await load(FORM_WHOSE_SUBMIT_SAYS_APPLY)
        await filler.click_apply(page)
        check("a submit input labelled 'Apply' is refused", await submitted(), False)

        await load(POSTING_PAGE)
        clicked = await filler.click_apply(page)
        check("a real 'Apply now' link is still followed", await page.evaluate("!!window.__opened"), True)
        check("...and the form below it is not sent", await submitted(), False)

        print("\nOther substring-matched buttons")
        await load(CONSENT_THAT_SUBMITS)
        await filler.dismiss_overlays(page)
        check("'Agree' does not click 'I agree and submit'", await submitted(), False)

        await load(WORKDAY_REVIEW_STEP)
        moved = await workday._click_next(page)
        check("Workday footer labelled Submit is not 'Next'", await submitted(), False)
        check("_click_next reports it did not move", moved, False)

        print("\nFinishing a filled form, with every flag asking to submit")
        await load(FILLED_FORM)
        profile = ApplicantProfile(auto_submit=True)
        result = await filler.finish_application(
            page, page, profile, True, "never-submits-test",
            {"filled": 3, "resume_uploaded": True}, ROOT / "data" / "apply_logs",
        )
        check("auto_submit=True still does not submit", await submitted(), False)
        check("the job is left for review", result["status"], "needs_review")

        await browser.close()

    shot = ROOT / "data" / "apply_logs" / "never-submits-test-filled.png"
    shot.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for line in failures:
            print(f"  - {line}")
        sys.exit(1)
    print("All checks passed.")
