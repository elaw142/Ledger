from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional


def to_cents(value) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def cents_to_display(cents: Optional[int], currency: str = "NZD") -> str:
    if cents is None:
        return "-"
    sign = "-" if cents < 0 else ""
    value = abs(cents)
    dollars = value // 100
    remainder = value % 100
    prefix = "$" if currency.upper() in {"NZD", "AUD", "USD"} else f"{currency} "
    return f"{sign}{prefix}{dollars:,}.{remainder:02d}"
