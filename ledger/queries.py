from __future__ import annotations

from datetime import date

from .db import connection, row_to_dict


def latest_sync(database_path: str) -> dict | None:
    with connection(database_path) as con:
        row = con.execute(
            "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row_to_dict(row)


def accounts(database_path: str) -> list[dict]:
    with connection(database_path) as con:
        return [
            row_to_dict(row)
            for row in con.execute(
                """
                SELECT id, name, connection_name, connection_type, account_type,
                       formatted_account, currency, balance_current_cents,
                       balance_available_cents, updated_at
                FROM accounts
                ORDER BY name
                """
            )
        ]


def review_count(database_path: str) -> int:
    with connection(database_path) as con:
        row = con.execute(
            "SELECT COUNT(*) AS count FROM transactions WHERE review_status = 'needs_review'"
        ).fetchone()
        return int(row["count"])


def categories(database_path: str) -> list[str]:
    with connection(database_path) as con:
        rows = con.execute(
            """
            SELECT DISTINCT effective_category AS category
            FROM transactions
            WHERE effective_category IS NOT NULL AND effective_category != ''
            ORDER BY effective_category
            """
        ).fetchall()
        return [row["category"] for row in rows]


def spending_by_category(database_path: str, *, start: str | None = None, end: str | None = None) -> list[dict]:
    start = start or date.today().replace(day=1).isoformat()
    params: list[str] = [start]
    clause = "date >= ? AND amount_cents < 0 AND pending = 0"
    if end:
        clause += " AND date <= ?"
        params.append(end)
    with connection(database_path) as con:
        return [
            dict(row)
            for row in con.execute(
                f"""
                SELECT COALESCE(effective_category, 'Uncategorized') AS category,
                       SUM(ABS(amount_cents)) AS spend_cents,
                       COUNT(*) AS count
                FROM transactions
                WHERE {clause}
                GROUP BY COALESCE(effective_category, 'Uncategorized')
                ORDER BY spend_cents DESC
                """,
                params,
            )
        ]


def transactions(database_path: str, filters: dict) -> list[dict]:
    clauses = ["1 = 1"]
    params: list[str] = []
    if filters.get("account"):
        clauses.append("account_id = ?")
        params.append(filters["account"])
    if filters.get("category"):
        clauses.append("effective_category = ?")
        params.append(filters["category"])
    if filters.get("start"):
        clauses.append("date >= ?")
        params.append(filters["start"])
    if filters.get("end"):
        clauses.append("date <= ?")
        params.append(filters["end"])
    if filters.get("review_status"):
        clauses.append("review_status = ?")
        params.append(filters["review_status"])
    where = " AND ".join(clauses)
    with connection(database_path) as con:
        return [
            row_to_dict(row)
            for row in con.execute(
                f"""
                SELECT t.*, a.name AS account_name, a.currency
                FROM transactions t
                JOIN accounts a ON a.id = t.account_id
                WHERE {where}
                ORDER BY t.date DESC, t.updated_at DESC
                LIMIT 500
                """,
                params,
            )
        ]

