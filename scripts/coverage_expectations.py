"""Shared expected-coverage checks for Facebook homepage discovery payloads."""

from __future__ import annotations

import re
from typing import Any


DEFAULT_EXPECTED_LABEL_LIMIT = 50


RELATIVE_LABEL_RE = re.compile(
    r"^(\d+)\s*(m|min|mins|minute|minutes|分钟|h|hr|hrs|hour|hours|小时|d|day|days|天|w|wk|wks|week|weeks|周)(?:\s*ago)?$",
    re.I,
)


def normalize_relative_label(value: Any) -> str:
    """Normalize common Facebook relative-time label variants for coverage matching."""

    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = text.replace("，", ",")
    text = re.sub(r"\s+", " ", text)
    if text in {"just now", "刚刚"}:
        return "just_now"
    if text in {"yesterday", "昨天"}:
        return "1d"
    compact = text.replace(" ", "")
    match = RELATIVE_LABEL_RE.match(text) or RELATIVE_LABEL_RE.match(compact)
    if not match:
        return compact
    amount = str(int(match.group(1)))
    unit = match.group(2).lower()
    if unit in {"m", "min", "mins", "minute", "minutes", "分钟"}:
        suffix = "m"
    elif unit in {"h", "hr", "hrs", "hour", "hours", "小时"}:
        suffix = "h"
    elif unit in {"d", "day", "days", "天"}:
        suffix = "d"
    else:
        suffix = "w"
    return f"{amount}{suffix}"


def split_expected_labels(value: str) -> list[str]:
    labels: list[str] = []
    for raw in value.replace("，", ",").replace("\n", ",").split(","):
        label = raw.strip()
        if label and label not in labels:
            labels.append(label)
    return labels


def captured_labels(discover_payload: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    snapshots = discover_payload.get("snapshots") if isinstance(discover_payload, dict) else []
    if isinstance(snapshots, list):
        for snapshot in snapshots:
            if not isinstance(snapshot, dict):
                continue
            for label in snapshot.get("visible_time_texts") or []:
                text = str(label or "").strip()
                if text and text not in labels:
                    labels.append(text)
                    if len(labels) >= DEFAULT_EXPECTED_LABEL_LIMIT:
                        return labels
    posts = discover_payload.get("posts") if isinstance(discover_payload, dict) else []
    if isinstance(posts, list):
        for post in posts:
            if not isinstance(post, dict):
                continue
            for label in [post.get("relative_time_text"), post.get("post_time_text")]:
                text = str(label or "").strip()
                if text and text not in labels:
                    labels.append(text)
                    if len(labels) >= DEFAULT_EXPECTED_LABEL_LIMIT:
                        return labels
    return labels


def expected_coverage_check(
    discover_payload: dict[str, Any],
    *,
    expected_post_count: int = 0,
    expected_labels: list[str] | None = None,
) -> dict[str, Any]:
    expected_labels = expected_labels or []
    captured_count = int(discover_payload.get("post_count") or 0)
    observed_labels = captured_labels(discover_payload)
    observed_label_keys = {
        normalized
        for label in observed_labels
        if (normalized := normalize_relative_label(label))
    }
    matched_labels = [label for label in expected_labels if normalize_relative_label(label) in observed_label_keys]
    missing_labels = [label for label in expected_labels if normalize_relative_label(label) not in observed_label_keys]
    count_missing = max(0, int(expected_post_count or 0) - captured_count)
    ok = count_missing == 0 and not missing_labels
    expected_label_count = len(expected_labels)
    messages: list[str] = []
    if count_missing:
        messages.append(f"期望至少 {expected_post_count} 条，当前只抓到 {captured_count} 条。")
    if missing_labels:
        messages.append("缺少人工可见时间标签：" + "、".join(missing_labels[:20]))
    return {
        "enabled": bool(expected_post_count or expected_labels),
        "ok": ok,
        "expected_post_count": int(expected_post_count or 0),
        "captured_post_count": captured_count,
        "missing_post_count": count_missing,
        "post_count_coverage_rate": round(min(captured_count, int(expected_post_count or 0)) / int(expected_post_count or 1), 4)
        if expected_post_count
        else 0.0,
        "expected_labels": expected_labels,
        "expected_label_count": expected_label_count,
        "captured_labels": observed_labels[:DEFAULT_EXPECTED_LABEL_LIMIT],
        "matched_labels": matched_labels,
        "matched_label_count": len(matched_labels),
        "missing_labels": missing_labels,
        "label_coverage_rate": round(len(matched_labels) / expected_label_count, 4) if expected_label_count else 0.0,
        "message": "；".join(messages),
    }


def apply_expected_coverage(
    discover_payload: dict[str, Any],
    *,
    expected_post_count: int = 0,
    expected_labels: list[str] | None = None,
) -> dict[str, Any]:
    check = expected_coverage_check(
        discover_payload,
        expected_post_count=expected_post_count,
        expected_labels=expected_labels,
    )
    if not check["enabled"]:
        return discover_payload
    next_payload = dict(discover_payload)
    coverage = dict(next_payload.get("coverage") or {})
    coverage["expected"] = check
    if not check["ok"]:
        coverage["coverage_incomplete"] = True
        coverage["capture_complete"] = False
        coverage["expected_coverage_failed"] = True
        coverage["message"] = check["message"] or coverage.get("message") or "人工期望覆盖未满足。"
        next_payload["coverage_incomplete"] = True
        next_payload["capture_complete"] = False
    next_payload["coverage"] = coverage
    return next_payload
