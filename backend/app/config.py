import os
from pathlib import Path

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[1]
INSTANCE_DIR = BACKEND_DIR / "instance"
load_dotenv(BACKEND_DIR / ".env")


def _database_url() -> str:
    configured = os.getenv("DATABASE_URL")
    if configured:
        if configured.startswith("sqlite:///./"):
            relative = configured.removeprefix("sqlite:///./")
            return f"sqlite:///{(BACKEND_DIR / relative).as_posix()}"
        if configured == "sqlite:///instance/learning_assistant.db":
            return f"sqlite:///{(INSTANCE_DIR / 'learning_assistant.db').as_posix()}"
        return configured
    return f"sqlite:///{(INSTANCE_DIR / 'learning_assistant.db').as_posix()}"


def _cors_origins() -> list[str]:
    value = os.getenv("CORS_ORIGINS", "http://127.0.0.1:5500,http://localhost:5500")
    return [origin.strip() for origin in value.split(",") if origin.strip()]


class BaseConfig:
    SECRET_KEY = os.getenv("SECRET_KEY", "development-only-change-me")
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JSON_SORT_KEYS = False
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_UPLOAD_MB", "100")) * 1024 * 1024
    UPLOAD_FOLDER = str(INSTANCE_DIR / "uploads")
    PROCESS_MATERIALS_INLINE = False
    PROCESS_AI_INLINE = False
    CORS_ORIGINS = _cors_origins()

    LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").strip().rstrip("/")
    LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
    LLM_MODEL = os.getenv("LLM_MODEL", "").strip()
    LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "120"))
    LLM_MAX_ATTEMPTS = 3


class DevelopmentConfig(BaseConfig):
    DEBUG = True


class TestingConfig(BaseConfig):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


class ProductionConfig(BaseConfig):
    DEBUG = False


CONFIGS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
