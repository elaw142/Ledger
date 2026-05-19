from __future__ import annotations

import re


_NON_WORD = re.compile(r"[^a-z0-9]+")


def compact(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def key(value: str | None) -> str:
    normalized = _NON_WORD.sub(" ", (value or "").lower())
    return compact(normalized)

