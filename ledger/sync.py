from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple

from .akahu import AkahuClient
from .categories import TRANSFER_CATEGORY, ensure_category
from .db import connection, utc_now
from .money import to_cents
from .text import compact, key


UNCERTAIN_CATEGORIES = {"", "unknown", "uncategorised", "uncategorized", "other"}
TRANSFER_TYPES = {"TRANSFER"}
TRANSFER_CATEGORY_KEYS = {"transfer", "transfers", "account transfer"}
PAIR_TRANSFER_TYPES = {"TRANSFER", "STANDING ORDER", "PAYMENT", "DIRECT CREDIT", "DEBIT", "CREDIT"}


@dataclass
class SyncResult:
    status: str
    accounts_seen: int
    transactions_seen: int
    new_transactions: int
    updated_transactions: int
    error: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "accounts_seen": self.accounts_seen,
            "transactions_seen": self.transactions_seen,
            "new_transactions": self.new_transactions,
            "updated_transactions": self.updated_transactions,
            "error": self.error,
        }


def _extract_connection(account: dict) -> tuple[str, str]:
    connection_info = account.get("connection") or {}
    return (
        compact(connection_info.get("name") or account.get("connection_name")),
        compact(connection_info.get("connection_type") or connection_info.get("type")),
    )


def _allowed_account(account: dict, allowed_connections: list[str]) -> bool:
    if not allowed_connections:
        return True
    connection_name, _ = _extract_connection(account)
    lowered = connection_name.lower()
    return any(allowed.lower() in lowered for allowed in allowed_connections)


def validate_account_is_safe(account: dict, allowed_connections: list[str]) -> None:
    connection_name, connection_type = _extract_connection(account)
    if not _allowed_account(account, allowed_connections):
        return
    if "anz" in connection_name.lower() and connection_type.lower() != "official":
        account_name = account.get("name") or account.get("_id") or "unknown account"
        raise ValueError(
            f"Refusing to sync {account_name}: ANZ connection is {connection_type or 'unknown'}, not official"
        )


def account_id(account: dict) -> str:
    return account.get("_id") or account.get("id")


def account_name(account: dict) -> str:
    return compact(account.get("name") or account.get("formatted_account") or account_id(account))


def _balance_value(account: dict, *names: str):
    balance = account.get("balance")
    if isinstance(balance, dict):
        for name in names:
            if name in balance:
                return balance.get(name)
    for name in names:
        if name in account:
            return account.get(name)
    return None


def _transaction_date(transaction: dict) -> str:
    value = transaction.get("date") or transaction.get("posted_at") or transaction.get("created_at")
    if not value:
        return datetime.now(timezone.utc).date().isoformat()
    return str(value)[:10]


def _merchant(transaction: dict) -> Tuple[Optional[str], Optional[str]]:
    merchant = transaction.get("merchant")
    if isinstance(merchant, dict):
        return merchant.get("_id") or merchant.get("id"), compact(merchant.get("name"))
    if isinstance(merchant, str):
        return None, compact(merchant)
    return None, None


def _category(transaction: dict) -> Tuple[Optional[str], Optional[str]]:
    category = transaction.get("category")
    if isinstance(category, dict):
        groups = category.get("groups") or {}
        personal_finance = groups.get("personal_finance") if isinstance(groups, dict) else None
        if isinstance(personal_finance, dict) and personal_finance.get("name"):
            return personal_finance.get("_id") or personal_finance.get("id"), compact(personal_finance.get("name"))
        return category.get("_id") or category.get("id"), compact(category.get("name") or category.get("label"))
    if isinstance(category, str):
        return None, compact(category)
    return None, None


def _description(transaction: dict) -> str:
    return compact(
        transaction.get("description")
        or transaction.get("particulars")
        or transaction.get("reference")
        or transaction.get("name")
        or "Transaction"
    )


def _is_useful_category(category_name: Optional[str]) -> bool:
    return key(category_name) not in UNCERTAIN_CATEGORIES


def _is_transfer_transaction(transaction: dict, category_name: Optional[str]) -> bool:
    category_key = key(category_name)
    tx_type = compact(transaction.get("type")).upper()
    if tx_type in TRANSFER_TYPES:
        return True
    return category_key in TRANSFER_CATEGORY_KEYS


def _source_merchant_key(merchant_name: Optional[str], description: str) -> str:
    return key(merchant_name or description)


def _has_transactions(account: dict) -> bool:
    attributes = account.get("attributes")
    if attributes is None:
        return True
    return "TRANSACTIONS" in attributes


def _effective_fields(con, merchant_key: str, transaction_id: str, merchant_name: Optional[str], category_name: Optional[str]):
    merchant_override = con.execute(
        "SELECT display_merchant, category_name, category_id FROM merchant_overrides WHERE merchant_key = ?",
        (merchant_key,),
    ).fetchone()
    tx_override = con.execute(
        "SELECT display_merchant, category_name, category_id FROM transaction_overrides WHERE transaction_id = ?",
        (transaction_id,),
    ).fetchone()

    effective_merchant = compact(merchant_name) or merchant_key or "Unknown merchant"
    effective_category = compact(category_name) or None

    if merchant_override:
        effective_merchant = compact(merchant_override["display_merchant"]) or effective_merchant
        effective_category = compact(merchant_override["category_name"]) or effective_category
    if tx_override:
        effective_merchant = compact(tx_override["display_merchant"]) or effective_merchant
        effective_category = compact(tx_override["category_name"]) or effective_category
    return effective_merchant, effective_category


def _apply_transfer_category(effective_category: Optional[str], transaction: dict, category_name: Optional[str]) -> Optional[str]:
    if _is_transfer_transaction(transaction, category_name):
        return TRANSFER_CATEGORY
    return effective_category


def _review_state(existing, category_name: Optional[str], effective_category: Optional[str], category_changed: bool) -> Tuple[str, Optional[str]]:
    if not _is_useful_category(effective_category or category_name):
        return "needs_review", "missing_category"
    if category_changed:
        return "needs_review", "akahu_category_changed"
    if existing and existing["review_status"] in {"reviewed", "ignored"}:
        return existing["review_status"], existing["review_reason"]
    return "auto", None


def _upsert_account(con, account: dict) -> None:
    now = utc_now()
    connection_name, connection_type = _extract_connection(account)
    balance_current = to_cents(_balance_value(account, "current", "current_balance", "balance"))
    balance_available = to_cents(_balance_value(account, "available", "available_balance"))
    currency = account.get("currency") or ((account.get("balance") or {}).get("currency") if isinstance(account.get("balance"), dict) else None) or "NZD"
    con.execute(
        """
        INSERT INTO accounts (
            id, name, connection_name, connection_type, account_type, status,
            formatted_account, currency, balance_current_cents, balance_available_cents,
            refreshed_balance_at, refreshed_transactions_at, raw_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            connection_name = excluded.connection_name,
            connection_type = excluded.connection_type,
            account_type = excluded.account_type,
            status = excluded.status,
            formatted_account = excluded.formatted_account,
            currency = excluded.currency,
            balance_current_cents = excluded.balance_current_cents,
            balance_available_cents = excluded.balance_available_cents,
            refreshed_balance_at = excluded.refreshed_balance_at,
            refreshed_transactions_at = excluded.refreshed_transactions_at,
            raw_json = excluded.raw_json,
            updated_at = excluded.updated_at
        """,
        (
            account_id(account),
            account_name(account),
            connection_name,
            connection_type,
            account.get("type"),
            account.get("status"),
            account.get("formatted_account"),
            currency,
            balance_current,
            balance_available,
            account.get("refreshed", {}).get("balance") if isinstance(account.get("refreshed"), dict) else None,
            account.get("refreshed", {}).get("transactions") if isinstance(account.get("refreshed"), dict) else None,
            json.dumps(account, sort_keys=True),
            now,
        ),
    )
    con.execute(
        """
        INSERT INTO balance_snapshots (
            account_id, current_cents, available_cents, currency, captured_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (account_id(account), balance_current, balance_available, currency, now),
    )


def _upsert_transaction(con, account: dict, transaction: dict) -> tuple[bool, bool]:
    tx_id = transaction.get("_id") or transaction.get("id")
    if not tx_id:
        raise ValueError("Akahu transaction is missing _id")
    description = _description(transaction)
    merchant_id, merchant_name = _merchant(transaction)
    category_id, category_name = _category(transaction)
    merchant_key = _source_merchant_key(merchant_name, description)
    amount_cents = to_cents(transaction.get("amount"))
    if amount_cents is None:
        raise ValueError(f"Transaction {tx_id} has an invalid amount")
    balance_cents = to_cents(transaction.get("balance"))
    effective_merchant, effective_category = _effective_fields(
        con, merchant_key, tx_id, merchant_name, category_name
    )
    effective_category = _apply_transfer_category(effective_category, transaction, category_name)
    if _is_useful_category(category_name):
        ensure_category(con, category_name)
    if effective_category:
        ensure_category(con, effective_category, is_system=effective_category == TRANSFER_CATEGORY)
    existing = con.execute(
        "SELECT akahu_category_name, review_status, review_reason FROM transactions WHERE id = ?",
        (tx_id,),
    ).fetchone()
    category_changed = bool(
        existing
        and existing["akahu_category_name"]
        and category_name
        and existing["akahu_category_name"] != category_name
    )
    review_status, review_reason = _review_state(
        existing, category_name, effective_category, category_changed
    )
    now = utc_now()
    con.execute(
        """
        INSERT INTO transactions (
            id, account_id, date, description, amount_cents, balance_cents, type,
            pending, akahu_merchant_id, akahu_merchant_name, akahu_category_id,
            akahu_category_name, merchant_key, effective_merchant, effective_category,
            review_status, review_reason, raw_json, first_seen_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            account_id = excluded.account_id,
            date = excluded.date,
            description = excluded.description,
            amount_cents = excluded.amount_cents,
            balance_cents = excluded.balance_cents,
            type = excluded.type,
            pending = excluded.pending,
            akahu_merchant_id = excluded.akahu_merchant_id,
            akahu_merchant_name = excluded.akahu_merchant_name,
            akahu_category_id = excluded.akahu_category_id,
            akahu_category_name = excluded.akahu_category_name,
            merchant_key = excluded.merchant_key,
            effective_merchant = excluded.effective_merchant,
            effective_category = excluded.effective_category,
            review_status = excluded.review_status,
            review_reason = excluded.review_reason,
            raw_json = excluded.raw_json,
            updated_at = excluded.updated_at
        """,
        (
            tx_id,
            account_id(account),
            _transaction_date(transaction),
            description,
            amount_cents,
            balance_cents,
            transaction.get("type"),
            1 if transaction.get("pending") else 0,
            merchant_id,
            merchant_name,
            category_id,
            category_name,
            merchant_key,
            effective_merchant,
            effective_category,
            review_status,
            review_reason,
            json.dumps(transaction, sort_keys=True),
            now,
            now,
        ),
    )
    if existing is not None:
        con.execute(
            "UPDATE transactions SET first_seen_at = COALESCE(first_seen_at, ?) WHERE id = ?",
            (now, tx_id),
        )
    return existing is None, existing is not None


def sync_akahu(
    database_path: str,
    client: AkahuClient,
    *,
    allowed_connections: list[str],
    since_days: Optional[int],
) -> SyncResult:
    started_at = utc_now()
    with connection(database_path) as con:
        cursor = con.execute(
            "INSERT INTO sync_runs (started_at, status) VALUES (?, 'running')",
            (started_at,),
        )
        run_id = cursor.lastrowid

    accounts_seen = transactions_seen = new_transactions = updated_transactions = 0
    try:
        accounts = client.accounts()
        with connection(database_path) as con:
            for account in accounts:
                validate_account_is_safe(account, allowed_connections)
                if not _allowed_account(account, allowed_connections):
                    continue
                _upsert_account(con, account)
                accounts_seen += 1
                if not _has_transactions(account):
                    continue
                transactions = client.transactions(account_id(account), since_days=since_days)
                for transaction in transactions:
                    created, updated = _upsert_transaction(con, account, transaction)
                    transactions_seen += 1
                    new_transactions += 1 if created else 0
                    updated_transactions += 1 if updated else 0
            updated_transactions += mark_transfers(con)
            con.execute(
                """
                UPDATE sync_runs
                SET finished_at = ?, status = 'success', accounts_seen = ?,
                    transactions_seen = ?, new_transactions = ?, updated_transactions = ?
                WHERE id = ?
                """,
                (utc_now(), accounts_seen, transactions_seen, new_transactions, updated_transactions, run_id),
            )
        return SyncResult("success", accounts_seen, transactions_seen, new_transactions, updated_transactions)
    except Exception as exc:
        with connection(database_path) as con:
            con.execute(
                "UPDATE sync_runs SET finished_at = ?, status = 'error', error = ? WHERE id = ?",
                (utc_now(), str(exc), run_id),
            )
        return SyncResult("error", accounts_seen, transactions_seen, new_transactions, updated_transactions, str(exc))


def detect_transfers(database_path: str) -> int:
    with connection(database_path) as con:
        changed = mark_transfers(con)
        return changed


def mark_transfers(con) -> int:
    now = utc_now()
    direct = con.execute(
        """
        UPDATE transactions
        SET effective_category = ?,
            review_status = 'auto',
            review_reason = NULL,
            updated_at = ?
        WHERE (
                UPPER(COALESCE(type, '')) = 'TRANSFER'
                OR LOWER(COALESCE(akahu_category_name, '')) IN ('transfer', 'transfers', 'account transfer')
              )
          AND review_status != 'reviewed'
          AND (
              COALESCE(effective_category, '') != ?
              OR review_status != 'auto'
              OR review_reason IS NOT NULL
          )
          AND NOT EXISTS (
              SELECT 1 FROM transaction_overrides o WHERE o.transaction_id = transactions.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM merchant_overrides o WHERE o.merchant_key = transactions.merchant_key
          )
        """,
        (TRANSFER_CATEGORY, now, TRANSFER_CATEGORY),
    ).rowcount

    rows = con.execute(
        """
        SELECT id, account_id, date, amount_cents, type
        FROM transactions
        WHERE pending = 0
          AND amount_cents != 0
          AND COALESCE(effective_category, '') != ?
          AND review_status != 'reviewed'
          AND UPPER(COALESCE(type, '')) IN ('TRANSFER', 'STANDING ORDER', 'PAYMENT', 'DIRECT CREDIT', 'DEBIT', 'CREDIT')
          AND NOT EXISTS (
              SELECT 1 FROM transaction_overrides o WHERE o.transaction_id = transactions.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM merchant_overrides o WHERE o.merchant_key = transactions.merchant_key
          )
        ORDER BY date, ABS(amount_cents), id
        """,
        (TRANSFER_CATEGORY,),
    ).fetchall()
    used = set()
    paired = 0
    for left in rows:
        if left["id"] in used:
            continue
        left_date = datetime.fromisoformat(left["date"])
        for right in rows:
            if right["id"] in used or right["id"] == left["id"]:
                continue
            if right["account_id"] == left["account_id"]:
                continue
            if right["amount_cents"] != -left["amount_cents"]:
                continue
            right_date = datetime.fromisoformat(right["date"])
            if abs((right_date - left_date).days) > 3:
                continue
            result = con.execute(
                """
                UPDATE transactions
                SET effective_category = ?,
                    review_status = 'auto',
                    review_reason = NULL,
                    updated_at = ?
                WHERE id IN (?, ?)
                  AND review_status != 'reviewed'
                  AND NOT EXISTS (
                      SELECT 1 FROM transaction_overrides o WHERE o.transaction_id = transactions.id
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM merchant_overrides o WHERE o.merchant_key = transactions.merchant_key
                  )
                """,
                (TRANSFER_CATEGORY, utc_now(), left["id"], right["id"]),
            )
            used.add(left["id"])
            used.add(right["id"])
            paired += result.rowcount
            break
    return direct + paired


def apply_merchant_override(
    database_path: str,
    *,
    merchant_key: str,
    display_merchant: Optional[str],
    category_name: Optional[str],
    category_id: Optional[str] = None,
) -> int:
    merchant_key = key(merchant_key)
    with connection(database_path) as con:
        con.execute(
            """
            INSERT INTO merchant_overrides (
                merchant_key, display_merchant, category_name, category_id, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(merchant_key) DO UPDATE SET
                display_merchant = excluded.display_merchant,
                category_name = excluded.category_name,
                category_id = excluded.category_id,
                updated_at = excluded.updated_at
            """,
            (merchant_key, compact(display_merchant), compact(category_name), category_id, utc_now()),
        )
        rows = con.execute(
            """
            UPDATE transactions
            SET effective_merchant = COALESCE(NULLIF(?, ''), effective_merchant),
                effective_category = COALESCE(NULLIF(?, ''), effective_category),
                review_status = 'reviewed',
                review_reason = NULL,
                updated_at = ?
            WHERE merchant_key = ?
            """,
            (compact(display_merchant), compact(category_name), utc_now(), merchant_key),
        )
        return rows.rowcount


def apply_transaction_override(
    database_path: str,
    *,
    transaction_id: str,
    display_merchant: Optional[str],
    category_name: Optional[str],
    category_id: Optional[str] = None,
) -> None:
    with connection(database_path) as con:
        con.execute(
            """
            INSERT INTO transaction_overrides (
                transaction_id, display_merchant, category_name, category_id, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(transaction_id) DO UPDATE SET
                display_merchant = excluded.display_merchant,
                category_name = excluded.category_name,
                category_id = excluded.category_id,
                updated_at = excluded.updated_at
            """,
            (transaction_id, compact(display_merchant), compact(category_name), category_id, utc_now()),
        )
        con.execute(
            """
            UPDATE transactions
            SET effective_merchant = COALESCE(NULLIF(?, ''), effective_merchant),
                effective_category = COALESCE(NULLIF(?, ''), effective_category),
                review_status = 'reviewed',
                review_reason = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (compact(display_merchant), compact(category_name), utc_now(), transaction_id),
        )
