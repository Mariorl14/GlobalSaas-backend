import os
from pathlib import Path

from datetime import timedelta
from dotenv import load_dotenv

# Always load .env from the barber-backend folder (works even if cwd is elsewhere).
_BASE_DIR = Path(__file__).resolve().parent
load_dotenv(_BASE_DIR / ".env")


def _normalize_database_url(url: str | None) -> str | None:
    if not url or not str(url).strip():
        return None
    url = str(url).strip()
    # Some hosts (e.g. older Heroku) use postgres:// — SQLAlchemy expects postgresql://
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Uploads (business logos, etc.)
    UPLOAD_FOLDER = os.getenv(
        "UPLOAD_FOLDER",
        str((_BASE_DIR / "uploads").resolve()),
    )
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", str(2 * 1024 * 1024)))

    # JWT
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-change-me")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(
        hours=int(os.getenv("JWT_ACCESS_TOKEN_EXPIRES_HOURS", "1"))
    )

    # WhatsApp / Twilio (platform-level credentials)
    WHATSAPP_NOTIFICATIONS_ENABLED = _env_bool("WHATSAPP_NOTIFICATIONS_ENABLED", False)
    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM")
    TWILIO_WHATSAPP_CONTENT_SID = os.getenv("TWILIO_WHATSAPP_CONTENT_SID")
    TWILIO_REQUEST_TIMEOUT = int(os.getenv("TWILIO_REQUEST_TIMEOUT", "10"))
    # Fallback ISO 3166-1 alpha-2 when business.country_code is unset.
    DEFAULT_PHONE_COUNTRY_CODE = (os.getenv("DEFAULT_PHONE_COUNTRY_CODE") or "").strip().upper()

    # Email appointment confirmations (SMTP)
    EMAIL_NOTIFICATIONS_ENABLED = _env_bool("EMAIL_NOTIFICATIONS_ENABLED", False)
    MAIL_SERVER = os.getenv("MAIL_SERVER")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USE_TLS = _env_bool("MAIL_USE_TLS", True)
    MAIL_USE_SSL = _env_bool("MAIL_USE_SSL", False)
    MAIL_USERNAME = os.getenv("MAIL_USERNAME")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER")
    MAIL_DEFAULT_SENDER_NAME = os.getenv("MAIL_DEFAULT_SENDER_NAME", "Barber Suite")
    MAIL_TIMEOUT = int(os.getenv("MAIL_TIMEOUT", "15"))
