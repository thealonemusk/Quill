"""
Repository health check.

Catches the classes of defect that have actually bitten this project rather
than a generic lint: modules that no longer import, dependencies that are used
but undeclared, code that is written but wired to nothing, and control
characters injected by shell heredocs (one of those silently disabled a regex
in the extension for days).

    python scripts/audit.py
"""
from __future__ import annotations

import ast
import importlib
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SKIP_DIRS = ("venv", "node_modules", ".next", "__pycache__", ".git")
problems: list[str] = []


def relevant(path: pathlib.Path) -> bool:
    return not any(part in SKIP_DIRS for part in path.parts)


def section(title: str) -> None:
    print(f"\n{title}")


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


def bad(msg: str) -> None:
    problems.append(msg)
    print(f"  FAIL  {msg}")


# ── 1. Everything parses and imports ─────────────────────────────────────────

def check_python() -> None:
    section("Python")
    files = [f for f in ROOT.rglob("*.py") if relevant(f)]
    syntax = 0
    for f in files:
        try:
            ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            syntax += 1
            bad(f"syntax {f.relative_to(ROOT)}:{exc.lineno} {exc.msg}")
    if not syntax:
        ok(f"{len(files)} files parse")

    _check_undefined_names(files)

    failed = 0
    for f in sorted((ROOT / "backend").rglob("*.py")):
        if not relevant(f):
            continue
        parts = list(f.relative_to(ROOT).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        module = ".".join(parts)
        if not module:
            continue
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - reporting, not handling
            failed += 1
            bad(f"import {module}: {type(exc).__name__}: {str(exc)[:70]}")
    if not failed:
        ok("every backend module imports")


def _check_undefined_names(files: list) -> None:
    """
    Names used but never bound — a NameError waiting for the right code path.

    Importing a module proves its top level runs; it proves nothing about a
    branch inside a function. `adapters.py` called `apply_url_for_ats()`
    without importing it for two sessions: every Lever application died on the
    first line of `apply_lever`, the broad `except Exception` in the engine
    turned it into `apply_status="failed"`, and the import check here passed
    the whole time because the module itself imported fine.
    """
    import builtins

    safe = set(dir(builtins)) | {
        "__file__", "__name__", "__doc__", "__package__", "__spec__",
        "__builtins__", "__loader__",
    }
    found = []
    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        bound = set(safe)
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                bound.update((a.asname or a.name).split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                bound.update(a.asname or a.name for a in n.names)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(n.name)
            elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                bound.add(n.id)
            elif isinstance(n, ast.arg):
                bound.add(n.arg)
            elif isinstance(n, (ast.Global, ast.Nonlocal)):
                bound.update(n.names)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                bound.add(n.name)
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in bound:
                found.append(f"{f.relative_to(ROOT)}:{n.lineno} {n.id}")

    if found:
        for item in found:
            bad(f"undefined name {item}")
    else:
        ok("no undefined names")


# ── 2. Declared dependencies match reality ───────────────────────────────────

THIRD_PARTY = {
    "fastapi", "uvicorn", "sqlalchemy", "playwright", "dotenv", "multipart",
    "httpx", "aiofiles", "pydantic", "pydantic_settings", "bs4", "lxml",
    "loguru", "requests", "openai", "fitz", "pylatexenc", "PyPDF2", "docx",
    "reportlab",
}
DIST_TO_MODULE = {
    "python-dotenv": "dotenv", "python-multipart": "multipart",
    "beautifulsoup4": "bs4", "pydantic-settings": "pydantic_settings",
    "pymupdf": "fitz", "python-docx": "docx",
}


def check_requirements() -> None:
    section("Dependencies")
    used = set()
    for f in (ROOT / "backend").rglob("*.py"):
        if not relevant(f):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                used.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                used.add(node.module.split(".")[0])
    used &= THIRD_PARTY

    req_file = ROOT / "backend" / "requirements.txt"
    declared = set()
    for line in req_file.read_text(encoding="utf-8").splitlines():
        name = re.split(r"[<>=!\[]", line.strip(), 1)[0].strip().lower()
        if name and not name.startswith("#"):
            declared.add(DIST_TO_MODULE.get(name, name))

    missing = sorted(m for m in used if m.lower() not in declared)
    if missing:
        bad(f"imported but undeclared in requirements.txt: {', '.join(missing)}")
    else:
        ok(f"all {len(used)} third-party imports are declared")


# ── 3. Shell-injected control characters ─────────────────────────────────────

def check_control_chars() -> None:
    section("Control characters")
    hits = 0
    for pattern in ("*.py", "*.js", "*.ts", "*.tsx", "*.json", "*.tex", "*.md"):
        for f in ROOT.rglob(pattern):
            if not relevant(f):
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for i, ch in enumerate(text):
                code = ord(ch)
                if code < 9 or code in (11, 12) or 14 <= code < 32:
                    hits += 1
                    bad(f"{f.relative_to(ROOT)} has {hex(code)} near {text[max(0, i-30):i+10]!r}")
                    break
    if not hits:
        ok("no stray control characters")


# ── 4. Code that is written but reachable from nothing ───────────────────────

def check_wiring() -> None:
    section("Wiring")
    backend = "\n".join(
        f.read_text(encoding="utf-8")
        for f in (ROOT / "backend").rglob("*.py")
        if relevant(f)
    )
    for module, marker in [
        ("backend/scrapers/bigtech.py", "bigtech"),
        ("backend/autopilot/select.py", "autopilot.select"),
        ("backend/resume/tailor.py", "resume import tailor"),
        ("backend/applier/routing.py", "applier.routing"),
        ("backend/applier/workday_creds.py", "workday_creds"),
    ]:
        path = ROOT / module
        if not path.is_file():
            bad(f"{module} is referenced by the audit but missing")
            continue
        if marker in backend:
            ok(f"{module} is wired in")
        else:
            bad(f"{module} exists but nothing imports it")

    routes = json.dumps(sorted(_api_routes()))
    for prefix in ("/api/autofill", "/api/autopilot", "/api/jobs", "/api/scan"):
        if prefix in routes:
            ok(f"routes mounted: {prefix}")
        else:
            bad(f"no routes under {prefix}")


def _api_routes() -> list[str]:
    from backend.api.main import app

    return [r.path for r in app.routes if getattr(r, "path", "").startswith("/api/")]


# ── 5. Extension manifest sanity ─────────────────────────────────────────────

def check_extension() -> None:
    section("Extension")
    manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
    files = [manifest["background"]["service_worker"]]
    files += manifest["content_scripts"][0]["js"]
    files.append(manifest["action"]["default_popup"])
    files += list(manifest["icons"].values())
    missing = [f for f in files if not (ROOT / "extension" / f).is_file()]
    if missing:
        bad(f"manifest references missing files: {missing}")
    else:
        ok(f"all {len(files)} manifest files exist")

    excludes = manifest["content_scripts"][0].get("exclude_matches", [])
    if any("linkedin" in e for e in excludes):
        ok("linkedin.com excluded from content scripts")
    else:
        bad("content script still runs on linkedin.com (fingerprinting risk)")


# ── 6. Secrets must never be tracked ─────────────────────────────────────────

def check_ledger_semantics() -> None:
    """
    Housekeeping must never blacklist a job.

    This is a defect that actually shipped: Clear recorded everything it
    removed as "dismissed", which is a blocking status, so tidying the
    dashboard permanently hid that whole page from every future scan. 235 live
    postings were blocked this way before it was noticed, and the symptom —
    "the scan only finds twenty jobs" — looks nothing like the cause.
    """
    section("Seen ledger")
    from backend import seen_ledger

    for status in ("cleared", "expired", "filtered", "seen", "discovered"):
        if status in seen_ledger.BLOCKING_STATUSES:
            bad(f"'{status}' blocks rediscovery, but it is not a user decision")
        else:
            ok(f"'{status}' does not block rediscovery")

    for status in ("applied", "visited", "dismissed"):
        if status in seen_ledger.BLOCKING_STATUSES:
            ok(f"'{status}' still blocks rediscovery")
        else:
            bad(f"'{status}' is a decision and must block rediscovery")

    # Read the actual calls, not the text. A substring search here matched the
    # word "dismissed" in the docstring explaining why it is no longer used.
    tree = ast.parse((ROOT / "backend" / "api" / "main.py").read_text(encoding="utf-8"))
    clear_fn = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "clear_discovered_jobs"
        ),
        None,
    )
    if clear_fn is None:
        bad("clear_discovered_jobs is missing")
        return

    blocking_marks = []
    for node in ast.walk(clear_fn):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", getattr(node.func, "id", ""))
        if name not in {"mark", "mark_jobs"}:
            continue
        args = [a.value for a in node.args if isinstance(a, ast.Constant)]
        args += [k.value.value for k in node.keywords
                 if isinstance(k.value, ast.Constant) and k.arg == "status"]
        blocking_marks += [a for a in args
                           if isinstance(a, str) and a in seen_ledger.BLOCKING_STATUSES
                           and a != "applied"]

    if blocking_marks:
        bad(f"clear marks removed jobs {sorted(set(blocking_marks))} — that blacklists them")
    else:
        ok("clear does not blacklist what it removes")


LINKEDIN_REQUEST_BUDGET = 600


def check_scrape_budget() -> None:
    """
    A scan must not out-request what a host will tolerate.

    LinkedIn's guest endpoint was being asked for 1,296 search pages per scan,
    back to back with no gap, and answered with 429s — so the scan reported
    zero jobs and nothing said why. Request count is the product of roles x
    query variants x locations x pages, so it grows silently the moment anyone
    adds a city or a query variant. This is the tripwire for that.
    """
    section("Scrape budget")
    from backend.config import settings
    from backend.scrapers import http
    from backend.scrapers.linkedin import LINKEDIN_PAGES, _merge_locations, _search_queries
    from backend.scrapers.registry import resolve_discovery_roles

    interval = http.HOST_MIN_INTERVAL.get("www.linkedin.com", 0)
    if interval <= 0:
        bad("LinkedIn requests are unpaced — that is what earned the 429s")
    else:
        ok(f"LinkedIn paced at {interval}s between requests")

    if http.RATE_LIMIT_STRIKES <= 0 or http.RATE_LIMIT_COOLDOWN_SEC <= 0:
        bad("no rate-limit circuit breaker — a block will be hammered into a longer one")
    else:
        ok(f"circuit breaker after {http.RATE_LIMIT_STRIKES} consecutive 429s")

    roles = resolve_discovery_roles(settings.target_roles_list)
    locations = _merge_locations(settings.preferred_locations_list)
    queries = sum(len(_search_queries(r)) for r in roles)
    total = queries * len(locations) * LINKEDIN_PAGES
    if total > LINKEDIN_REQUEST_BUDGET:
        bad(
            f"a LinkedIn scan would issue up to {total} requests "
            f"({len(roles)} roles x {len(locations)} locations x {LINKEDIN_PAGES} pages) "
            f"— budget is {LINKEDIN_REQUEST_BUDGET}"
        )
    else:
        ok(f"LinkedIn scan budget: up to {total} requests (limit {LINKEDIN_REQUEST_BUDGET})")

    excluded = [
        loc for loc in locations
        if any(city in loc.lower() for city in ("chennai", "kochi", "cochin", "ernakulam"))
    ]
    if excluded:
        bad(f"searching cities the filters reject: {excluded}")
    else:
        ok("no searches spent on excluded cities")


SENSITIVE_PATHS = (
    "config/.env", "config/.env.example", ".env", "config/workday_accounts.json",
    "config/applicant_profile.json", "Quill.db", "data/browser_state.json",
)
# Shapes of real credentials. Printed as file + first three characters only.
SECRET_PATTERNS = (
    ("OpenAI key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("Google AQ key", re.compile(r"\bAQ\.[0-9A-Za-z_-]{30,}")),
    ("Groq key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}")),
    ("xAI key", re.compile(r"\bxai-[A-Za-z0-9]{20,}")),
    # Config files only, and on one line: in code, `password = getpass()` is
    # an assignment, not a secret, and \s* used to run on into the next key.
    ("password in a config file", re.compile(r"(?im)^[ 	]*[A-Z_]*PASSWORD[ 	]*[=:][ 	]*[\"']?[^\s#\"']{6,}")),
)
CONFIG_SUFFIXES = {"", ".env", ".template", ".txt", ".json", ".yaml", ".yml", ".ini", ".cfg", ".toml", ".md"}


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def check_secrets() -> None:
    """
    Asks git, not .gitignore. The old check passed whenever a filename appeared
    in .gitignore — and a tracked file stays tracked whatever .gitignore says,
    which is how config/applicant_profile.json (phone, address, EEO answers)
    was committed and pushed.
    """
    section("Secrets")
    if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
        ok("not a git checkout — nothing can be committed")
        return
    tracked = set(_git("ls-files").stdout.splitlines())
    for path in SENSITIVE_PATHS:
        if path in tracked:
            bad(f"{path} is TRACKED by git — `git rm --cached {path}`")
        elif _git("check-ignore", "-q", path).returncode == 0:
            ok(f"{path} is ignored and untracked")
        else:
            bad(f"{path} is not gitignored")

    leaks = []
    for rel in sorted(tracked):
        path = ROOT / rel
        if path.suffix.lower() in {".png", ".jpg", ".ico", ".pdf", ".woff", ".woff2"} or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, pattern in SECRET_PATTERNS:
            if name.startswith("password") and path.suffix.lower() not in CONFIG_SUFFIXES:
                continue
            m = pattern.search(text)
            if m:
                leaks.append(f"{rel}: {name} ({m.group(0).split('=')[-1].strip()[:3]}…)")
    if leaks:
        for leak in leaks:
            bad(f"secret-shaped string in a tracked file — {leak}")
    else:
        ok(f"no secret-shaped strings in {len(tracked)} tracked files")


def main() -> None:
    check_python()
    check_requirements()
    check_control_chars()
    check_wiring()
    check_extension()
    check_ledger_semantics()
    check_scrape_budget()
    check_secrets()

    print()
    if problems:
        print(f"{len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print("Clean.")


if __name__ == "__main__":
    main()
