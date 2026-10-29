"""Applicant profile used to fill ATS forms. No LLM — structured data only."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROFILE_PATH = PROJECT_ROOT / "config" / "applicant_profile.json"
RESUMES_DIR = PROJECT_ROOT / "resumes"
RESUME_SUFFIXES = {".pdf", ".doc", ".docx"}


def safe_resume_path(value: str) -> Optional[Path]:
    """
    `value` as a resume file inside `resumes/`, or None.

    The resume path is sent to employers and served by `/api/autofill/resume`,
    so it must not be able to name any other file: set to `config/.env`, that
    endpoint returned the API keys and passwords as "the resume".
    """
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        path = path.resolve()
        path.relative_to(RESUMES_DIR.resolve())
    except (OSError, ValueError):
        return None
    return path if path.suffix.lower() in RESUME_SUFFIXES else None


@dataclass
class Education:
    school: str = ""
    degree: str = ""
    major: str = ""
    start_year: str = ""
    end_year: str = ""


@dataclass
class Experience:
    title: str = ""
    company: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    description: str = ""


@dataclass
class ApplicantProfile:
    first_name: str = ""
    last_name: str = ""
    full_name: str = ""
    email: str = ""
    phone: str = ""
    phone_country: str = "India"
    phone_country_code: str = "+91"
    city: str = ""
    state: str = ""
    country: str = "India"
    country_code: str = "IN"
    postal_code: str = ""
    address_line1: str = ""
    address_line2: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    current_title: str = "Software Engineer"
    current_company: str = ""
    years_experience: str = "1"
    notice_period: str = "Immediate"
    earliest_start: str = "Immediately"
    salary_expectation: str = "Negotiable"
    authorized_to_work: bool = True
    require_sponsorship: bool = False
    gender: str = "Decline to self-identify"
    ethnicity: str = "Decline to self-identify"
    veteran: str = "I am not a protected veteran"
    disability: str = "I do not want to answer"
    how_heard: str = "Company website"
    education: List[Education] = field(default_factory=list)
    experience: List[Experience] = field(default_factory=list)
    skills: str = "Python, JavaScript, TypeScript, SQL, React, FastAPI, Git"
    cover_letter_template: str = (
        "Dear Hiring Team,\n\n"
        "I am applying for the {title} role at {company}. I am a software engineer "
        "with hands-on experience building backend and full-stack systems, and I would "
        "welcome the opportunity to contribute.\n\n"
        "Best regards,\n{name}"
    )
    custom_answers: Dict[str, str] = field(default_factory=dict)
    resume_path: str = "resumes/resume.pdf"
    auto_submit: bool = False  # ignored: Quill never submits

    def split_name(self) -> None:
        if self.first_name and self.last_name:
            if not self.full_name:
                self.full_name = f"{self.first_name} {self.last_name}".strip()
            return
        name = (self.full_name or "").strip()
        parts = name.split()
        if not self.first_name:
            self.first_name = parts[0] if parts else ""
        if not self.last_name:
            self.last_name = " ".join(parts[1:]) if len(parts) > 1 else (parts[0] if parts else "")
        if not self.full_name:
            self.full_name = f"{self.first_name} {self.last_name}".strip()

    def phone_e164(self) -> str:
        digits = re.sub(r"\D", "", self.phone or "")
        if digits.startswith("91") and len(digits) >= 12:
            return "+" + digits
        # "09939964663" is how an Indian number is commonly written down. The
        # trunk prefix is not part of the international form, and without this
        # the number fell through every branch below and was emitted raw — an
        # invalid value in a field that asked for E.164.
        if len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        if len(digits) == 10:
            return "+91" + digits
        if (self.phone or "").startswith("+"):
            return "+" + digits
        return self.phone or ""

    def phone_national(self) -> str:
        digits = re.sub(r"\D", "", self.phone or "")
        if digits.startswith("91") and len(digits) >= 12:
            return digits[-10:]
        return digits[-10:] if len(digits) >= 10 else digits

    def location_string(self) -> str:
        parts = [p for p in [self.city, self.state, self.country] if p]
        return ", ".join(parts)

    def available_date_iso(self) -> str:
        return (date.today() + timedelta(days=30)).isoformat()

    def cover_letter(self, title: str, company: str) -> str:
        try:
            return self.cover_letter_template.format(
                title=title or "this role",
                company=company or "your company",
                name=self.full_name,
            )
        except Exception:
            return self.cover_letter_template

    def resume_file(self) -> Optional[Path]:
        path = safe_resume_path(self.resume_path)
        if path is not None and path.is_file():
            return path
        for candidate in sorted(RESUMES_DIR.glob("*")):
            if candidate.suffix.lower() in {".pdf", ".doc", ".docx"}:
                return candidate
        return None

    def missing_required(self) -> List[str]:
        self.split_name()
        missing = []
        if not self.full_name:
            missing.append("full_name")
        if not self.email or "@" not in self.email:
            missing.append("email")
        if not self.phone_national():
            missing.append("phone")
        return missing

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return data


def _parse_education(raw: Any) -> List[Education]:
    items = []
    if not isinstance(raw, list):
        return items
    for row in raw:
        if not isinstance(row, dict):
            continue
        items.append(Education(
            school=str(row.get("school", "")),
            degree=str(row.get("degree", "")),
            major=str(row.get("major", "")),
            start_year=str(row.get("start_year", "")),
            end_year=str(row.get("end_year", "")),
        ))
    return items


def _parse_experience(raw: Any) -> List[Experience]:
    items = []
    if not isinstance(raw, list):
        return items
    for row in raw:
        if not isinstance(row, dict):
            continue
        items.append(Experience(
            title=str(row.get("title", "")),
            company=str(row.get("company", "")),
            location=str(row.get("location", "")),
            start_date=str(row.get("start_date", "")),
            end_date=str(row.get("end_date", "")),
            description=str(row.get("description", "")),
        ))
    return items


def _defaults_from_settings() -> Dict[str, Any]:
    full = (settings.user_full_name or "").strip()
    parts = full.split()
    loc = settings.user_location or ""
    city = loc.split(",")[0].strip() if loc else ""
    email = (
        getattr(settings, "user_email", "")
        or settings.naukri_email
        or settings.indeed_email
        or settings.linkedin_email
        or ""
    )
    return {
        "full_name": full,
        "first_name": parts[0] if parts else "",
        "last_name": " ".join(parts[1:]) if len(parts) > 1 else "",
        "email": email,
        "phone": settings.user_phone or "",
        "city": city,
        "country": "India",
        "linkedin": settings.user_linkedin_url or "",
        "github": settings.user_github or "",
        "portfolio": settings.user_portfolio or "",
        "years_experience": str(settings.experience_years),
        "current_title": (settings.target_roles_list or ["Software Engineer"])[0],
    }


def load_profile() -> ApplicantProfile:
    data = _defaults_from_settings()
    if PROFILE_PATH.exists():
        try:
            saved = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                data.update({k: v for k, v in saved.items() if v not in (None, "")})
                if saved.get("custom_answers"):
                    data["custom_answers"] = saved["custom_answers"]
                if "auto_submit" in saved:
                    data["auto_submit"] = bool(saved["auto_submit"])
                if saved.get("education"):
                    data["education"] = saved["education"]
                if saved.get("experience"):
                    data["experience"] = saved["experience"]
                for key in ("address_line1", "address_line2", "state", "postal_code",
                            "salary_expectation", "notice_period", "earliest_start",
                            "cover_letter_template", "skills", "resume_path"):
                    if key in saved:
                        data[key] = saved[key]
                for key in ("authorized_to_work", "require_sponsorship"):
                    if key in saved:
                        data[key] = bool(saved[key])
        except (json.JSONDecodeError, OSError):
            pass

    education = _parse_education(data.pop("education", []))
    experience = _parse_experience(data.pop("experience", []))
    allowed = {f.name for f in fields(ApplicantProfile)}
    clean = {k: v for k, v in data.items() if k in allowed}
    if "years_experience" in clean:
        clean["years_experience"] = str(clean["years_experience"])
    if "custom_answers" in clean and not isinstance(clean["custom_answers"], dict):
        clean["custom_answers"] = {}
    profile = ApplicantProfile(**clean)
    profile.education = education
    profile.experience = experience
    profile.split_name()
    return profile


def save_profile(updates: Dict[str, Any]) -> ApplicantProfile:
    current = load_profile().to_dict()
    for key, value in updates.items():
        if key == "resume_path" and value and safe_resume_path(str(value)) is None:
            raise ValueError("resume_path must be a .pdf, .doc or .docx inside resumes/")
        if key == "auto_submit":
            value = False                 # Quill never submits
        if key in current:
            current[key] = value
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return load_profile()


RESUME_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {{ font-family: Arial, Helvetica, sans-serif; font-size: 12px; color: #111; margin: 40px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .meta {{ color: #444; margin-bottom: 16px; }}
  h2 {{ font-size: 13px; border-bottom: 1px solid #ddd; padding-bottom: 3px; margin-top: 16px; }}
  p, li {{ line-height: 1.4; }}
</style></head><body>
<h1>{name}</h1>
<div class="meta">{contact}</div>
<h2>Summary</h2>
<p>Software engineer based in {location} with {years} year(s) of experience. Skills: {skills}.</p>
{experience}
{education}
<h2>Links</h2>
<p>{links}</p>
</body></html>"""


def resume_html(profile: ApplicantProfile) -> str:
    contact = " · ".join(x for x in [
        profile.email, profile.phone_e164() or profile.phone, profile.location_string()
    ] if x)
    links = " · ".join(x for x in [profile.linkedin, profile.github, profile.portfolio] if x)
    exp_html = ""
    usable_exp = [e for e in profile.experience if e.title or e.company]
    if usable_exp:
        blocks = []
        for e in usable_exp:
            blocks.append(
                f"<p><b>{e.title}</b> — {e.company}<br>{e.start_date} – {e.end_date or 'Present'}"
                f"<br>{e.description}</p>"
            )
        exp_html = "<h2>Experience</h2>" + "".join(blocks)
    edu_html = ""
    usable_edu = [e for e in profile.education if e.school]
    if usable_edu:
        blocks = []
        for e in usable_edu:
            blocks.append(
                f"<p><b>{e.school}</b> — {e.degree} {e.major}<br>{e.start_year} – {e.end_year}</p>"
            )
        edu_html = "<h2>Education</h2>" + "".join(blocks)
    return RESUME_HTML.format(
        name=profile.full_name,
        contact=contact,
        location=profile.location_string() or profile.country,
        years=profile.years_experience,
        skills=profile.skills,
        experience=exp_html,
        education=edu_html,
        links=links or "N/A",
    )


async def ensure_resume_pdf(page, profile: ApplicantProfile) -> Path:
    existing = profile.resume_file()
    if existing:
        return existing
    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    out = PROJECT_ROOT / "resumes" / "resume.pdf"
    await page.set_content(resume_html(profile))
    await page.pdf(path=str(out), format="A4")
    profile.resume_path = "resumes/resume.pdf"
    save_profile({"resume_path": profile.resume_path})
    return out
