from __future__ import annotations

import json

import requests

from .db import connection, utc_now
from .queries import review_count


def send_discord(database_path: str, webhook_url: str, payload: dict) -> dict:
    if not webhook_url:
        return {"status": "skipped", "reason": "DISCORD_WEBHOOK_URL is not configured"}
    response = requests.post(webhook_url, json=payload, timeout=20)
    status = "sent" if response.status_code < 400 else "error"
    with connection(database_path) as con:
        con.execute(
            """
            INSERT INTO notification_logs (type, sent_at, status, payload, response_body)
            VALUES ('discord', ?, ?, ?, ?)
            """,
            (utc_now(), status, json.dumps(payload, sort_keys=True), response.text[:1000]),
        )
    return {"status": status, "http_status": response.status_code}


def notify_review_needed(database_path: str, webhook_url: str, public_url: str) -> dict:
    count = review_count(database_path)
    if count == 0:
        return {"status": "skipped", "reason": "no review items", "review_count": 0}
    content = f"Ledger has {count} transaction{'s' if count != 1 else ''} waiting for review: {public_url}"
    result = send_discord(database_path, webhook_url, {"content": content})
    result["review_count"] = count
    return result

