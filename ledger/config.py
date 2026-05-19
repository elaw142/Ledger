from __future__ import annotations

import os
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent


def default_database_path() -> str:
    data_dir = Path(os.environ.get("LEDGER_DATA_DIR", PROJECT_DIR / "ledger-data"))
    return str(Path(os.environ.get("LEDGER_DATABASE", data_dir / "ledger.sqlite3")))


class Config:
    SECRET_KEY = os.environ.get("LEDGER_SECRET_KEY", "dev-ledger-change-me")
    DATABASE = default_database_path()
    PUBLIC_URL = os.environ.get("LEDGER_PUBLIC_URL", "https://ledger.emlw.dev/").rstrip("/") + "/"
    AKAHU_BASE_URL = os.environ.get("AKAHU_BASE_URL", "https://api.akahu.io")
    AKAHU_APP_TOKEN = os.environ.get("AKAHU_APP_TOKEN", "")
    AKAHU_USER_TOKEN = os.environ.get("AKAHU_USER_TOKEN", "")
    ALLOWED_CONNECTIONS = [
        value.strip()
        for value in os.environ.get("LEDGER_ALLOWED_CONNECTIONS", "ANZ").split(",")
        if value.strip()
    ]
    DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
    ADMIN_PASSWORD = os.environ.get("LEDGER_ADMIN_PASSWORD", "")
    PASSWORD_HASH = os.environ.get("LEDGER_PASSWORD_HASH", "")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SYNC_LOOKBACK_DAYS = int(os.environ.get("LEDGER_SYNC_LOOKBACK_DAYS", "400"))


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret"

