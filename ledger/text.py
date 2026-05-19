from __future__ import annotations

import re
from typing import Optional


_NON_WORD = re.compile(r"[^a-z0-9]+")


def compact(value: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def key(value: Optional[str]) -> str:
    normalized = _NON_WORD.sub(" ", (value or "").lower())
    return compact(normalized)
