"""Local configuration; environment variables override the project .env file."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str = field(repr=False)
    telegram_test_token: str = field(repr=False)
    google_calendar_id: str = field(default='',repr=False)
    google_token_file: str = ''
    google_client_file: str = ''


def load_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    required = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        raise ValueError("Missing settings: " + ", ".join(missing))
    try:
        port = int(os.getenv("POSTGRES_PORT", "15432"))
    except ValueError:
        raise ValueError("POSTGRES_PORT must be an integer") from None
    if not 1 <= port <= 65535:
        raise ValueError("POSTGRES_PORT must be between 1 and 65535")
    return Settings(
        postgres_host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        postgres_port=port,
        postgres_db=os.environ["POSTGRES_DB"],
        postgres_user=os.environ["POSTGRES_USER"],
        postgres_password=os.environ["POSTGRES_PASSWORD"],
        telegram_test_token=os.getenv("TELEGRAM_TEST", "").strip(),
        google_calendar_id=os.getenv('GOOGLE_CALENDAR_ID',os.getenv('GOOGLE_CALENDAR','')).strip(),
        google_token_file=os.getenv('GOOGLE_TOKEN_FILE','').strip(),
        google_client_file=os.getenv('GOOGLE_OAUTH_CLIENT_FILE',''),
    )
