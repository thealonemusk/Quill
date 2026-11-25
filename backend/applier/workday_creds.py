"""
Per-tenant Workday credentials.

Workday is not one site. Every employer runs an isolated tenant —
`intel.wd1.myworkdayjobs.com`, `cisco.wd1...`, `accenture.wd103...` — with its
own account database. An account created at Intel means nothing at Cisco. The
existing `creds.workday_credentials()` returns a single global email/password,
which can only ever work for tenants where the same pair happens to have been
registered, and gives no way to tell which those are.

So credentials are stored per tenant, with the global pair kept as a default
for the common case of reusing one email and password everywhere. Crucially,
`credentials_for()` reports *where* the answer came from, so an apply run can
tell "I have an account here" from "I am guessing", and a tenant with no
account is routed to manual instead of burning a browser session on a login
that cannot succeed.

Storage is a gitignored JSON file next to the other config. That matches how
this project already keeps LinkedIn and Naukri passwords in `config/.env` —
plaintext, local, never committed. It is not secret management; if you want
that, point `Quill_WORKDAY_ACCOUNTS` at a file on an encrypted volume.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from loguru import logger

from backend.applier.creds import workday_credentials as _global_credentials

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_STORE = PROJECT_ROOT / "config" / "workday_accounts.json"

# Workday hosts look like <tenant>.<datacentre>.myworkdayjobs.com, where the
# datacentre (wd1, wd3, wd103, wd5) is infrastructure, not identity: the same
# employer can be served from different ones. The tenant is the first label.
_HOST_RE = re.compile(r"^(?P<tenant>[a-z0-9-]+)\.(?P<dc>wd\d+)\.myworkday(?:jobs)?\.com$", re.I)


def store_path() -> Path:
    override = os.environ.get("Quill_WORKDAY_ACCOUNTS", "").strip()
    return Path(override) if override else DEFAULT_STORE


def tenant_of(url: str) -> str:
    """The employer key for a Workday URL, or "" when it is not Workday."""
    host = (urlparse(url or "").netloc or "").lower().split(":")[0]
    if not host:
        return ""
    m = _HOST_RE.match(host)
    if m:
        return m.group("tenant").lower()
    # Some employers front Workday from their own domain.
    if "myworkday" in host:
        return host.split(".")[0].lower()
    return ""


@dataclass
class WorkdayAccount:
    tenant: str
    email: str
    password: str
    label: str = ""                 # e.g. "Intel"
    created_at: str = ""
    last_used: str = ""
    note: str = ""

    def redacted(self) -> Dict[str, object]:
        """Safe to send to a UI or a log — never includes the password."""
        return {
            "tenant": self.tenant,
            "email": self.email,
            "label": self.label,
            "has_password": bool(self.password),
            "created_at": self.created_at,
            "last_used": self.last_used,
            "note": self.note,
        }


def load_accounts() -> Dict[str, WorkdayAccount]:
    path = store_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"Workday account store unreadable ({exc}); treating as empty")
        return {}
    out: Dict[str, WorkdayAccount] = {}
    for tenant, row in (raw.get("accounts") or {}).items():
        if not isinstance(row, dict):
            continue
        out[tenant.lower()] = WorkdayAccount(
            tenant=tenant.lower(),
            email=str(row.get("email", "")),
            password=str(row.get("password", "")),
            label=str(row.get("label", "")),
            created_at=str(row.get("created_at", "")),
            last_used=str(row.get("last_used", "")),
            note=str(row.get("note", "")),
        )
    return out


def _write(accounts: Dict[str, WorkdayAccount]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_comment": "Per-tenant Workday logins. Gitignored. Plaintext — keep it local.",
        "accounts": {t: asdict(a) for t, a in sorted(accounts.items())},
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)       # best effort; a no-op on some Windows setups
    except OSError:
        pass


def save_account(
    tenant: str,
    email: str,
    password: str,
    label: str = "",
    note: str = "",
) -> WorkdayAccount:
    """Record the login for one employer. Overwrites an existing entry."""
    tenant = (tenant or "").strip().lower()
    if not tenant:
        raise ValueError("tenant is required")
    accounts = load_accounts()
    existing = accounts.get(tenant)
    account = WorkdayAccount(
        tenant=tenant,
        email=(email or "").strip(),
        password=password or "",
        label=label or (existing.label if existing else "") or tenant.title(),
        created_at=(existing.created_at if existing else "") or datetime.utcnow().isoformat(),
        last_used=(existing.last_used if existing else ""),
        note=note or (existing.note if existing else ""),
    )
    accounts[tenant] = account
    _write(accounts)
    return account


def delete_account(tenant: str) -> bool:
    accounts = load_accounts()
    if (tenant or "").lower() not in accounts:
        return False
    accounts.pop(tenant.lower())
    _write(accounts)
    return True


def mark_used(tenant: str) -> None:
    accounts = load_accounts()
    account = accounts.get((tenant or "").lower())
    if not account:
        return
    account.last_used = datetime.utcnow().isoformat()
    _write(accounts)


def credentials_for(url_or_tenant: str) -> Tuple[str, str, str]:
    """
    Return (email, password, source) for a Workday URL or tenant name.

    `source` is "tenant" for a recorded account, "default" for the global
    WORKDAY_EMAIL/PASSWORD reused speculatively, and "none" when there is
    nothing to try. A caller that treats "default" as equivalent to "tenant"
    will spend a browser session failing to log in; the distinction is the
    whole point.
    """
    tenant = tenant_of(url_or_tenant) or (url_or_tenant or "").strip().lower()
    if tenant:
        account = load_accounts().get(tenant)
        if account and account.email and account.password:
            return account.email, account.password, "tenant"

    email, password = _global_credentials()
    if email and password:
        return email, password, "default"
    return "", "", "none"


def has_account(url_or_tenant: str) -> bool:
    """True only for an account recorded against this specific employer."""
    return credentials_for(url_or_tenant)[2] == "tenant"


def missing_tenants(urls: List[str]) -> List[str]:
    """Employers in this batch with no recorded account, newest-first order kept."""
    seen: List[str] = []
    for url in urls:
        tenant = tenant_of(url)
        if tenant and tenant not in seen and not has_account(tenant):
            seen.append(tenant)
    return seen


def signup_url(url: str) -> str:
    """Where the user goes once, by hand, to create the account for this tenant."""
    parsed = urlparse(url or "")
    if not parsed.netloc:
        return ""
    parts = [p for p in parsed.path.split("/") if p]
    site: List[str] = []
    for part in parts:
        low = part.lower()
        if low in {"en-us", "en-gb"}:
            site.append(part)
            continue
        if low in {"job", "jobs", "login", "apply", "userhome"}:
            break
        site.append(part)
    base = f"{parsed.scheme}://{parsed.netloc}"
    return f"{base}/{'/'.join(site)}/login" if site else f"{base}/login"
