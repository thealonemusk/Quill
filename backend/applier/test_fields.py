"""
The answers the resolver sends to real employers.

Every "wrong" case below was reproduced against the real profile before it was
fixed: a substring or a single country-blind flag produced a false statement
on an application. The "still right" cases prove the fix did not buy safety
by answering nothing.

    python backend/applier/test_fields.py              # resolver + one headless browser
    python backend/applier/test_fields.py --no-browser # resolver only

Reads config/applicant_profile.json; writes nothing but a screenshot the
Lever check deletes again. The browser pages are served by `page.route`, so
nothing reaches the network.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.applier.fields import (  # noqa: E402
    custom_answer,
    is_skip_field,
    match_field_key,
    match_question_key,
    pick_option,
    resolve_value,
)
from backend.applier.profile import ApplicantProfile, load_profile  # noqa: E402

failures: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name:62} {str(got)[:40]!r}")
    if not ok:
        failures.append(f"{name}: expected {want!r}, got {got!r}")


def section(title: str) -> None:
    print(f"\n{title}")


VETERAN = ["Protected Veteran", "Not a Veteran", "I decline to answer"]


def test_pick_option() -> None:
    section("pick_option — polarity, declines, whole words")
    check("not a protected veteran -> Not a Veteran",
          pick_option(VETERAN, "I am not a protected veteran"), "Not a Veteran")
    check("decline never becomes Yes/No",
          pick_option(["Yes", "No"], "I do not want to answer"), None)
    check("decline picks the decline option",
          pick_option(VETERAN, "Decline to self-identify"), "I decline to answer")
    check("decline picks 'Prefer not to say'",
          pick_option(["Male", "Female", "Prefer not to say"], "I do not want to answer"),
          "Prefer not to say")
    check("disability decline is not 'No, I do not have...'",
          pick_option(["Yes, I have a disability", "No, I do not have a disability",
                       "I do not want to answer"], "I don't wish to answer"),
          "I do not want to answer")
    check("negated answer never matches the positive option",
          pick_option(["Protected Veteran", "Other"], "I am not a protected veteran"), None)
    check("positive answer never matches the negated option",
          pick_option(["Not a Veteran", "Other"], "Protected Veteran"), None)
    check("one option is not an answer by itself",
          pick_option(["Yes"], "No"), None)
    check("one option that does match still matches",
          pick_option(["Yes"], "Yes"), "Yes")
    check("Yes -> Yes", pick_option(["Yes", "No"], "Yes"), "Yes")
    check("No -> No", pick_option(["Yes", "No"], "No"), "No")
    check("No -> 'No, I will not require sponsorship'",
          pick_option(["Yes, I will require sponsorship", "No, I will not require sponsorship"], "No"),
          "No, I will not require sponsorship")
    check("'male' is not in 'female'", pick_option(["Female", "Male"], "Male"), "Male")
    check("India beats British Indian Ocean Territory",
          pick_option(["British Indian Ocean Territory", "India +91", "Indiana"], "India"), "India +91")
    check("No does not pick 'None of the above'",
          pick_option(["Yes", "None of the above"], "No"), None)
    check("placeholder is not an option", pick_option(["Select", "Yes"], "No"), None)


def test_field_keys() -> None:
    section("match_field_key — whole words; a question is not a contact field")
    check("'Personal statement' is not state", match_field_key("Personal statement"), None)
    check("mobile app development is not phone",
          match_field_key("Do you have experience in mobile app development?"), None)
    check("United States is not address_line2",
          match_field_key("Are you legally authorized to work in the United States?"), None)
    check("Degree of proficiency is not degree",
          match_field_key("Degree of proficiency in Python"), None)
    check("Unity is not address_line2", match_field_key("Have you used Unity?"), None)
    check("open source is not how_heard",
          match_field_key("Do you contribute to open source?"), None)
    check("First Name", match_field_key("First Name"), "first_name")
    check("Phone number", match_field_key("Phone number"), "phone")
    check("Mobile", match_field_key("Mobile"), "phone")
    check("State", match_field_key("State"), "state")
    check("State / Province *", match_field_key("State / Province *"), "state")
    check("City", match_field_key("City"), "city")
    check("Email", match_field_key("Email"), "email")
    check("Degree", match_field_key("Degree"), "degree")
    check("What is your email?", match_field_key("What is your email?"), "email")
    check("Which city are you based in?", match_field_key("Which city are you based in?"), "city")
    check("Address Line 2", match_field_key("Address Line 2"), "address_line2")
    check("How did you hear about us?", match_field_key("How did you hear about us?"), "how_heard")
    check("Phone Device Type", match_field_key("Phone Device Type"), "phone_device_type")


def test_question_keys(profile: ApplicantProfile) -> None:
    section("match_question_key / custom_answer — whole words")
    check("'embrace change' is not ethnicity",
          match_question_key("Describe a time you had to embrace change"), None)
    check("'military' is not ITAR",
          match_question_key("Have you served in the military?"), None)
    check("'Middlesex County' is not gender", match_question_key("Middlesex County"), None)
    check("Race / Ethnicity", match_question_key("Race / Ethnicity"), "ethnicity")
    check("Gender", match_question_key("Gender"), "gender")
    check("Pronouns (plural)", match_question_key("Pronouns"), "decline")
    check("ITAR", match_question_key("Are you subject to ITAR restrictions?"), "no")
    check("saved 'visa' does not answer Visakhapatnam", custom_answer(profile, "Visakhapatnam"), "")
    check("saved 'current employer' still answers",
          custom_answer(profile, "Name of your current employer"), "Paytm")
    check("longest needle still wins",
          custom_answer(profile, "Are you subject to any employment agreements with your current employer?"),
          "No")
    check("military question resolves to nothing",
          resolve_value(profile, "Have you served in the military?"), "")


def test_work_auth(profile: ApplicantProfile) -> None:
    section("Work authorisation — country-aware; blank beats false")
    check("authorized in the United States -> No",
          resolve_value(profile, "Are you legally authorized to work in the United States?"), "No")
    check("authorized in the US -> No",
          resolve_value(profile, "Are you authorized to work in the US?"), "No")
    check("right to work in the UK -> No",
          resolve_value(profile, "Do you have the right to work in the United Kingdom?"), "No")
    check("authorised in Ireland -> No",
          resolve_value(profile, "Are you authorised to work in Ireland?"), "No")
    check("authorized in India -> Yes",
          resolve_value(profile, "Are you authorized to work in India?"), "Yes")
    check("legally authorised in India -> Yes",
          resolve_value(profile, "Are you legally authorised to work in India?"), "Yes")
    check("'in the country' -> blank, not yes",
          resolve_value(profile, "Are you authorized to work in the country?"), "")
    check("'where this job is located' -> blank",
          resolve_value(profile, "Are you legally eligible to work in the country where this job is located?"), "")
    check("listed location -> blank, not an address",
          resolve_value(profile, "Do you have the legal right to work in the listed location?"), "")
    check("US sponsorship -> blank (not guessed)",
          resolve_value(profile, "Will you now or in the future require visa sponsorship to work in the US?"), "")
    check("sponsorship, no country -> blank",
          resolve_value(profile, "Will you now or in the future require sponsorship?"), "")
    check("India sponsorship -> No (per profile)",
          resolve_value(profile, "Will you require sponsorship to work in India?"), "No")
    check("India without sponsorship -> Yes",
          resolve_value(profile, "Are you authorized to work in India without sponsorship?"), "Yes")
    check("India or US -> blank", resolve_value(profile, "Are you authorized to work in India or the US?"), "")
    # "Will you tell us" is a pronoun, not the United States.
    check("pronoun 'us' is not a country",
          resolve_value(profile, "Are you authorized to work? Tell us"), "")


def test_still_answers(profile: ApplicantProfile) -> None:
    section("Normal answers survive")
    check("First Name", resolve_value(profile, "First Name"), profile.first_name)
    check("Phone number", resolve_value(profile, "Phone number"), profile.phone_e164())
    check("State", resolve_value(profile, "State"), profile.state)
    check("Veteran status", resolve_value(profile, "Veteran Status"), profile.veteran)
    check("Country of residence", resolve_value(profile, "Current country of residence"), "India")
    check("honeypot is skipped", is_skip_field("Leave this field blank", "hp", ""), True)
    check("beecatcher is skipped", is_skip_field("", "", "", "beecatcher"), True)
    check("a real field is not skipped", is_skip_field("First Name", "first_name", "given-name"), False)


# ── Browser: _committed, _typeahead, Lever checkboxes ────────────────────────

COMMITTED_YES_SHOWN = """
<div class="field"><label for="q">Will you now or in the future require sponsorship?</label>
<div class="select__control"><div class="select__single-value">Yes</div>
<input id="q" role="combobox"></div>
<div class="select__menu"><div role="option">Yes</div><div role="option">No</div></div></div>"""

COMMITTED_NO_SHOWN = COMMITTED_YES_SHOWN.replace(
    '<div class="select__single-value">Yes</div>', '<div class="select__single-value">No</div>')

NATIVE_SELECT_YES = """
<label for="s">Will you now require sponsorship? No answer means no.</label>
<select id="s"><option>No</option><option selected>Yes</option></select>"""

TYPEAHEAD = """
<script>window.__clicked = '';</script>
<input data-automation-id="addressSection_country">
<div>{rows}</div>"""


def _rows(*names: str) -> str:
    return "".join(
        f'<div data-automation-id="promptOption" onclick="window.__clicked={name!r}">{name}</div>'
        for name in names
    )


LEVER_PAGE = """<!doctype html><html><body>
<form>
<label><input type="checkbox" name="consent[marketing]">
  Keep me updated about future opportunities at Acme</label>
<label><input type="checkbox" name="consent[talent]" required>
  Add me to the Acme talent community</label>
<label><input type="checkbox" name="retention" required>
  I have read the data retention notice</label>
<label><input type="checkbox" name="optional_other">
  Something optional</label>
</form></body></html>"""


async def browser_checks() -> None:
    from playwright.async_api import async_playwright

    from backend.applier import adapters, filler, workday

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            section("_committed — the control's value, not the label or menu")
            await page.set_content(COMMITTED_YES_SHOWN)
            check("'No' is not committed when 'Yes' is shown",
                  await filler._committed(page, "#q", "No"), False)
            check("'Yes' shown reads as committed", await filler._committed(page, "#q", "Yes"), True)
            await page.set_content(COMMITTED_NO_SHOWN)
            check("'No' shown reads as committed", await filler._committed(page, "#q", "No"), True)
            await page.set_content(NATIVE_SELECT_YES)
            check("native select: 'No' is not committed", await filler._committed(page, "#s", "No"), False)
            check("native select: 'Yes' is committed", await filler._committed(page, "#s", "Yes"), True)

            section("workday._typeahead — never clicks a row that does not match")
            await page.set_content(TYPEAHEAD.format(rows=_rows("Albania", "British Indian Ocean Territory")))
            ok = await workday._typeahead(page, "addressSection_country", "India")
            check("no match -> returns False", ok, False)
            check("no match -> nothing clicked", await page.evaluate("window.__clicked"), "")
            await page.set_content(TYPEAHEAD.format(rows=_rows("British Indian Ocean Territory", "India")))
            ok = await workday._typeahead(page, "addressSection_country", "India")
            check("match -> returns True", ok, True)
            check("match -> India clicked, not BIOT", await page.evaluate("window.__clicked"), "India")

            section("Lever checkboxes — required only, never marketing")
            url = "https://jobs.lever.co/acme/00000000-test/apply"

            async def serve(route):
                if route.request.url.split("?")[0] == url:
                    await route.fulfill(status=200, content_type="text/html", body=LEVER_PAGE)
                else:
                    await route.abort()

            await page.route("**/*", serve)
            await page.goto(url)
            job_id = "lever-checkbox-test"
            await adapters.apply_lever(page, ApplicantProfile(), ROOT / "resumes" / "_none_.pdf",
                                       "Engineer", "Acme", False, job_id)
            state = await page.evaluate(
                "Object.fromEntries([...document.querySelectorAll('input[type=checkbox]')]"
                ".map(b => [b.name, b.checked]))"
            )
            check("optional marketing opt-in left unticked", state.get("consent[marketing]"), False)
            check("required talent-community box left for him", state.get("consent[talent]"), False)
            check("required non-marketing box ticked", state.get("retention"), True)
            check("optional non-consent box untouched", state.get("optional_other"), False)
            (adapters.LOG_DIR / f"{job_id}.png").unlink(missing_ok=True)
        finally:
            await browser.close()


def test_job_location(profile: ApplicantProfile) -> None:
    """A question that names no country means the job's country."""
    section("Work authorisation by the job's location")
    from backend.applier.fields import JOB_LOCATION

    no_country = "Will you now or in the future require sponsorship?"
    in_country = "Are you legally authorized to work in the country where this job is located?"

    def under(location: str, label: str) -> str:
        token = JOB_LOCATION.set(location)
        try:
            return resolve_value(profile, label)
        finally:
            JOB_LOCATION.reset(token)

    home_sponsor = "Yes" if profile.require_sponsorship else "No"
    home_auth = "Yes" if profile.authorized_to_work else "No"
    check("India job (city only): sponsorship from profile", under("Bengaluru, Karnataka", no_country), home_sponsor)
    check("India job: authorisation from profile", under("Hyderabad, Telangana, India", in_country), home_auth)
    check("Delhi NCR job resolves as India", under("Gurugram, Delhi NCR", in_country), home_auth)
    check("US job: not authorised there", under("Seattle, WA, United States", in_country), "No")
    check("US job: sponsorship left for him", under("Seattle, WA, United States", no_country), "")
    check("unknown page: sponsorship left blank", under("", no_country), "")
    check("a named country still wins over the job's", under("Bengaluru", "Are you authorized to work in the US?"), "No")

    from backend.applier.fields import APPLY_REGION

    def under_region(location: str, label: str, region: str) -> str:
        loc = JOB_LOCATION.set(location)
        reg = APPLY_REGION.set(region)
        try:
            return resolve_value(profile, label)
        finally:
            JOB_LOCATION.reset(loc)
            APPLY_REGION.reset(reg)

    uk_sponsor = "Will you require visa sponsorship to work in the United Kingdom?"
    check("Europe + London: sponsorship Yes", under_region("London", no_country, "europe"), "Yes")
    check("Europe + London: not authorised", under_region("London", in_country, "europe"), "No")
    check("Europe + Berlin: sponsorship Yes", under_region("Berlin, Germany", no_country, "europe"), "Yes")
    check("Europe + named UK sponsorship Yes", under_region("", uk_sponsor, "europe"), "Yes")
    check("Europe + UK authorisation No",
          under_region("London", "Do you have the right to work in the United Kingdom?", "europe"), "No")
    check("Europe + US sponsorship still blank",
          under_region("Seattle, WA, United States", no_country, "europe"), "")
    check("India mode + London sponsorship stays blank", under_region("London", no_country, "india"), "")


def test_consent() -> None:
    section("Consent boxes — ticked unasked, so marketing opt-ins must never qualify")
    from backend.applier.fields import is_consent_label

    for label, want in (
        ("I agree to the privacy policy", True),
        ("I certify that the information is accurate", True),
        ("I acknowledge the terms of use", True),
        ("I agree to receive marketing emails", False),
        ("Join our talent community and get job alerts", False),
        ("Keep me updated about future opportunities", False),
        ("I do not agree to the terms", False),
        ("Rate your accuracy", False),
    ):
        check(f"consent {label[:40]!r}", is_consent_label(label), want)


def main() -> int:
    profile = load_profile()
    test_consent()
    test_job_location(profile)
    test_pick_option()
    test_field_keys()
    test_question_keys(profile)
    test_work_auth(profile)
    test_still_answers(profile)
    if "--no-browser" not in sys.argv:
        asyncio.run(browser_checks())
    print()
    if failures:
        print(f"{len(failures)} FAILED")
        for f in failures:
            print("  -", f)
        return 1
    print("All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
