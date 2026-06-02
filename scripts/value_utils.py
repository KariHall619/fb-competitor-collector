#!/usr/bin/env python3
"""Small shared value parsing helpers without project-module dependencies."""

from __future__ import annotations

from typing import Any


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if value in (None, ""):
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on", "confirmed"}
