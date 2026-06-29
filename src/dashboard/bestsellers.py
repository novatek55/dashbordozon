from __future__ import annotations

from typing import Any, Optional


def _to_number(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(str(value).replace(" ", "").replace(",", "."))
        except (TypeError, ValueError):
            return None


def normalize_bestsellers_percent(value: Any) -> Optional[float]:
    """Return bestsellers percentage metrics as a fraction in the 0..1 range."""
    num = _to_number(value)
    if num is None:
        return None
    return num / 100.0 if abs(num) > 1 else num
