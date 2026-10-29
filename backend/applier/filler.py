"""Generic Playwright form filling: discover fields, map labels, type values, upload resume."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from playwright.async_api import FrameLocator, Page, TimeoutError as PlaywrightTimeout

from backend.applier.fields import (
    is_consent_label,
    is_skip_field,
    pick_option,
    resolve_value,
)
from backend.applier.profile import ApplicantProfile


def apply_result(status: str, message: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = {"status": status, "message": message}
    if extra:
        out.update(extra)
    return out


COOKIE_NAMES = (
    "Accept all", "Accept All", "Accept cookies", "Accept Cookies",
    "I agree", "Agree", "Got it", "Allow all", "Allow All", "OK", "Okay",
)
# "Submit application" used to be in this list, which made the step that opens
# a form able to send it, blank, before anything was filled.
APPLY_NAMES = (
    "Apply for this job", "Apply Now", "Apply Manually", "Start application",
    "I'm interested", "Apply",
)
SKIP_APPLY_SUBSTRINGS = (
    "linkedin", "indeed", "google", "facebook", "continue with", "sign in with",
    "easy apply",
)
NEXT_NAMES = ("Next", "Continue", "Save and continue", "Save & Continue")

# Quill never submits. Filling is automated; the final click is the
# user's, because a mis-parsed field that reaches an employer cannot be
# recalled. There is deliberately no submit helper in this module: every
# button click goes through `click_named_button`, which refuses anything that
# reads as a submission — role names match as substrings, so "Apply" alone
# finds "Submit Application".
_SUBMIT_WORDS_RE = re.compile(
    r"\b(submit|send (my |your )?application|finish (my |your )?application|"
    r"complete (my |your )?application|review and submit)\b"
    # Automation ids run words together: Workday's pageFooterSubmitButton.
    r"|submit[-_]?button",
    re.I,
)


def reads_as_submit(text: str) -> bool:
    return bool(_SUBMIT_WORDS_RE.search(text or ""))

FIELD_JS = """() => {
  const visible = (el) => {
    if (!el) return false;
    const s = window.getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const labelFor = (el) => {
    const aria = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby');
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    if (el.labels && el.labels.length) return el.labels[0].innerText;
    if (el.id) {
      const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lab) return lab.innerText;
    }
    const wrap = el.closest('label, fieldset, .field, .form-field, .application-question, [class*="question"], [data-automation-id*="formField"], [class*="FormField"]');
    if (wrap) {
      const lab = wrap.querySelector('label, legend, [class*="label"], [data-automation-id$="label"]');
      if (lab && lab !== el) return lab.innerText;
      const text = wrap.innerText || '';
      return text.split('\\n')[0].slice(0, 180);
    }
    return el.placeholder || el.name || el.getAttribute('data-automation-id') || '';
  };
  const selectorFor = (el, idx) => {
    const auto = el.getAttribute('data-automation-id');
    if (auto) return `[data-automation-id="${auto}"]`;
    if (el.id) return `#${CSS.escape(el.id)}`;
    const name = el.getAttribute('name');
    if (name) return `${el.tagName.toLowerCase()}[name="${name}"]`;
    return `[data-ta-idx="${idx}"]`;
  };
  const nodes = Array.from(document.querySelectorAll('input, textarea, select, [role="combobox"], [contenteditable="true"]'));
  nodes.forEach((el, idx) => el.setAttribute('data-ta-idx', String(idx)));
  const out = [];
  for (const el of nodes) {
    if (el.closest('.iti__country-list') || el.classList.contains('iti__search-input')) continue;
    const type = (el.getAttribute('type') || el.tagName || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) continue;
    if (!visible(el) && type !== 'file') continue;
    const idx = el.getAttribute('data-ta-idx');
    const options = el.tagName === 'SELECT'
      ? Array.from(el.options).map(o => o.text)
      : [];
    out.push({
      idx,
      selector: selectorFor(el, idx),
      tag: el.tagName.toLowerCase(),
      type,
      name: el.getAttribute('name') || '',
      id: el.id || '',
      autocomplete: el.getAttribute('autocomplete') || '',
      placeholder: el.getAttribute('placeholder') || '',
      automation: el.getAttribute('data-automation-id') || '',
      required: el.required || el.getAttribute('aria-required') === 'true',
      label: (labelFor(el) || '').trim(),
      value: (el.value || el.innerText || '').trim(),
      options,
      role: el.getAttribute('role') || '',
    });
  }
  return out;
}"""


async def _scope_eval(scope: Page | FrameLocator, expression: str):
    if isinstance(scope, Page):
        return await scope.evaluate(expression)
    return await scope.locator("body").evaluate(expression)


async def dismiss_overlays(page: Page) -> None:
    for sel in (
        '#onetrust-accept-btn-handler',
        'button#onetrust-accept-btn-handler',
        'button:has-text("Accept All Cookies")',
        'button:has-text("Accept Cookies")',
        'button:has-text("Accept All")',
        '[data-automation-id="legalNoticeAcceptButton"]',
        '[data-automation-id="wd-LegalNotice-acceptButton"]',
        'button[data-automation-id="cookieAcceptButton"]',
        'button[aria-label="Close"]',
        '[aria-label="close"]',
        ".osano-cm-accept-all",
    ):
        loc = page.locator(sel)
        try:
            if await loc.count() and await loc.first.is_visible():
                await loc.first.click(timeout=1500)
                await page.wait_for_timeout(300)
        except Exception:
            continue
    for name in COOKIE_NAMES:
        loc = page.get_by_role("button", name=name, exact=False)
        try:
            if await loc.count() and await loc.first.is_visible():
                # "Agree" also finds "I agree — submit my application".
                if reads_as_submit(await control_text(loc.first)) or await is_submit_control(loc.first):
                    continue
                await loc.first.click(timeout=1500)
                await page.wait_for_timeout(300)
        except Exception:
            continue
    for sel in ('button[aria-label="Close"]', '[aria-label="close"]', ".osano-cm-accept-all"):
        loc = page.locator(sel)
        try:
            if await loc.count() and await loc.first.is_visible():
                await loc.first.click(timeout=1200)
        except Exception:
            continue


async def application_frame(page: Page) -> Page | FrameLocator:
    for sel in ("#grnhse_iframe", "iframe[id*='grnhse']"):
        loc = page.locator(sel)
        try:
            if not await loc.count():
                continue
            frame = page.frame_locator(sel)
            if await frame.locator("input, textarea, select").count() > 0:
                return frame
        except Exception:
            continue
    return page


def _locator(scope: Page | FrameLocator, selector: str):
    return scope.locator(selector)


async def upload_resume(scope: Page | FrameLocator, resume: Path, page: Optional[Page] = None) -> bool:
    # Greenhouse-style Attach button opens a file chooser.
    host = page or (scope if isinstance(scope, Page) else None)
    if host is not None:
        attach = scope.get_by_role("button", name="Attach", exact=False)
        try:
            if await attach.count() and await attach.first.is_visible():
                async with host.expect_file_chooser(timeout=5000) as chooser_info:
                    await attach.first.click()
                chooser = await chooser_info.value
                await chooser.set_files(str(resume))
                logger.info("Uploaded resume via Attach file chooser")
                await asyncio.sleep(0.6)
                return True
        except Exception as exc:
            logger.debug(f"Attach chooser upload failed: {exc}")

    files = _locator(scope, 'input[type="file"]')
    try:
        count = await files.count()
    except Exception:
        return False
    if not count:
        return False
    for i in range(count):
        inp = files.nth(i)
        try:
            blob = ""
            try:
                blob = " ".join(
                    (await inp.get_attribute(attr, timeout=1500) or "")
                    for attr in ("name", "id", "aria-label")
                ).lower()
            except Exception:
                blob = ""
            if "cover" in blob:
                continue
            await inp.set_input_files(str(resume), timeout=8000)
            logger.info(f"Uploaded resume to file input {i}")
            await asyncio.sleep(0.4)
            return True
        except Exception as exc:
            logger.debug(f"Resume upload failed on input {i}: {exc}")
    return False


async def _fill_text(scope: Page | FrameLocator, selector: str, value: str) -> bool:
    loc = _locator(scope, selector).first
    try:
        if not await loc.count():
            return False
        await loc.click(timeout=2500)
        await loc.fill("")
        await loc.fill(value)
        return True
    except Exception:
        try:
            await loc.press_sequentially(value, delay=15)
            return True
        except Exception as exc:
            logger.debug(f"Text fill failed {selector}: {exc}")
            return False


async def _select_option(scope: Page | FrameLocator, selector: str, options: List[str], desired: str) -> bool:
    choice = pick_option(options, desired)
    if not choice:
        return False
    loc = _locator(scope, selector).first
    try:
        await loc.select_option(label=choice)
        return True
    except Exception:
        try:
            await loc.select_option(value=choice)
            return True
        except Exception:
            return False


# Where a dropdown's options render once it is open. Covers react-select
# (Greenhouse, Lever, Ashby) and Workday's prompt list.
#
# `:not(.iti__country)` is load-bearing. The intl-tel-input phone widget keeps
# ~240 hidden country <li role="option"> nodes in the DOM at all times, and they
# sort ahead of everything else in document order — scanning a bounded window of
# matches finds nothing but invisible phone countries and concludes the dropdown
# never opened.
OPTION_SELECTOR = (
    '[data-automation-id="promptOption"], [data-automation-id="promptLeafNode"], '
    '[data-automation-id="selectWidget-option"], [role="option"]:not(.iti__country), '
    '.select__option, [class*="select__option"]'
)

# The box you type into once it is open. On Greenhouse the control *is* the
# input; Workday renders a separate search field.
SEARCH_BOX_SELECTOR = (
    '[data-automation-id="searchBox"], input[role="combobox"], '
    'input.select__input, input[id*="react-select"], input[placeholder*="Search" i]'
)


async def _open_options(scope, deadline_sec: float = 2.5) -> List[str]:
    """Poll until the dropdown renders, and return its visible option texts."""
    menu = scope.locator(OPTION_SELECTOR)
    waited = 0.0
    while waited < deadline_sec:
        try:
            count = min(await menu.count(), 40)
        except Exception:
            count = 0
        texts: List[str] = []
        for i in range(count):
            el = menu.nth(i)
            try:
                if not await el.is_visible():
                    continue
                if await el.evaluate("e => !!e.closest('.iti__country-list')"):
                    continue
                texts.append(((await el.inner_text()) or "").strip())
            except Exception:
                continue
        if texts:
            return texts
        await asyncio.sleep(0.15)
        waited += 0.15
    return []


async def _click_option(scope, choice: str) -> bool:
    """Click the option whose text is exactly `choice`."""
    menu = scope.locator(OPTION_SELECTOR)
    try:
        count = min(await menu.count(), 60)
    except Exception:
        return False
    for i in range(count):
        el = menu.nth(i)
        try:
            if not await el.is_visible():
                continue
            if ((await el.inner_text()) or "").strip() != choice:
                continue
            await el.scroll_into_view_if_needed(timeout=1500)
            await el.click(timeout=2500)
            return True
        except Exception:
            continue
    return False


async def _fill_combobox(scope: Page | FrameLocator, selector: str, value: str) -> bool:
    """
    Drive a non-native dropdown and confirm the choice actually stuck.

    The previous version clicked the first visible option whenever nothing
    matched — which answers "Yes" to a question whose answer is "No" — and
    returned success after a bare Enter without checking anything had been
    selected. That is why fields reported as filled came back empty: the value
    was typed into a react-select input but never committed.

    Now: only an option the shared matcher actually chooses is clicked, and the
    control is read back afterwards to confirm.
    """
    loc = _locator(scope, selector).first
    try:
        if not await loc.count():
            return False
        try:
            await loc.click(timeout=2500)
        except Exception:
            control = loc.locator('xpath=ancestor::*[contains(@class,"select")][1]')
            if await control.count():
                await control.first.click(timeout=2500, force=True)
            else:
                return False
        await asyncio.sleep(0.25)

        # Try the open list before typing anything.
        #
        # Most questions on these forms are yes/no or a short enumeration, where
        # the answer is already on screen. Typing into a react-select input can
        # close its own menu, so filtering is a fallback for long lists (a
        # country dropdown), not the default path.
        options = await _open_options(scope)
        choice = pick_option(options, value) if options else None
        if choice:
            if await _click_option(scope, choice):
                await asyncio.sleep(0.3)
                if await _committed(scope, selector, choice):
                    return True
                # A long list only renders the rows near the viewport, so the
                # best visible option can be the wrong one — "British Indian
                # Ocean Territory" for India, because "India +91" has not been
                # scrolled into existence yet. Fall through and filter by typing
                # rather than accept it.
                logger.debug(
                    f"Combobox {selector}: {choice!r} did not stick, filtering by text instead"
                )
                try:
                    await loc.click(timeout=2000)
                    await asyncio.sleep(0.2)
                except Exception:
                    pass

        # Type to filter.
        #
        # Which element accepts the text depends on the widget: react-select
        # (Greenhouse, Lever) makes the control itself an <input>, while Workday
        # renders a separate search field inside the open listbox.
        #
        # Hunting for a search box page-wide is wrong and was: every combobox on
        # a Greenhouse form matches the selector, so the text went into a
        # different question's input and closed this dropdown.
        typed_into = loc
        try:
            tag = (await loc.evaluate("el => el.tagName.toLowerCase()")) or ""
        except Exception:
            tag = ""
        if tag not in {"input", "textarea"}:
            search = scope.locator(SEARCH_BOX_SELECTOR)
            try:
                for i in range(min(await search.count(), 8)):
                    box = search.nth(i)
                    if await box.is_visible():
                        typed_into = box
                        break
            except Exception:
                typed_into = loc
        try:
            await typed_into.fill(value, timeout=1500)
        except Exception:
            try:
                await typed_into.press_sequentially(value, delay=20, timeout=2000)
            except Exception:
                pass
        await asyncio.sleep(0.35)

        options = await _open_options(scope)
        if not options:
            logger.debug(f"Combobox {selector}: dropdown rendered no visible options")
            await _dismiss_menu(scope)
            return False

        # One matcher for every surface — the same `pick_option` the extension
        # and the resolver use, including its yes/no and decline handling.
        choice = pick_option(options, value)
        if not choice:
            logger.debug(f"Combobox {selector}: no option matches {value!r} among {options[:6]}")
            await _dismiss_menu(scope)
            return False

        if not await _click_option(scope, choice):
            logger.debug(f"Combobox {selector}: option {choice!r} was not clickable")
            await _dismiss_menu(scope)
            return False

        await asyncio.sleep(0.3)
        ok = await _committed(scope, selector, choice)
        if not ok:
            logger.debug(f"Combobox {selector}: clicked {choice!r} but it did not stick")
        return ok
    except Exception as exc:
        logger.debug(f"Combobox fill failed {selector}: {exc}")
        return False


async def _dismiss_menu(scope) -> None:
    try:
        await scope.locator("body").press("Escape", timeout=1000)
    except Exception:
        pass


async def _committed(scope, selector: str, choice: str) -> bool:
    """
    Did the control actually take the value?

    Reporting a fill that did not happen is worse than reporting a failure: the
    form is submitted with an empty required field, or the run claims success
    on an application nobody completed.
    """
    # Compare on the meaningful core of the label. A picker's option reads
    # "India +91" but the control afterwards shows only "India" or a flag and
    # "+91", so matching the whole string reports a correct selection as failed.
    want = (choice or "").strip().lower()
    core = re.sub(r"[\(\[].*?[\)\]]", " ", want)
    core = re.sub(r"[+\d]+\s*$", " ", core).strip()
    want = " ".join(core.split()) or want
    if not want:
        return False
    try:
        # Only what the control *displays as its value*: the input's value, a
        # <select>'s chosen option, a Workday button's text, a react-select
        # single/multi value, a Workday chip. Never an ancestor's innerText —
        # that is the question label plus the open menu, so choosing "No" on
        # "Will you now or in the future require sponsorship?" read back as
        # committed because "no" is in "now", whatever was selected.
        shown = await _locator(scope, selector).first.evaluate(
            """el => {
                 const SHOWN = '[class*="single-value"], [class*="singleValue"], '
                   + '[class*="multi-value__label"], [class*="multiValue"], '
                   + '[data-automation-id="selectedItem"]';
                 const CONTROL = '[role="combobox"], select, button[aria-haspopup="listbox"]';
                 // An open menu's rows are candidates, not the value.
                 const inMenu = n => !!n.closest(
                   '[data-automation-widget="wd-popup"], [class*="__menu"], [class*="-menu"], '
                   + '[role="listbox"]:not([data-automation-id="selectedItemList"])');
                 const parts = [];
                 if (el.tagName === 'SELECT') {
                   const o = el.selectedOptions && el.selectedOptions[0];
                   if (o) parts.push(o.textContent || '');
                 } else if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
                   parts.push(el.value || '');
                 } else {
                   parts.push(el.innerText || '');      // Workday's dropdown button shows its value
                 }
                 // react-select renders the committed choice in a sibling node,
                 // never on the input. Walk up to the nearest display, and stop
                 // at an ancestor holding another control: its value is not ours.
                 let node = el.parentElement;
                 for (let i = 0; i < 4 && node; i += 1) {
                   const found = [...node.querySelectorAll(SHOWN)].filter(n => !inMenu(n));
                   if (found.length) { found.forEach(n => parts.push(n.innerText || '')); break; }
                   const others = [...node.querySelectorAll(CONTROL)]
                     .filter(c => c !== el && !c.contains(el) && !el.contains(c));
                   if (others.length) break;
                   node = node.parentElement;
                 }
                 return parts.map(s => s.replace(/\\s+/g, ' ').trim().toLowerCase()).filter(Boolean);
               }"""
        )
    except Exception:
        return True          # cannot read it back; trust the click rather than retry blindly
    # Whole words: "no" is committed by a value reading "No", not by "Now".
    pattern = rf"(?<![a-z0-9]){re.escape(want)}(?![a-z0-9])"
    return any(re.search(pattern, value or "") for value in (shown or []))


async def _check_if_needed(scope: Page | FrameLocator, selector: str, label: str, desired: str) -> bool:
    loc = _locator(scope, selector).first
    try:
        if await loc.is_checked():
            return True
        want_yes = desired.lower() in {"yes", "true", "y", "1"} or is_consent_label(label)
        if want_yes:
            await loc.check()
            return True
        return False
    except Exception:
        return False


async def _choose_radio(scope: Page | FrameLocator, name: str, desired: str, options_hint: str = "") -> bool:
    if not name:
        return False
    radios = _locator(scope, f'input[type="radio"][name="{name}"]')
    try:
        count = await radios.count()
    except Exception:
        return False
    labels = []
    for i in range(count):
        r = radios.nth(i)
        rid = await r.get_attribute("id") or ""
        value = await r.get_attribute("value") or ""
        lab = ""
        if rid:
            lab_loc = _locator(scope, f'label[for="{rid}"]')
            if await lab_loc.count():
                lab = (await lab_loc.first.inner_text()).strip()
        labels.append(lab or value)
    choice = pick_option(labels, desired)
    if not choice:
        return False
    for i, lab in enumerate(labels):
        if lab == choice:
            try:
                await radios.nth(i).check()
                return True
            except Exception:
                try:
                    await radios.nth(i).click(force=True)
                    return True
                except Exception:
                    return False
    return False


async def collect_fields(scope: Page | FrameLocator) -> List[Dict[str, Any]]:
    try:
        if isinstance(scope, Page):
            return await scope.evaluate(FIELD_JS)
        return await scope.locator("body").evaluate(FIELD_JS)
    except Exception as exc:
        logger.debug(f"Field collection failed: {exc}")
        return []


async def fill_form(
    scope: Page | FrameLocator,
    profile: ApplicantProfile,
    job_title: str,
    company: str,
) -> Dict[str, Any]:
    """Fill every visible mapped field. Returns counts and leftover required labels."""
    fields = await collect_fields(scope)
    filled = 0
    skipped = 0
    unknown_required: List[str] = []
    seen_radio = set()

    for field in fields:
        label = field.get("label") or field.get("placeholder") or field.get("name") or ""
        ftype = field.get("type") or ""
        name = field.get("name") or ""
        selector = field.get("selector")
        if not selector:
            continue
        if is_skip_field(label, name, field.get("autocomplete") or "", field.get("automation") or ""):
            skipped += 1
            continue
        if ftype == "file":
            continue
        current = (field.get("value") or "").strip()
        if current and ftype not in {"checkbox", "radio"}:
            continue

        desired = resolve_value(profile, label, job_title, company)
        if ftype in {"tel", "phone"} or "phone" in (label + name).lower():
            if not desired:
                desired = profile.phone_e164() or profile.phone_national()
            elif desired.startswith("+") and "country" in label.lower():
                desired = profile.phone_country

        if ftype == "date" and desired and not desired[:1].isdigit():
            desired = profile.available_date_iso()

        if ftype == "checkbox":
            ok = await _check_if_needed(scope, selector, label, desired or ("Yes" if is_consent_label(label) else ""))
            if ok:
                filled += 1
            continue

        if ftype == "radio":
            if name in seen_radio:
                continue
            seen_radio.add(name)
            if not desired:
                if field.get("required"):
                    unknown_required.append(label)
                continue
            ok = await _choose_radio(scope, name, desired)
            if ok:
                filled += 1
            elif field.get("required"):
                unknown_required.append(label)
            continue

        if not desired:
            if field.get("required"):
                unknown_required.append(label)
            continue

        ok = False
        if field.get("tag") == "select" or ftype == "select-one":
            ok = await _select_option(scope, selector, field.get("options") or [], desired)
        elif field.get("role") == "combobox" or "combobox" in (field.get("automation") or "").lower():
            ok = await _fill_combobox(scope, selector, desired)
        else:
            ok = await _fill_text(scope, selector, desired)

        if ok:
            filled += 1
        elif field.get("required"):
            unknown_required.append(label)

    return {
        "filled": filled,
        "skipped": skipped,
        "unknown_required": [x for x in unknown_required if x],
        "field_count": len(fields),
    }


async def control_text(target) -> str:
    """
    Everything a control could be called by. `inner_text` alone is empty for
    `<input type=submit value="Submit">`, which would sail past the check.
    """
    parts = []
    try:
        parts.append(await target.inner_text())
    except Exception:
        pass
    for attr in ("value", "aria-label", "title", "data-automation-id"):
        try:
            parts.append(await target.get_attribute(attr) or "")
        except Exception:
            pass
    return " ".join(parts).lower()


async def is_submit_control(target) -> bool:
    """
    A native submit control. "Next" on a single-page form can be one, and
    clicking it sends the application. Workday's footer buttons are exempt:
    they are `type=submit` on every step, and its final step has its own
    automation id, which `control_text` already refuses.
    """
    try:
        auto = (await target.get_attribute("data-automation-id") or "").lower()
        if auto in {"bottom-navigation-next-button", "pagefooternextbutton"}:
            return False
        # A control whose form collects an application — a file upload, an
        # email box or a free-text answer — sends that application when it is
        # a submitter, whatever its label says ("Apply", "Continue"). A
        # <button> with no type attribute inside a form is a submitter.
        return bool(await target.evaluate(
            """el => {
              const tag = el.tagName.toLowerCase();
              const type = (el.getAttribute('type') || '').toLowerCase();
              const submits = tag === 'input' ? (type === 'submit' || type === 'image')
                            : tag === 'button' ? (type === 'submit' || (type === '' && !!el.form))
                            : false;
              if (!submits || !el.form) return false;
              return !!el.form.querySelector(
                'input[type=file], input[type=email], textarea');
            }"""
        ))
    except Exception:
        # Unknown is not safe: refuse rather than risk a submission.
        return True


async def click_named_button(scope: Page | FrameLocator, names: tuple, timeout: int = 2500) -> bool:
    for name in names:
        for role in ("button", "link"):
            loc = scope.get_by_role(role, name=name, exact=False)
            try:
                count = await loc.count()
            except Exception:
                continue
            for i in range(count):
                target = loc.nth(i)
                try:
                    if not await target.is_visible():
                        continue
                    text = await control_text(target)
                    if any(s in text for s in SKIP_APPLY_SUBSTRINGS):
                        continue
                    if reads_as_submit(text) or await is_submit_control(target):
                        logger.info(f"Refusing to click {text[:60]!r}: it submits the application")
                        continue
                    await target.click(timeout=timeout)
                    return True
                except Exception:
                    continue
    return False


async def click_apply(page: Page) -> bool:
    await dismiss_overlays(page)
    if await click_named_button(page, APPLY_NAMES):
        await page.wait_for_timeout(800)
        return True
    loc = page.locator('[data-automation-id="adventureButton"], a[href*="/apply"], button[data-test="apply-button"]')
    try:
        if await loc.count() and await loc.first.is_visible():
            await loc.first.click(timeout=2500)
            await page.wait_for_timeout(800)
            return True
    except Exception:
        pass
    return False


async def click_next(scope: Page | FrameLocator) -> bool:
    wd = _locator(scope, '[data-automation-id="bottom-navigation-next-button"], [data-automation-id="pageFooterNextButton"]')
    try:
        if await wd.count() and await wd.first.is_visible():
            await wd.first.click(timeout=3000)
            return True
    except Exception:
        pass
    return await click_named_button(scope, NEXT_NAMES)


async def unfilled_required(scope: Page | FrameLocator) -> List[str]:
    js = """() => {
      const vis = (el) => {
        const s = window.getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden') return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      };
      const out = [];
      const nodes = document.querySelectorAll('input, textarea, select');
      for (const el of nodes) {
        if (!el.required && el.getAttribute('aria-required') !== 'true') continue;
        if (!vis(el)) continue;
        const type = (el.type || '').toLowerCase();
        if (['hidden', 'submit', 'button'].includes(type)) continue;
        if (el.closest('[data-automation-id="multiSelectContainer"], [data-automation-id="selectedItemList"]')) {
          const wrap = el.closest('[data-automation-id^="formField-"]');
          if (wrap && /item selected/i.test(wrap.innerText || '')) continue;
        }
        let empty = false;
        if (type === 'checkbox' || type === 'radio') {
          const group = document.querySelectorAll(`[name="${el.name}"]`);
          empty = ![...group].some(g => g.checked);
        } else if (type === 'file') {
          empty = !el.files || el.files.length === 0;
        } else {
          empty = !(el.value || '').trim();
        }
        if (empty) {
          const label = (el.labels && el.labels[0] && el.labels[0].innerText) || el.getAttribute('aria-label') || el.name || el.id;
          out.push((label || 'required field').trim());
        }
      }
      return [...new Set(out)];
    }"""
    try:
        if isinstance(scope, Page):
            return await scope.evaluate(js)
        return await scope.locator("body").evaluate(js)
    except Exception:
        return []


SUCCESS_TEXT = (
    "thank you for applying",
    "thanks for applying",
    "application submitted",
    "application has been submitted",
    "we've received your application",
    "we have received your application",
    "successfully applied",
    "application received",
    "you have applied",
    "successfully submitted",
    "your application was submitted",
    "we received your application",
)

SUCCESS_URL_PARTS = ("/confirmation", "/thanks", "/thank-you", "application-submitted", "applied=true")


async def application_succeeded(page: Page) -> bool:
    url = (page.url or "").lower()
    if any(part in url for part in SUCCESS_URL_PARTS):
        return True
    try:
        body = (await page.inner_text("body")).lower()
    except Exception:
        return False
    return any(s in body for s in SUCCESS_TEXT)


async def captcha_present(page: Page) -> bool:
    loc = page.locator("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], .g-recaptcha")
    try:
        count = await loc.count()
    except Exception:
        return False
    for i in range(count):
        try:
            el = loc.nth(i)
            if not await el.is_visible():
                continue
            box = await el.bounding_box()
            if box and box.get("height", 0) >= 40 and box.get("width", 0) >= 40:
                return True
        except Exception:
            continue
    return False


async def screenshot_failure(page: Page, dest: Path) -> None:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(dest), full_page=True)
    except Exception as exc:
        logger.debug(f"Screenshot failed: {exc}")


async def wait_settled(page: Page, ms: int = 900) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
    except PlaywrightTimeout:
        pass
    await page.wait_for_timeout(ms)


async def finish_application(
    page: Page,
    scope,
    profile: ApplicantProfile,
    auto_submit: bool,
    job_id: str,
    filled_info: Dict[str, Any],
    log_dir: Path,
) -> Dict[str, Any]:
    if await application_succeeded(page):
        return apply_result("applied", "Application submitted", filled_info)

    leftover = await unfilled_required(scope)
    leftover += filled_info.get("unknown_required") or []
    leftover = [x for x in leftover if x]
    filled = int(filled_info.get("filled") or 0)
    uploaded = bool(filled_info.get("resume_uploaded"))

    if filled == 0 and not uploaded:
        await screenshot_failure(page, log_dir / f"{job_id}.png")
        return apply_result("failed", "No application form was found or filled on this posting", filled_info)

    if leftover:
        await screenshot_failure(page, log_dir / f"{job_id}.png")
        return apply_result(
            "needs_review",
            "Required fields could not be filled: " + "; ".join(leftover[:8]),
            filled_info,
        )

    # Never submitted, whatever `auto_submit` says. The flag survives only so
    # old callers and saved profiles do not break; it has no effect.
    if auto_submit or profile.auto_submit:
        logger.warning("auto_submit was requested and ignored: Quill never submits")
    await screenshot_failure(page, log_dir / f"{job_id}-filled.png")
    if await captcha_present(page):
        return apply_result("needs_review", "Form filled; a CAPTCHA is waiting — review and submit it yourself.", filled_info)
    return apply_result("needs_review", "Form filled. Review it and submit it yourself.", filled_info)
