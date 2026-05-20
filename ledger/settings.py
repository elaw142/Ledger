from __future__ import annotations

from typing import Optional

from .db import connection, utc_now


SECRET_KEYS = {"AKAHU_APP_TOKEN", "AKAHU_USER_TOKEN", "DISCORD_WEBHOOK_URL"}


def get_setting(database_path: str, key: str, default: str = "") -> str:
    with connection(database_path) as con:
        row = con.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row and row["value"] else default


def get_runtime_setting(database_path: str, config: dict, key: str) -> str:
    return config.get(key) or get_setting(database_path, key, "")


def set_setting(database_path: str, key: str, value: Optional[str]) -> None:
    with connection(database_path) as con:
        con.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value or "", utc_now()),
        )


def configured_settings(database_path: str, config: dict) -> dict:
    return {
        "akahu_app_token": bool(get_runtime_setting(database_path, config, "AKAHU_APP_TOKEN")),
        "akahu_user_token": bool(get_runtime_setting(database_path, config, "AKAHU_USER_TOKEN")),
        "discord_webhook_url": bool(get_runtime_setting(database_path, config, "DISCORD_WEBHOOK_URL")),
        "public_url": config.get("PUBLIC_URL"),
        "allowed_connections": config.get("ALLOWED_CONNECTIONS", []),
    }

