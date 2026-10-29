# Quill — working notes

Read this before changing anything. It records decisions and traps that are not
visible from the code, and the state of work in progress.

## The goal

Ashutosh runs a session; the board fetches jobs; the system selects ~30 of the
best openings (FAANG / Fortune-100 / well-known companies, right role, right
location), tailors the resume to each JD, and fills the applications. He should
not have to intervene unless genuinely necessary.

**It never submits.** Filling is automated, the final click is his. A mis-parsed
field reaching a real employer cannot be recalled.

This was a promise, not a property, until 2026-09-24: the main dashboard's
Apply sent `auto_submit: true`, the API and the committed profile defaulted to
true, and `filler.py` clicked Submit. `APPLY_NAMES` even contained "Submit
application", so opening a form could send it blank. Now there is no submit
helper at all; `finish_application` ignores `auto_submit`; and every click goes
through `click_named_button`, which refuses anything that reads as a submit or
is a native submitter of a form holding application inputs.
`backend/applier/test_never_submits.py` proves it in a real browser — keep it
green, and never add a submit path "behind a flag".

## Hard rules

1. **No fabrication on the resume.** He has said "you can lie on my resume".
   The answer is still no, and `backend/resume/guard.py` enforces it
   deterministically — a rewrite introducing a technology, employer or number
   absent from the master `.tex` is rejected and the original bullet is kept.
   It has already caught a `45 -> 450` inflation and invented `Kafka`,
   `Terraform` and `Google`. This is a gate, not a prompt instruction. Do not
   weaken it into one.

   Its first version checked every rewrite against the *whole* document and
   compared numbers by substring, so 40% -> 64% passed (the digits are in his
   phone number), 18 -> 8 passed, 45 -> 45M passed, and a Paytm bullet could say
   "at Denr Financial Services". Scope is the point: figures are checked
   against the bullet being rewritten, as value + unit; names and technologies
   against the bullet's own entry (`texdoc.entry_context`); number words
   ("five", "millions") at all; and a capitalised job-description term the
   master never mentions is refused in any casing. Only the summary may draw on
   the whole resume. Aliases are explicit (`_ALIASES`), never substrings —
   substring equivalence let `javascript` pass for `java`.
2. **Never sign in to his real employer accounts to submit an application.**
   Credentials are for filling forms he then reviews.
3. **Secrets stay out of git and out of argv.** `config/.env` and
   `config/workday_accounts.json` are gitignored. Workday passwords are
   write-only through the API and read via `getpass` in the CLI — never pass a
   password as a command argument. A password he pasted in chat should be
   rotated.

   The API is **localhost only** (`API_HOST=127.0.0.1`). On 0.0.0.0 with no
   login, anything on the Wi-Fi could set the resume path to `config/.env` and
   download it as "the resume". `local_only` in `main.py` refuses a foreign
   Host (DNS rebinding) and a foreign Origin on writes (CSRF — CORS blocks
   reading, not sending); `safe_resume_path` confines the resume to
   `resumes/`; `/api/settings` refuses line breaks. `config/.env.example` is
   **not** a template — it holds real credentials and must stay ignored;
   `config/env.template` is the tracked one. `config/applicant_profile.json`
   (PII) is untracked. `audit.py` now asks git what is tracked and scans
   tracked content for key shapes.
4. **LinkedIn is manual.** Auto-apply does not work there and the extension is
   excluded from `linkedin.com` by `exclude_matches` in the manifest. LinkedIn
   fingerprints ~2,953 named extensions; the documented permanent restrictions
   attach to extension users, not to scripts driving an ordinary browser. Route
   LinkedIn jobs to manual review.
5. **Use the Write tool for backslash-heavy content, never a bash heredoc.**
   Git Bash on Windows mangles them. This has cost real time: `\\[2pt]` became
   `\[2pt]` and broke display math, and `\b` became a literal `0x08`, silently
   disabling a regex in `fill.js` for days. `scripts/audit.py` now scans for
   stray control characters because of that second one.

## Layout beyond the README

```
backend/applier/routing.py     jobs -> AUTO | LOGIN | MANUAL, before a browser opens
backend/applier/fields.py      label -> value; shared by the extension and headless apply
backend/applier/filler.py      Playwright form filling, combobox driving
backend/applier/workday_creds.py   per-tenant logins
backend/autopilot/select.py    company tiering (FAANG/ELITE/STRONG/KNOWN/UNKNOWN/EXCLUDED)
backend/resume/texdoc.py       .tex -> frozen source + editable slots; splice render
backend/resume/guard.py        fabrication gate
backend/resume/match.py        keyword coverage, word-boundary matching
backend/resume/compile.py      Tectonic wrapper + ATS audit
extension/src/scrape.js        form scraping, repeating-section inference
extension/src/fill.js          React-safe writes, listbox driving, Workday prompts
extension/test/diagnose-workday.js  console probe: the page at rest
extension/test/trace-skills.js      console probe: drives the Skills picker
dashboard/app/(autopilot)/     separate route group — must not disturb the dashboard
scripts/audit.py               repo health; run it before declaring anything done
```

## Design decisions that look arbitrary but are not

- **The LLM never emits LaTeX.** The `.tex` is split into a frozen part and
  editable prose slots; only plain text goes out and comes back. An earlier
  version of this feature asked the model for a whole LaTeX document and
  produced files that would not compile. A no-edit round trip must stay
  byte-identical — that is the parser's correctness test.
- **`\kern0pt` is the ligature breaker**, not `{}`. XeTeX shapes straight
  through empty groups. Ligature glyphs (U+FB00–FB04) break ATS keyword
  matching, so `fi`/`ffi`/`fl` in words like *workflow* must be split.
- **`:not(.iti__country)` in the option selector is load-bearing.** Roughly 240
  hidden phone-country rows sort first and consume the scan window, making
  every dropdown look like it never opened. I removed it once by accident; do
  not remove it again.
- **`custom_answer()` is longest-needle-wins.** Dict order once answered
  *"Are you subject to any employment agreements…"* with `"Paytm"`.
- **Word boundaries everywhere.** This is the repo's recurring bug class:
  `"go"` matched *google*, `"in, "` matched *"Dublin, Dublin"* and let Ireland
  through the India filter, `"india"` matched *Indian*.
- **Repeating Workday fields** are resolved from the container id where the
  tenant uses familiar names, and otherwise by repetition order — the second
  "Company" on the page is employer 2. Without the fallback, Company / Role
  Description / URL come back blank on unfamiliar tenants.
- **Tailored PDFs live in `resumes/tailored/<sub>/`, never `resumes/`.**
  `ApplicantProfile.resume_file()` globs `resumes/*` non-recursively, so a
  stray PDF there silently hijacks the default resume for every application.
- **Honeypots are skipped, not blanked.** `is_skip_field()` returns
  `action="skip"`; the extension skips at `fill.js:257`. Intel's form has a
  `beecatcher` field that silently discards the application if filled.
- **`TARGET_REGION` is `india` or `europe`, and the other region's rows stay
  stored.** Europe means the UK, Ireland, Germany, and the Netherlands. The
  Jobs page toggle writes the setting. A save that omits `target_region`
  keeps the current value; anything else is read back as `india`
  (`settings.active_region`). The jobs list, autopilot candidates, apply-all,
  clear, purge-visited, and purge-experienced all go through `job_in_region`,
  so clearing India leaves the London and Berlin rows where they are.

  The matchers have the India filter's traps, and a few more. "northern
  ireland" contains "ireland", so the Ireland markers run only after that
  phrase is masked — Belfast is the UK only. A country name in prose ("teams
  span the UK") does not place the role; a city does. Paris is not in
  `ABROAD_MARKERS`, so without `_OTHER_COUNTRY_MARKERS` it looked like no
  country and fell through. A description that never mentions a visa is kept.
  A sentence that refuses one is dropped, unless that same sentence also
  offers sponsorship. "cannot provide sponsorship" contains the offer phrase,
  so the words in front of it are what decide.

  `APPLY_REGION` defaults to `india` and is set with `JOB_LOCATION` by the
  apply engine and `/api/autofill/resolve`. In Europe mode a London or Berlin
  sponsorship question is Yes and authorisation is No: he needs a visa in
  those four countries. A US question stays blank. In India mode London stays
  blank. "London" is not in `COUNTRY_NAMES`; `europe_countries_in` maps the
  city. A caller that sets the job location and forgets the region answers
  the UK under India rules.

  Europe search is small on purpose. LinkedIn asks six cities
  (`EUROPE_SEARCH_LOCATIONS`), one page, the settings roles only — not the
  discovery extras — and at most 40 listings. The extras times six cities
  never finished, so the queue stayed empty until every description came
  back. `EUROPE_PREFERRED_LOCATIONS` is both the scoring list and the regex
  Google uses to read a card. A shorter regex dropped Hamburg and Cork
  before the allow-list could see them. A city the allow-list accepts has to
  be on that list, or Edinburgh scores as out of scope. The Europe scan
  ignores the dashboard's India board list and runs LinkedIn, Amazon,
  Microsoft, and Google as separate tasks. One combined `bigtech` task hid
  Amazon until the Microsoft and Google browsers finished, and the scan
  looked empty. `passes_hard_filters` defaults to `india`. Naukri and company
  careers still call it that way, and they are not on the Europe scan. A new
  caller has to pass `region`.

## Workday's widgets do not work the way they look

Everything below was learned the hard way, from a real tenant that filled
nothing while the fixtures stayed green. The mechanisms come from
[job_app_filler](https://github.com/berellevy/job_app_filler) by Berel Levy
(BSD-3-Clause), which drives these widgets reliably; the attribution is in the
`fill.js` comments and must stay there. `harness-experience.html` was written
from *assumed* Workday markup and passes regardless — it is not evidence.

- **Its prompts are React controlled, so typing into them does nothing.**
  Skills, Degree, Field of Study, Source, phone country code. React never
  adopts a DOM value it did not set, so "type into the box, wait for the menu,
  click the row" was writing text nothing was listening to. The value goes in
  through React's own handler:
  `getReactProps(input).onKeyDown({key: "Tab", target: {value}})`, reading
  props off the `__reactProps$…` key on the input.
- **The content script cannot see those props.** It runs in Chrome's
  isolated world, which shares the DOM but not the page's JS properties, so
  `__reactProps$…` is simply absent there. `src/react-bridge.js` runs in the
  page world (`"world": "MAIN"` in the manifest) and makes the call when
  `fill.js` asks through a `Quill:react` event. Until 2026-09-25 fill.js
  read the props itself, so on every live tenant Skills fell back to typing,
  while every suite stayed green, because `add_script_tag` injects into the
  *page* world. `run_real_extension_worlds` (`--integration`) runs the fixture
  through the unpacked extension and evaluates in its own world; it fails with
  the bridge removed. Any new use of a page JS property goes through the bridge.
- **The menu is not inside the field.** It is a popup at `body` level,
  `[data-automation-widget="wd-popup"]`, tied back to its widget by
  `data-associated-widget`. A page-wide scan for options finds some other
  control's open menu just as readily, and clicks it.
- **Tab searches; it only sometimes selects.** An unambiguous value commits
  outright, an ambiguous one just leaves the menu open and still needs a click
  on `[data-automation-id="promptOption"]`. That is the difference between
  "it searched" and "it selected", and it is what the report *"it is just
  searching skills not selecting them"* meant.
- **Poll for the rows.** The search is a server round trip. Looking once,
  immediately, finds an empty menu and clicks nothing. Never decide a picker
  has no match from the first non-empty render either — the menu opens holding
  the *previous* value's rows.
- **Do not give up unless exactly one popup is open.** Workday keeps several
  around. `popups.length === 1 ? popups[0] : null` returned null on a live
  page essentially always. Fall back to the newest popup, but mark it unowned
  and then only click a row whose text actually matches — never "it was the
  only row".
- **A date is two spinbuttons**, `aria-label="Month"` and `aria-label="Year"`
  (plus `"Day"` where asked). The empty pair is what renders as `MM/YYYY`;
  there is no single box to write. A written value does not stick, so set it
  one short and press ArrowUp — React performs the increment itself and lands
  on state it owns.
- **The checkbox answers a tick late, through `aria-checked`.** Reading
  `el.checked` on the next line says false. Never "fix" that by forcing
  `el.checked = true`: React reverts it, so the box stays visibly unticked
  while the run reports success. Click, then wait for the state.
- **`CHIP_SELECTOR` must not match `selectedItemList`.** It is the container.
  A loose `*="selectedItem"` matched it, so the count was 1 before a skill and
  1 after, every committed value read as a failure — and then got wiped,
  because the caller clears the box when it believes nothing landed.

## Verification

All green as of 2026-09-24. Run them before claiming work is done.

```bash
python scripts/audit.py                       # imports, deps, wiring, control chars, secrets
python backend/resume/test_resume.py          # 163 — parser, guard scoping, tailor, send log, LLM retry
python backend/applier/test_fields.py         # 97 — the answers sent to employers
python backend/applier/test_never_submits.py  # 10 — real browser; no path submits
python backend/api/test_api_security.py       # 22 — localhost, origin, resume path, .env injection
python backend/scrapers/test_filters.py       # 159 — location, title, experience, tiers, dedup, ledger
python extension/test/run_tests.py            # 182
python extension/test/run_tests.py --integration   # real Chrome
```

**No test may touch `Quill.db` or `config/`.** Every suite that goes
through the API installs an in-memory database via `app.dependency_overrides`.
An early `test_api_security.py` probed the origin check with
`/purge-visited` against the real DB — harmless only because nothing was in
the purge set. Probe with a request that stops at validation (422).

A fixture the extension itself wrote is not evidence that a tenant works. Two
of the suites here exist because the green ones were lying: `run_async_prompt`
(a picker whose rows arrive from a server, with the previous value's rows still
on screen) and `run_workday_react_widgets` (React props on the input, a
body-level portal, a decoy popup belonging to another widget, spinbutton dates,
a checkbox that answers late). When a fix is for a live-tenant bug, revert
*just that behaviour* and watch the new check fail — a suite that passes both
ways has not tested anything.

`scripts/audit.py` checks the defect classes that have actually bitten this
repo, not generic lint. It caught real dependency drift: `openai`, `PyMuPDF`
and `pylatexenc` were imported but undeclared, so a fresh clone could not run
the tailorer.

Operator CLIs: `scripts/workday_login.py`, `scripts/check_ai_key.py`,
`scripts/verify_boards.py`.

Two console probes, for when a live tenant fills nothing and the fixtures are
green. Both are pasted into DevTools on the failing step and copy their own
output to the clipboard; neither submits anything.

- `extension/test/diagnose-workday.js` — read-only, the page at rest. Reports
  for every control the label and value the scraper would send, the entry it
  would be tagged with, and the verdict the resolver would reach, including
  `SKIP: already filled` — a field skipped there never opens a widget, which
  is invisible from the page. Also the Add buttons and the entry containers.
- `extension/test/trace-skills.js` — drives the Skills picker the way
  `fill.js` *used to* (typing; it predates the React commit path, and its
  `CHIP_SELECTOR` still has the loose `*="selectedItem"`), and names the step
  that fails. Pasted into the console it runs in the page world, so it can
  never reproduce an isolated-world failure. The decisive line is the
  OPTION SCAN: it counts what `OPTION_SELECTOR` matches against what *looks*
  like an option by any reading, so "the menu renders but the extension cannot
  see it" is distinguished from "no menu" and from "clicked, but no chip
  counted". It types one word into Skills and clears it again.

## Running it

```bash
python run.py                  # backend on :8000
cd dashboard && npm run dev    # dashboard on :3000
```

If `run.py` dies with `[Errno 10048] ... only one usage of each socket
address`, an older server is still holding the port. **Check its start time
before assuming it is fine** — a process from a previous day serves stale code,
and the extension will keep autofilling through the old resolver. Kill it and
restart rather than reusing it.

After changing the extension: reload it at `chrome://extensions`, then
hard-refresh any job tab already open — the old content script stays resident
in those pages.

## State as of 2026-10-29

The Jobs page switches `TARGET_REGION`. India scans are the boards they were.
Europe scans LinkedIn plus Amazon, Microsoft, and Google, and both regions
share the jobs table. `test_europe` and `test_clear_region` in
`test_filters.py`, and the Europe cases in `test_fields.py`, passed on this
date. The rest of the Verification list was not re-run.

Google and Microsoft are still browser scrapes. The September note that
Google returns nothing is about the old selectors; the card reader is now
region-aware, and nobody has confirmed either board against a live Europe
page. Company-career boards and Naukri stay on the India scan. Their filters
still default to `region="india"`.

## State as of 2026-09-24 — after the full review

A five-way review found, and this pass fixed, with a revert-verified check for
each: the submit paths above; the guard's scope; LAN/CSRF/file-read exposure;
wrong legal answers (`pick_option` picked "Protected Veteran" for "I am not a
protected veteran", "authorized to work in the United States?" answered Yes);
substring field matching ("Personal statement" -> state, "mobile app" -> the
phone number, "embrace" -> ethnicity, "military" -> ITAR); extension option
matching ("no" inside "now" answered sponsorship Yes); the India filter
admitting Seattle/Dublin when the JD mentioned India; "2026 New Grad", "VPN",
"Leadership" and "1+ years" rejected as senior; the ledger downgrading
dismissed jobs so they came back; same-title requisitions deduplicated away;
company tiers ("Goldman Sachs Services Pvt Ltd" excluded, "Square Yards" ELITE).

Work authorisation is country-aware. A question naming no country ("Will you
require sponsorship?") means the job's country: `fields.JOB_LOCATION` is set
per job by the engine and per request by `/api/autofill/resolve`. A known
India-located job answers from the profile; a job abroad answers
authorisation "No" and leaves sponsorship blank; an unknown page leaves both
blank. Blank beats false. Europe mode (2026-10-29) is the exception for the
UK, Ireland, Germany, and the Netherlands — the region decision above.

Tailoring runs on OpenRouter (`LLM_PROVIDER=openrouter`, model
`openrouter/free`, which routes to whichever free model has capacity). Its
first real run showed two gaps, both now gates: every rewrite exceeded the
prompt's "within 15%" (122-160%) and the extra length carried unsupported
claims ("collaborating asynchronously in distributed team environments") the
fact guard cannot see — `MAX_REWRITE_GROWTH` refuses those; and a reasoning
model can spend the whole token budget thinking — a truncated reply is retried
once with double the budget. Concept terms ("performance tuning") absent from
the master are still refused; whether to allow them is his call.

**Tailor from the extension** (panel button "Tailor resume"). The job
description comes from, best first: text he selected on the page; the one the
scanner saved for this job; the ATS's own description container
(`TA.jobDescription`, `JD_SELECTORS` in scrape.js); one remembered from the
posting page earlier in the same tab — Workday's form steps show none, so the
content script stores the posting page's description per tab in
`chrome.storage.session` (same host, under an hour old). A generic `main` or
`article` is never remembered: on a form step it is the form. The run takes
minutes and extension requests time out in 12s, so `POST /api/autofill/tailor`
starts it in a thread and `GET /api/autofill/tailor/{job_id}` is polled. An
unknown page becomes a job row, so the next Autofill finds the tailored PDF by
the same URL lookup. One run at a time (free-tier rate limits). Tests:
`backend/api/test_tailor_endpoint.py`, `run_tailor_button` in run_tests.py.

Still on him: rotate the LinkedIn/Workday password (the review found them
reused, and a copy sits in `config/.env.example`); move secrets out of the
OneDrive-synced folder or accept that they sync; set `EXTENSION_ID` to pin
the extension; check whether `github.com/thealonemusk/Quill` is public —
the profile was pushed before it was untracked and stays in history.

## State as of 2026-09-23

On `Working-V3` through `e997cf3`; working tree clean. All three suites green.

Working and verified against a live server: routing, the autofill resolver
(legal questions, honeypots, employer 1 vs 2, websites, country), resume
tailoring with compile/audit/trim, per-tenant Workday credentials, the isolated
`/autopilot` route group, and extension UI restyled to the dashboard's tokens.

The Workday widget work (`e997cf3`) is **verified against fixtures, not against
his tenant.** The fixtures were rebuilt to match documented Workday behaviour
rather than assumption, and each fix was isolated by reverting its own
behaviour and watching the check fail — but nobody has yet seen it fill a real
form. Do not describe it as working until he reports a run.

The path through these four bugs, in case the next one looks similar: the
backend resolved all four correctly the whole time (`sections_needed` was
`{experience: 2, …}`, entry 2 answered `Denr Financial Services`, `From`
answered `01/2025`), so every failure was browser-side. Checking that first
took ten minutes and ruled out half the codebase.

Known unfinished — none of these are started, and none should be started
without asking:

- **Employer 2 (`Denr`) still does not fill.** He ran whole-page Fill, so
  `expandSections` ran and either did not find his tenant's Add button or the
  click did not take inside its fixed 700ms wait. `addButtonFor` matches on
  accessible name (`/^add( another)?$/`, then a word-bounded "add"); no
  observation of the real button yet. The Add-buttons block of
  `diagnose-workday.js` is what settles it.
- A multiselect field is reported as applied if *any* value lands, so a
  partial skills fill still shows green in the widget tally.
- Google scraper returns 0 results (selectors are stale). Microsoft returns 3
  and is untested since the location-filter fix.
- `_committed()` cannot read back the `#country` widget, so that field is
  filled but unverified. It is a Workday prompt, so it now goes through the
  React commit path too — worth re-checking when he next runs one.
- No UI for Workday accounts on `/autopilot`; `scripts/workday_login.py` is the
  only way in.
- Cover letters (`Application.cover_letter`) are still unwritten.
- `_role_matches` cuts about 94% of India engineering roles. Whether that
  threshold is right is his call, not a bug to silently "fix".

Blocked on him: OpenAI credits, and Workday signups for the tenants he wants to
apply to (Workday requires a per-employer account before a form is reachable).

## How he wants the work done

He reads the code and will spot a broken flow by looking at it — he predicted
the dead apply path from `engine.py` before any test caught it. His words:
*"write like a principal engineer who slaps himself for every broken flow"*.
Validate the critical path rather than widening surface area, and do not report
something as working until it has actually been run.

When a live page misbehaves, get the observation before shipping the fix. Two
rounds were spent here on plausible fixes for a widget nobody had looked at —
each one real, neither one his bug — while the actual answer was in a
maintained open-source extension that already drives Workday. Read the prior
art, or get the console probe run, first. Blind patches to a form filler are
worse than no patch: it fills a real employer's form and reports success.
