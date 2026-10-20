from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    DATABASE_URL: str = f"sqlite:///{(BASE_DIR / 'edupulse.db').as_posix()}"

    JWT_SECRET: str = "dev-secret-change-me"
    JWT_EXPIRE_MINUTES: int = 60 * 24
    ADMIN_INVITE_CODE: str = "EDUPULSE-ADMIN"

    FRONTEND_URL: str = "http://localhost:3000"
    SEED_DEMO_DATA: bool = True

    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "EduPulse AI <no-reply@edupulse.ai>"
    SMTP_USE_TLS: bool = True
    LOGIN_ALERT_EMAILS: bool = True

    LLM_PROVIDER: str = ""
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-flash-latest"
    GEMINI_FALLBACK_MODEL: str = "gemini-flash-lite-latest"
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.SMTP_HOST)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
