# Quill Autofill — browser extension

A Simplify-style autofill panel for job applications. Open any application form,
click **Autofill**, review what it wrote, submit it yourself.

The extension holds no data and no rules. It scrapes the form, sends the labels
to the Quill backend, and writes back the values it gets — so the answers
come from the same `backend/applier/fields.py` resolver that drives the headless
auto-apply. Tune an alias or a custom answer once and both paths improve.

## LinkedIn is excluded on purpose

The content script does not run on `linkedin.com`. LinkedIn probes for ~2,953
named browser extensions, job-application tools among them, and the one
documented *permanent* restriction tied to such a tool belonged to someone
applying by hand with the extension merely installed. Scripts driving a normal
browser are not on that list; extensions are the detectable surface.

Since LinkedIn jobs route to manual apply anyway, the only cost is autofill on
Easy Apply. To re-enable it, delete `exclude_matches` from `manifest.json`.

## Install

1. Start the backend: `python run.py` (the extension needs `localhost:8000`).
2. Open `chrome://extensions`, turn on **Developer mode**.
3. **Load unpacked** → select this `extension/` folder.

Works in Chrome, Edge, Brave and any Chromium browser.

## Use

| Action | How |
| --- | --- |
| Fill the current page | Click **Autofill** on the panel, or press `Alt+Shift+F` |
| Fill only one part of the form | **Fill a section**, then click the block you want. `Esc` cancels |
| Re-fill over existing values | **Fill again** |
| Jump to a question it could not answer | Click it in the amber list |
| Record the application in the dashboard | **Mark applied** |
| Move the panel | Drag its header |
| Hide it for this page | The `×` |

Field outlines after a run:

- **green** — filled with a confident match
- **amber (solid)** — filled, but check it
- **amber (dashed)** — required and unanswered; it needs you
- **red (dashed)** — a value was resolved but the widget refused it

Subjective questions ("why do you want to work here?") are deliberately left
blank. There is no LLM in this path, and a generic answer is worse than none.

## What it handles

| Portal | The hard part |
| --- | --- |
| **Workday** | No `<select>` anywhere — every dropdown is a `button[aria-haspopup="listbox"]` or a `multiSelectContainer`. The filler clicks it, types into the `[data-automation-id="searchBox"]` if one appears, then clicks the matching `promptOption`. Labels are read off the `[data-automation-id*="formField"]` wrapper. "Phone Device Type" answers `Mobile`, not your number. The `/apply` URL is a chooser, not a form — see below. |
| **Glassdoor / Indeed** | The apply flow is an iframe on `smartapply.indeed.com`. The content script runs in every frame, and a child frame that finds a form tells the top frame to show the panel — otherwise a page whose form is entirely embedded would never offer one. |
| **Greenhouse / Lever / Ashby** | Embedded boards (another iframe) and react-select comboboxes rather than native selects. Values are written through the native prototype setter so React's value tracker does not revert them. |
| **Anything else** | The generic path: inputs, textareas, selects, radio groups keyed off their `<legend>`, consent checkboxes, and the resume file input. |

### The Workday apply flow

A Workday `/apply` URL does not open a form. It opens a chooser, and behind
that sits a six-step wizard whose first step is Create Account / Sign In. Both
scrape to zero fields, so the panel detects the stage and shows what to do:

| Stage | Panel shows |
| --- | --- |
| The chooser | **Use My Last Application** (primary), Autofill with Resume, Apply Manually — clicking one opens the form and the panel follows along |
| Create Account / Sign In | "Sign in first. Quill never fills passwords." No fill is attempted |
| A wizard step | A `Step 3 of 6 · My Experience` chip and the normal Autofill button |

**Use My Last Application** is the recommended first choice: Workday carries
your previous answers forward, so there is far less left to fill or correct.

Workday replaces the whole page on every step, so press `Alt+Shift+F` again on
each one. The panel re-reads the stage on its own as the page changes.

### Filling one section at a time

**Fill a section** arms a picker: hovering outlines the smallest block holding
more than one field and shows how many it would fill, and clicking fills only
that block. It works in embedded forms too — the picker is armed in every
frame and whichever one gets the click wins.

A section run always overwrites, because the reason to point at a block is
usually that what is in it is wrong. It does not click any **Add** buttons:
you chose a region, and opening new entries underneath it would be filling
what you did not pick.

### Repeating sections (My Experience)

Work Experience, Education and Websites are not on the page when the step
loads — each sits behind an **Add** button, and Workday repeats the block once
per entry (`workExperience-1`, `workExperience-2`, `education-1`,
`websitePanelSet-3`). A flat resolver cannot answer those: "Company" means a
different employer in entry 1 than in entry 2.

So filling runs in two passes. The backend reports how many entries the profile
can fill (`sections_needed`), the extension clicks Add that many times, then
re-scrapes — and every field now carries the entry it belongs to, which
`backend/applier/sections.py` answers against `profile.experience[n]` /
`education[n]` / the LinkedIn-GitHub-portfolio list.

Split date widgets are handled too: `01/2025` becomes Month `01`, Year `2025`,
and an entry still in progress checks **I currently work here** and leaves the
To fields blank.

The **Skills** picker takes values one at a time rather than one comma-joined
string, and only plain taxonomy names are offered — a compound entry like
`TLS/Certificate Rotation` would never match a Workday skill.

Each value is typed, then committed by whichever route the widget supports:
the matching option if the tenant renders a listbox, otherwise `Enter`, the
way a person adds a tag. Success is measured by the entry actually appearing
in the field, not by the click having been dispatched. Typing into a picker
never fires `blur` — Workday closes the listbox on blur, and doing so used to
tear the menu down before any option could be clicked.

### Repeating entries when the ids are unfamiliar

Entry membership is read from the container id (`workExperience-1`,
`education-2`, `websitePanelSet-3`) where that works. Not every Workday tenant
uses those names, and an untagged field falls through to the flat resolver,
which has no answer for a bare "Company" or "Role Description" — so those boxes
stay empty.

The fallback is repetition order: the first "Company" on the page is employer 1,
the second is employer 2, and the same for "URL". That holds whatever the tenant
calls its containers.

If something still will not fill, paste `test/diagnose-workday.js` into the
DevTools console on that step. It is read-only and reports the real ids, the Add
buttons, and every control with the label the scraper derives.

### Bot traps

Every Workday Create Account step ships a **visible, rendered** input whose
label tells a human not to touch it — Intel's is
`data-automation-id="beecatcher"`, labelled *"Enter website. This input is for
robots only, do not enter if you're human."* Filling it gets the application
discarded silently, with no error shown.

Traps are matched on label text and automation id, not on CSS visibility, so
this class of field is skipped whether it is hidden off-screen, named
`website_url`, or rendered in plain sight. Password fields are never filled.

## Layout

```
extension/
├── manifest.json
├── src/
│   ├── background.js   Service worker. The only code that talks to the backend,
│   │                   because Workday's CSP blocks a content script's fetch.
│   │                   Also routes messages between frames.
│   ├── scrape.js       DOM -> field descriptors. Light DOM + open shadow roots.
│   ├── fill.js         Fills -> DOM. React-safe writes, async dropdown driving.
│   ├── widget.js       The floating panel, in a shadow root so no portal
│   │                   stylesheet can reach it.
│   └── content.js      Per-frame orchestrator and cross-frame aggregation.
├── popup/              Toolbar popup: backend status, profile, manual trigger.
└── test/               Local harness reproducing Workday, Glassdoor and
                        Greenhouse markup, including an iframe-embedded form.
```

## Backend endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /api/autofill/ping` | Handshake — is the profile complete, is a resume on file |
| `POST /api/autofill/resolve` | The brain: scraped fields in, values out |
| `GET /api/autofill/resume` | Resume bytes, cached by the service worker |
| `POST /api/autofill/track` | Record an application; creates the job row if the scanner never saw it |

`track` also writes the seen-ledger, so a job you applied to by hand will not
come back in the next scan.

## Testing without a browser store

```bash
# unit: scraper + filler against the real resolver, headless
python extension/test/run_tests.py

# integration: the real unpacked extension in Chrome, needs the backend running
python extension/test/run_tests.py --integration
```

## Troubleshooting

**Panel never appears** — the page has to look like an application (form fields,
or a known ATS host). Force it from the toolbar popup with **Show the panel**.

**Grey or red status dot** — the backend is not reachable. Start `python run.py`,
or point the extension elsewhere under **Backend URL** in the popup.

**Nothing fills on Workday** — Workday renders its form well after page load.
Wait for the fields to appear, then press `Alt+Shift+F`.

**A dropdown stayed empty** — the value had no matching option. The field is
outlined red; pick it by hand. If it is a question you will hit again, add it to
`custom_answers` in `config/applicant_profile.json`.
