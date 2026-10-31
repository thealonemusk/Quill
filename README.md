# Quill — Job Search Engine

Automated multi-platform job discovery for early-career software roles. The Jobs page switches the search between India and Europe (the UK, Ireland, Germany, the Netherlands, and Sweden). Hard filters drop senior titles, roles asking for more than two years, and locations outside the region you picked.

## Quick start

### Prerequisites
- Python 3.10+
- Node.js 18+

### Backend

```bash
pip install -r backend/requirements.txt
python -m playwright install chromium
python run.py
```

API: `http://localhost:8000`

### Dashboard

```bash
cd dashboard
npm install
npm run dev
```

UI: `http://localhost:3000`

## Platforms

**India** (the default) scans LinkedIn, Naukri, and company career sites (Greenhouse, Lever, Ashby, Workday).

**Europe** ignores that list. It searches LinkedIn, Amazon, Microsoft, and Google for the UK, Ireland, Germany, the Netherlands, and Sweden, and hides a posting that says it will not sponsor a visa. Clearing the list, or removing jobs you have already opened, leaves the other region in the database.

Indeed, InstaHyre, and Wellfound stay in the codebase and run only when a scan asks for them. The platform list is `backend/platforms.py`, exposed via `GET /api/settings`. `TARGET_REGION` in `config/env.template` is `india` or `europe`.

## Architecture

```
Quill/
├── run.py
├── backend/
│   ├── platforms.py           # Single source of truth for scan platforms
│   ├── pipeline.py            # Scan orchestrator + job upsert
│   ├── scrapers/
│   │   ├── registry.py        # Platform → scraper mapping
│   │   ├── filter_utils.py    # Experience, title, and region filters
│   │   ├── linkedin.py
│   │   ├── indeed.py          # Optional
│   │   ├── naukri.py
│   │   ├── instahyre.py       # Optional
│   │   ├── wellfound.py       # Optional
│   │   ├── company_careers.py # Greenhouse / Lever / Ashby / Workday
│   │   └── bigtech.py         # Amazon, Microsoft, Google
│   ├── applier/
│   │   ├── fields.py          # Label → value resolver (shared by both apply paths)
│   │   ├── filler.py          # Playwright form filling
│   │   └── workday.py         # Workday apply flow
│   ├── db/models.py
│   └── api/
│       ├── main.py
│       └── autofill.py        # Resolver API for the browser extension
├── dashboard/
│   ├── app/(dashboard)/       # Jobs, Analytics, Settings
│   ├── components/            # Sidebar, JobRow, UI primitives
│   └── lib/                   # api.ts, types, platforms
└── extension/                 # Chrome extension — assisted autofill
    ├── src/                   # scraper, filler, widget, service worker
    └── test/                  # Workday/Glassdoor/Greenhouse harness
```

## Browser extension

A Simplify-style autofill panel for applications you open yourself. Click
**Autofill**, review what it wrote, submit it by hand. Load it from
`chrome://extensions` → Developer mode → **Load unpacked** → `extension/`.

It carries no rules of its own: it scrapes the form, posts the labels to
`/api/autofill/resolve`, and writes back what the backend answers — the same
`backend/applier/fields.py` resolver the headless auto-apply uses. Handles
Workday's listbox widgets, forms embedded in an iframe (Glassdoor, Greenhouse
boards), react-select comboboxes, radio groups and the resume upload; leaves
subjective questions to you and flags them. See `extension/README.md`.

```bash
python extension/test/run_tests.py                # headless, no install needed
python extension/test/run_tests.py --integration  # real extension in Chrome
```

## Autopilot

Selects the best N openings, tailors a resume to each, fills the application and
stops for review. It never submits — a mis-parsed field reaching a real employer
cannot be recalled.

Jobs are routed before a browser opens (`backend/applier/routing.py`):

| Route | Meaning |
| --- | --- |
| `auto` | public ATS form (Greenhouse, Lever, Ashby) — filled by the bot |
| `login` | needs an account for that employer — uses the stored Workday login |
| `manual` | LinkedIn and anything with no reachable form — applied by hand |

UI lives at `/autopilot`, in its own route group so it cannot disturb the
existing dashboard pages.

```bash
python scripts/audit.py            # repo health: imports, deps, wiring, secrets
python scripts/verify_boards.py    # which company boards are still alive
python scripts/workday_login.py    # per-employer Workday logins
python scripts/check_ai_key.py     # is the tailoring key usable
```

## Features

- India / Europe switch on the Jobs page; the other region is kept when you clear
- Hard experience boundary (<2 years), senior title exclusion, and region location checks
- Multi-platform parallel scraping via registry
- Apple-inspired dashboard with light/dark mode
- Assisted autofill on any job portal via the browser extension
- Job-specific resume tailoring, compiled from your own LaTeX
- Autopilot: select, tailor, fill, review
- Manual job add, status tracking, analytics
