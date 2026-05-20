from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from .categories import seed_categories


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(database_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(database_path), exist_ok=True)
    con = sqlite3.connect(database_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


@contextmanager
def connection(database_path: str):
    con = connect(database_path)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db(database_path: str) -> None:
    with connection(database_path) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                connection_name TEXT,
                connection_type TEXT,
                account_type TEXT,
                status TEXT,
                formatted_account TEXT,
                currency TEXT NOT NULL DEFAULT 'NZD',
                balance_current_cents INTEGER,
                balance_available_cents INTEGER,
                refreshed_balance_at TEXT,
                refreshed_transactions_at TEXT,
                raw_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS balance_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                current_cents INTEGER,
                available_cents INTEGER,
                currency TEXT NOT NULL DEFAULT 'NZD',
                captured_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_balance_snapshots_account_time
            ON balance_snapshots(account_id, captured_at);

            CREATE TABLE IF NOT EXISTS categories (
                name TEXT PRIMARY KEY COLLATE NOCASE,
                color TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 1000,
                is_system INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_categories_order
            ON categories(sort_order, name);

            CREATE TABLE IF NOT EXISTS merchant_overrides (
                merchant_key TEXT PRIMARY KEY,
                display_merchant TEXT,
                category_name TEXT,
                category_id TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS transaction_overrides (
                transaction_id TEXT PRIMARY KEY,
                display_merchant TEXT,
                category_name TEXT,
                category_id TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(transaction_id) REFERENCES transactions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                date TEXT NOT NULL,
                description TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                balance_cents INTEGER,
                type TEXT,
                pending INTEGER NOT NULL DEFAULT 0,
                akahu_merchant_id TEXT,
                akahu_merchant_name TEXT,
                akahu_category_id TEXT,
                akahu_category_name TEXT,
                merchant_key TEXT NOT NULL,
                effective_merchant TEXT NOT NULL,
                effective_category TEXT,
                review_status TEXT NOT NULL DEFAULT 'auto',
                review_reason TEXT,
                raw_json TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_transactions_account_date
            ON transactions(account_id, date DESC);

            CREATE INDEX IF NOT EXISTS idx_transactions_category_date
            ON transactions(effective_category, date DESC);

            CREATE INDEX IF NOT EXISTS idx_transactions_review
            ON transactions(review_status);

            CREATE TABLE IF NOT EXISTS sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                accounts_seen INTEGER NOT NULL DEFAULT 0,
                transactions_seen INTEGER NOT NULL DEFAULT 0,
                new_transactions INTEGER NOT NULL DEFAULT 0,
                updated_transactions INTEGER NOT NULL DEFAULT 0,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS notification_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                status TEXT NOT NULL,
                payload TEXT NOT NULL,
                response_body TEXT
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        seed_categories(con)


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    if row is None:
        return None
    result = dict(row)
    for key_name in ("raw_json",):
        if key_name in result and isinstance(result[key_name], str):
            try:
                result[key_name] = json.loads(result[key_name])
            except json.JSONDecodeError:
                pass
    return result
