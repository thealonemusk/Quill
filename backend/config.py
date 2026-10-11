from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List
from pathlib import Path

# Load .env from config directory or root
env_path = Path(__file__).parent.parent / "config" / ".env"
if not env_path.exists():
    env_path = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    # AI
    gemini_api_key: str = ""
    gpt_key: str = ""

    # Platform credentials
    linkedin_email: str = ""
    linkedin_password: str = ""

    # Indeed — Google-auth only, no password needed
    indeed_email: str = ""
    indeed_password: str = ""          # leave blank if using Google auth
    indeed_use_google_auth: bool = True

    # Naukri — Google-auth only, no password needed
    naukri_email: str = ""
    naukri_password: str = ""          # leave blank if using Google auth
    naukri_use_google_auth: bool = True

    # InstaHyre — Google-auth only, no password needed
    instahyre_email: str = ""
    instahyre_password: str = ""       # leave blank if using Google auth
    instahyre_use_google_auth: bool = True

    workday_email: str = ""
    workday_password: str = ""

    # User profile
    user_full_name: str = "Ashutosh Jha"
    user_email: str = ""
    user_phone: str = ""
    user_location: str = "Bengaluru, India"
    user_github: str = ""
    user_linkedin_url: str = ""
    user_portfolio: str = ""

    # Job preferences
    target_roles: str = "Software Engineer,Backend Engineer"
    experience_years: int = 2
    preferred_locations: str = "Bengaluru,Remote"
    min_relevance_score: int = 55
    excluded_companies: str = ""

    # Resume tailoring
    openai_model: str = "gpt-5.4"
    # Any OpenAI-compatible endpoint: a proxy, a gateway, or a local runtime
    # such as Ollama (http://localhost:11434/v1). Blank means api.openai.com.
    openai_base_url: str = ""
    # "gemini" sends tailoring to Google with GEMINI_API_KEY / GEMINI_MODEL;
    # blank or "openai" uses gpt_key. See backend/resume/llm.py.
    llm_provider: str = ""
    # gemini-2.x is closed to new keys; 3.6-flash is what Google points to.
    gemini_model: str = "gemini-3.6-flash"
    # Tried when the primary is overloaded or out of quota; blank for none.
    gemini_fallback_model: str = ""
    # LLM_PROVIDER=openrouter: OpenAI-compatible, one key for many models.
    open_router_api_key: str = ""
    openrouter_model: str = "openrouter/free"
    openrouter_fallback_model: str = ""
    tectonic_path: str = ""                  # blank -> tools/tectonic.exe, then PATH
    master_resume_path: str = "resumes/master_resume.tex"

    # App
    base_resume_path: str = "resumes/base_resume.tex"
    database_url: str = "sqlite:///./tempoapply.db"
    # Localhost only. 0.0.0.0 served the profile, the resume and every
    # write endpoint to anything on the same Wi-Fi, with no login.
    api_host: str = "127.0.0.1"
    # Pins the extension allowed to call the API (chrome://extensions shows
    # the 32-letter id). Blank accepts any extension origin.
    extension_id: str = ""
    api_port: int = 8000

    class Config:
        env_file = str(env_path)
        env_file_encoding = "utf-8"
        extra = "ignore"

    @property
    def target_roles_list(self) -> List[str]:
        return [r.strip() for r in self.target_roles.split(",")]

    @property
    def preferred_locations_list(self) -> List[str]:
        return [l.strip() for l in self.preferred_locations.split(",")]

    @property
    def excluded_companies_list(self) -> List[str]:
        if not self.excluded_companies:
            return []
        return [c.strip() for c in self.excluded_companies.split(",")]


settings = Settings()
