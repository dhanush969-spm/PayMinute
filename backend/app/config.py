"""Settings from environment variables and .env."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(ROOT_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# default base URL and model per provider (override with LLM_MODEL)
LLM_DEFAULTS = {
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4.1-mini"},
    "ollama": {"base_url": "http://localhost:11434/v1", "model": "llama3.1"},
    "anthropic": {"base_url": "https://api.anthropic.com", "model": "claude-sonnet-4-5"},
}


@dataclass
class Settings:
    paypal_client_id: str = field(default_factory=lambda: os.getenv("PAYPAL_CLIENT_ID", "").strip())
    paypal_client_secret: str = field(
        default_factory=lambda: (os.getenv("PAYPAL_CLIENT_SECRET") or os.getenv("PAYPAL_SECRET") or "").strip()
    )
    paypal_webhook_id: str = field(default_factory=lambda: os.getenv("PAYPAL_WEBHOOK_ID", "").strip())

    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "").strip().lower())
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", "").strip())
    llm_model_raw: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "").strip())
    llm_base_url_raw: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "").strip())

    database_path: str = field(
        default_factory=lambda: os.getenv("DATABASE_PATH", str(BACKEND_DIR / "payminute.db"))
    )
    # number/date format in the UI, e.g. en-IN
    locale: str = field(default_factory=lambda: os.getenv("PAYMINUTE_LOCALE", "en-US").strip() or "en-US")
    currency: str = field(default_factory=lambda: os.getenv("PAYMINUTE_CURRENCY", "USD").strip().upper())
    app_base_url: str = field(default_factory=lambda: os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/"))
    # shown on PayPal checkout and payout emails
    business_name: str = field(default_factory=lambda: os.getenv("PAYMINUTE_BUSINESS_NAME", "PayMinute"))
    # country for numbers typed without +code
    phone_region: str = field(default_factory=lambda: os.getenv("PAYMINUTE_PHONE_REGION", "US").strip().upper() or "US")
    # require Log in with PayPal for the API
    auth_required: bool = field(default_factory=lambda: _bool("AUTH_REQUIRED", True))
    # signs the login cookie; random if unset
    session_secret: str = field(default_factory=lambda: os.getenv("SESSION_SECRET", "").strip() or secrets.token_urlsafe(32))
    # log out after this many minutes without activity (0 = never); a session also ends 8 hours after login
    session_idle_minutes: int = field(default_factory=lambda: int(os.getenv("SESSION_IDLE_MINUTES", "15")))
    session_hours: int = field(default_factory=lambda: int(os.getenv("SESSION_HOURS", "8")))
    # optional allow-list of PayPal emails
    # must match the attributes enabled under "Log in with PayPal" in the PayPal app
    login_scopes: str = field(default_factory=lambda: (os.getenv("PAYPAL_LOGIN_SCOPES") or "openid profile email phone https://uri.paypal.com/services/paypalattributes").strip())
    allowed_emails: list[str] = field(default_factory=lambda: [e.strip().lower() for e in os.getenv("ALLOWED_PAYPAL_EMAILS", "").split(",") if e.strip()])
    status_poll_seconds: int = field(default_factory=lambda: int(os.getenv("STATUS_POLL_SECONDS", "60")))
    # incoming payments at or above this score are held
    hold_threshold: float = field(default_factory=lambda: float(os.getenv("PAYMINUTE_HOLD_THRESHOLD", "0.45")))

    @property
    def paypal_configured(self) -> bool:
        return bool(self.paypal_client_id and self.paypal_client_secret)

    @property
    def paypal_mode(self) -> str:
        return "sandbox"

    @property
    def resolved_llm_provider(self) -> str:
        if self.llm_provider in {"none", "offline"}:
            return "offline"
        if self.llm_provider and self.llm_provider in LLM_DEFAULTS:
            if self.llm_provider == "ollama" or self.llm_api_key:
                return self.llm_provider
            return "offline"
        if self.llm_provider == "openai_compatible" and self.llm_base_url_raw:
            return "openai_compatible"
        return "offline"

    @property
    def llm_fallback_models(self) -> list[str]:
        raw = os.getenv("LLM_FALLBACK_MODELS")
        if raw is None:
            raw = LLM_DEFAULTS.get(self.resolved_llm_provider, {}).get("fallbacks", "")
        return [m.strip() for m in raw.split(",") if m.strip() and m.strip() != self.llm_model]

    @property
    def llm_model(self) -> str:
        if self.llm_model_raw:
            return self.llm_model_raw
        return LLM_DEFAULTS.get(self.resolved_llm_provider, {}).get("model", "")

    @property
    def llm_base_url(self) -> str:
        if self.llm_base_url_raw:
            return self.llm_base_url_raw.rstrip("/")
        return LLM_DEFAULTS.get(self.resolved_llm_provider, {}).get("base_url", "")


settings = Settings()


def reload_settings() -> Settings:
    """Reload settings from the environment."""
    global settings
    settings = Settings()
    return settings
