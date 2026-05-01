from __future__ import annotations

from typing import Any


def value_is_true(value: Any) -> dict[str, bool]:
    if isinstance(value, bool):
        return {"ok": value}
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return {"ok": True}
        if normalized == "false":
            return {"ok": False}
    if isinstance(value, (int, float)):
        if value == 1:
            return {"ok": True}
        if value == 0:
            return {"ok": False}
    raise ValueError(f"value_is_true expected a boolean-like value, got {value!r}")
