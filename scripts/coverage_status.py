#!/usr/bin/env python3
"""Coverage status helpers for capture ledger output."""

from __future__ import annotations

from typing import Any


COVERAGE_INCOMPLETE_REASONS = {"coverage_blocked", "coverage_incomplete"}


def coverage_reasons_from_payload(payload: dict[str, Any]) -> list[str]:
    coverage = payload.get("coverage") if isinstance(payload, dict) else {}
    if not isinstance(coverage, dict):
        coverage = {}
    reasons: list[str] = []
    if payload.get("coverage_blocked") or coverage.get("coverage_blocked"):
        reasons.append("coverage_blocked")
    if payload.get("coverage_incomplete") or coverage.get("coverage_incomplete"):
        reasons.append("coverage_incomplete")
    return reasons


def coverage_note_from_payload(payload: dict[str, Any]) -> str:
    reasons = coverage_reasons_from_payload(payload)
    if not reasons:
        return ""
    coverage = payload.get("coverage") if isinstance(payload, dict) else {}
    message = coverage.get("message") if isinstance(coverage, dict) else ""
    if message:
        return str(message)
    if "coverage_incomplete" in reasons:
        return "采集达到快照上限时仍有新增候选，本次覆盖不完整，需从主页顶部继续补抓。"
    return "连续滚动后未发现新增候选，但人工可能仍能看到更多目标窗口帖子，本次覆盖不完整，需补抓。"

