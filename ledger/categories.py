from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from .text import compact, key


TRANSFER_CATEGORY = "Transfers"
UNCATEGORIZED_CATEGORY = "Uncategorized"
SYSTEM_CATEGORIES = {TRANSFER_CATEGORY, UNCATEGORIZED_CATEGORY}

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_PALETTE = [
    "#1A6840",
    "#8B3A2A",
    "#3A5F8B",
    "#7A5C1E",
    "#5A3A7A",
    "#4A7C5F",
    "#7C4A35",
    "#5F6F52",
    "#6E5748",
    "#8A6B2F",
]


def clean_category_name(value: Optional[str]) -> str:
    return compact(value)


def validate_category_name(value: Optional[str]) -> str:
    name = clean_category_name(value)
    if not name:
        raise ValueError("Category name is required")
    if len(name) > 64:
        raise ValueError("Category name must be 64 characters or fewer")
    return name


def clean_color(value: Optional[str], fallback_name: str) -> str:
    color = compact(value)
    if color and _HEX_COLOR.match(color):
        return color.upper()
    return color_for_name(fallback_name)


def color_for_name(name: str) -> str:
    category_key = key(name)
    index = sum(ord(char) for char in category_key) % len(_PALETTE)
    return _PALETTE[index]


def is_system_category(name: Optional[str]) -> bool:
    category_key = key(name)
    return any(category_key == key(system_name) for system_name in SYSTEM_CATEGORIES)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_category(con, name: Optional[str], *, is_system: bool = False, color: Optional[str] = None) -> Optional[str]:
    category_name = clean_category_name(name)
    if not category_name:
        return None
    system = 1 if is_system or is_system_category(category_name) else 0
    now = utc_now()
    existing = con.execute(
        "SELECT name, is_system FROM categories WHERE name = ? COLLATE NOCASE",
        (category_name,),
    ).fetchone()
    if existing:
        if system and not existing["is_system"]:
            con.execute(
                "UPDATE categories SET is_system = 1, updated_at = ? WHERE name = ? COLLATE NOCASE",
                (now, category_name),
            )
        return existing["name"]
    row = con.execute("SELECT COALESCE(MAX(sort_order), 0) AS max_sort FROM categories").fetchone()
    sort_order = int(row["max_sort"] or 0) + 10
    con.execute(
        """
        INSERT INTO categories (name, color, sort_order, is_system, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (category_name, clean_color(color, category_name), sort_order, system, now, now),
    )
    return category_name


def seed_categories(con) -> None:
    for system_name in (UNCATEGORIZED_CATEGORY, TRANSFER_CATEGORY):
        ensure_category(con, system_name, is_system=True)

    rows = con.execute(
        """
        SELECT effective_category AS name FROM transactions
        WHERE COALESCE(effective_category, '') != ''
        UNION
        SELECT akahu_category_name AS name FROM transactions
        WHERE COALESCE(akahu_category_name, '') != ''
        UNION
        SELECT category_name AS name FROM merchant_overrides
        WHERE COALESCE(category_name, '') != ''
        UNION
        SELECT category_name AS name FROM transaction_overrides
        WHERE COALESCE(category_name, '') != ''
        """
    ).fetchall()
    for row in rows:
        ensure_category(con, row["name"], is_system=is_system_category(row["name"]))
