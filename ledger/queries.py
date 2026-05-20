from __future__ import annotations

from datetime import date
from typing import Optional

from .categories import TRANSFER_CATEGORY, UNCATEGORIZED_CATEGORY, seed_categories
from .db import connection, row_to_dict


def _month_start() -> str:
    return date.today().replace(day=1).isoformat()


def _spending_clause(*, start: Optional[str], end: Optional[str]) -> tuple[str, list[str]]:
    params: list[str] = [TRANSFER_CATEGORY]
    clause = """
        pending = 0
        AND amount_cents < 0
        AND COALESCE(effective_category, '') != ?
    """
    if start:
        clause += " AND date >= ?"
        params.append(start)
    if end:
        clause += " AND date <= ?"
        params.append(end)
    return clause, params


def latest_sync(database_path: str) -> Optional[dict]:
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


def categories(database_path: str, *, start: Optional[str] = None, end: Optional[str] = None) -> list[dict]:
    start = start or _month_start()
    with connection(database_path) as con:
        seed_categories(con)
        return [
            dict(row)
            for row in con.execute(
            """
                WITH usage AS (
                    SELECT COALESCE(effective_category, ?) AS name,
                           COUNT(*) AS transaction_count
                    FROM transactions
                    GROUP BY COALESCE(effective_category, ?)
                ),
                spend AS (
                    SELECT COALESCE(effective_category, ?) AS name,
                           SUM(ABS(amount_cents)) AS spend_cents
                    FROM transactions
                    WHERE pending = 0
                      AND amount_cents < 0
                      AND COALESCE(effective_category, ?) != ?
                      AND date >= ?
                      AND (? IS NULL OR date <= ?)
                    GROUP BY COALESCE(effective_category, ?)
                )
                SELECT c.name, c.color, c.sort_order, c.is_system,
                       COALESCE(usage.transaction_count, 0) AS transaction_count,
                       COALESCE(spend.spend_cents, 0) AS spend_cents
                FROM categories c
                LEFT JOIN usage ON usage.name = c.name COLLATE NOCASE
                LEFT JOIN spend ON spend.name = c.name COLLATE NOCASE
                ORDER BY c.sort_order, c.name
                """,
                (
                    UNCATEGORIZED_CATEGORY,
                    UNCATEGORIZED_CATEGORY,
                    UNCATEGORIZED_CATEGORY,
                    TRANSFER_CATEGORY,
                    TRANSFER_CATEGORY,
                    start,
                    end,
                    end,
                    UNCATEGORIZED_CATEGORY,
                ),
            )
        ]


def spending_by_category(database_path: str, *, start: Optional[str] = None, end: Optional[str] = None) -> list[dict]:
    start = start or _month_start()
    clause, params = _spending_clause(start=start, end=end)
    with connection(database_path) as con:
        return [
            dict(row)
            for row in con.execute(
                f"""
                SELECT COALESCE(t.effective_category, ?) AS category,
                       COALESCE(c.color, '#006D77') AS color,
                       SUM(ABS(amount_cents)) AS spend_cents,
                       COUNT(*) AS count
                FROM transactions t
                LEFT JOIN categories c ON c.name = COALESCE(t.effective_category, ?) COLLATE NOCASE
                WHERE {clause}
                GROUP BY COALESCE(t.effective_category, ?), COALESCE(c.color, '#006D77')
                ORDER BY spend_cents DESC
                """,
                [UNCATEGORIZED_CATEGORY, UNCATEGORIZED_CATEGORY, *params, UNCATEGORIZED_CATEGORY],
            )
        ]


def total_spend(database_path: str, *, start: Optional[str] = None, end: Optional[str] = None) -> int:
    start = start or _month_start()
    clause, params = _spending_clause(start=start, end=end)
    with connection(database_path) as con:
        row = con.execute(
            f"SELECT COALESCE(SUM(ABS(amount_cents)), 0) AS spend_cents FROM transactions WHERE {clause}",
            params,
        ).fetchone()
        return int(row["spend_cents"] or 0)


def net_cash_flow(database_path: str, *, start: Optional[str] = None, end: Optional[str] = None) -> int:
    start = start or _month_start()
    params: list[str] = [TRANSFER_CATEGORY]
    clause = """
        pending = 0
        AND COALESCE(effective_category, '') != ?
    """
    if start:
        clause += " AND date >= ?"
        params.append(start)
    if end:
        clause += " AND date <= ?"
        params.append(end)
    with connection(database_path) as con:
        row = con.execute(
            f"SELECT COALESCE(SUM(amount_cents), 0) AS cash_flow_cents FROM transactions WHERE {clause}",
            params,
        ).fetchone()
        return int(row["cash_flow_cents"] or 0)


def monthly_spending(database_path: str, *, start: Optional[str] = None, end: Optional[str] = None) -> list[dict]:
    if not start:
        today = date.today()
        month_index = today.year * 12 + today.month - 1 - 11
        start_year = month_index // 12
        start_month = month_index % 12 + 1
        start = date(start_year, start_month, 1).isoformat()
    clause, params = _spending_clause(start=start, end=end)
    with connection(database_path) as con:
        return [
            dict(row)
            for row in con.execute(
                f"""
                SELECT substr(date, 1, 7) AS month,
                       COALESCE(SUM(ABS(amount_cents)), 0) AS spend_cents,
                       COUNT(*) AS count
                FROM transactions
                WHERE {clause}
                GROUP BY substr(date, 1, 7)
                ORDER BY month
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
