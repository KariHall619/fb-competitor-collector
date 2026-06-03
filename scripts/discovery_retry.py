#!/usr/bin/env python3
"""Shared homepage discovery retry helpers."""

from __future__ import annotations

from typing import Any


def needs_snapshot_budget_retry(payload: dict[str, Any] | None) -> bool:
    """Return True when OpenCLI stopped at the snapshot cap while still finding posts."""

    if not isinstance(payload, dict):
        return False
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    if coverage.get("expected_coverage_failed"):
        return False
    coverage_incomplete = bool(payload.get("coverage_incomplete") or coverage.get("coverage_incomplete"))
    capture_complete = payload.get("capture_complete")
    coverage_complete = coverage.get("capture_complete")
    if not coverage_incomplete and capture_complete is not False and coverage_complete is not False:
        return False
    return str(coverage.get("stop_reason") or payload.get("stop_reason") or "") == "max_snapshots"


def retry_snapshot_budget(current_max: int, *, minimum: int = 32, increment: int = 12, multiplier: float = 1.5) -> int:
    """Compute the next homepage snapshot budget without lowering the current one."""

    current = max(0, int(current_max or 0))
    raised_by_increment = current + max(1, int(increment or 1))
    raised_by_multiplier = int(current * max(1.0, float(multiplier or 1.0)))
    return max(current + 1, minimum, raised_by_increment, raised_by_multiplier)


def attach_auto_retry_report(
    final_payload: dict[str, Any],
    *,
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Annotate the final discovery payload with bounded auto-retry metadata."""

    if not attempts:
        return final_payload
    next_payload = dict(final_payload)
    coverage = dict(next_payload.get("coverage") or {})
    coverage["auto_retry"] = {
        "attempted": True,
        "attempt_count": len(attempts),
        "attempts": attempts,
        "resolved": not bool(next_payload.get("coverage_incomplete") or coverage.get("coverage_incomplete")),
    }
    next_payload["coverage"] = coverage
    return next_payload
