#!/usr/bin/env python3
"""Configurable field audit rules for Feishu preview-and-refetch output."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from config_loader import deep_get
from story_summary_policy import has_valid_story_summary
from value_utils import parse_bool


DEFAULT_REQUIRED_ENGAGEMENT_FIELDS = ["likes", "comments", "shares"]
DEFAULT_REQUIRED_POST_TYPES = ["图文", "视频", "仅图片", "仅文字"]
SYSTEM_MARKER_PREFIX = "待补抓："
QUALIFIED_LEAD_SOURCES = {"comment", "comment_reply", "post_cta"}
ESTIMATED_TIME_SOURCES = {"relative_hour", "relative_estimated", "relative_label"}
COVERAGE_INCOMPLETE_REASONS = {"coverage_blocked", "coverage_incomplete"}
FACEBOOK_INTERNAL_HOSTS = {
    "facebook.com",
    "m.facebook.com",
    "mbasic.facebook.com",
    "www.facebook.com",
    "fb.watch",
    "meta.com",
    "www.meta.com",
}

REASON_LABELS = {
    "exact_time": "精确时间",
    "lead_link": "引流链接",
    "article_summary": "文章概要",
    "likes": "点赞数",
    "comments": "评论数",
    "shares": "分享数",
    "likes_low": "点赞数异常低",
    "post_type": "帖子类型",
    "coverage": "覆盖不足",
}

REASON_STAGES = {
    "exact_time": "detail_time",
    "lead_link": "lead_link",
    "article_summary": "summary",
    "likes": "engagement",
    "comments": "engagement",
    "shares": "engagement",
    "likes_low": "engagement",
    "post_type": "post_type",
}


def audit_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or {}
    required_engagement = deep_get(config, "quality_audit.required_engagement_fields", DEFAULT_REQUIRED_ENGAGEMENT_FIELDS)
    if not isinstance(required_engagement, list) or not required_engagement:
        required_engagement = list(DEFAULT_REQUIRED_ENGAGEMENT_FIELDS)
    required_post_types = deep_get(config, "quality_audit.required_post_types", DEFAULT_REQUIRED_POST_TYPES)
    if not isinstance(required_post_types, list) or not required_post_types:
        required_post_types = list(DEFAULT_REQUIRED_POST_TYPES)
    return {
        "required_engagement_fields": [str(item) for item in required_engagement],
        "low_like_threshold": int(deep_get(config, "quality_audit.low_like_threshold", 5)),
        "required_post_types": [str(item) for item in required_post_types],
        "assume_lead_link_exists": bool(deep_get(config, "quality_audit.assume_lead_link_exists", True)),
        "missing_marker_column": str(deep_get(config, "quality_audit.missing_marker_column", "是否采用") or "是否采用"),
        "update_existing_rows_by": str(deep_get(config, "quality_audit.update_existing_rows_by", "post_url") or "post_url"),
    }


def parse_reasons(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if not value:
        return []
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        return [item.strip() for item in text.split(",") if item.strip()]
    return parse_reasons(parsed)


def has_qualified_audit_lead(post: dict[str, Any]) -> bool:
    landing_url = post.get("landing_url") or post.get("article_url")
    return bool(
        post.get("lead_link_status") == "qualified"
        and post.get("lead_link_source") in QUALIFIED_LEAD_SOURCES
        and landing_url
        and is_external_landing_url(landing_url)
    )


def is_external_landing_url(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    parsed = urlparse(text)
    if parsed.netloc == "l.facebook.com":
        qs = parse_qs(parsed.query)
        if qs.get("u"):
            return is_external_landing_url(unquote(qs["u"][0]))
    host = parsed.netloc.lower().removeprefix("www.")
    if parsed.scheme not in {"http", "https"} or not host:
        return False
    return host not in FACEBOOK_INTERNAL_HOSTS and not host.endswith(".facebook.com") and not host.endswith(".meta.com")


def _missing_number(post: dict[str, Any], field: str) -> bool:
    value = post.get(field)
    return value is None or value == ""


def audit_post_fields(post: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = audit_config(config)
    reasons: list[str] = []
    if (
        not post.get("posted_at")
        or not parse_bool(post.get("time_confirmed"))
        or str(post.get("time_source") or "") in ESTIMATED_TIME_SOURCES
    ):
        reasons.append("exact_time")
    if cfg["assume_lead_link_exists"] and not has_qualified_audit_lead(post):
        reasons.append("lead_link")
    if not has_valid_story_summary(post):
        reasons.append("article_summary")
    for field in cfg["required_engagement_fields"]:
        if _missing_number(post, field):
            reasons.append(field)
    likes = post.get("likes")
    try:
        likes_number = int(likes) if likes is not None and likes != "" else None
    except (TypeError, ValueError):
        likes_number = None
    if likes_number is not None and likes_number <= cfg["low_like_threshold"]:
        reasons.append("likes_low")
    if post.get("post_type") not in set(cfg["required_post_types"]):
        reasons.append("post_type")
    if str(post.get("coverage_note") or "").strip() or any(
        str(post.get(field) or "").strip() in COVERAGE_INCOMPLETE_REASONS
        for field in ("coverage_status", "coverage_reason")
    ):
        reasons.append("coverage")

    ordered: list[str] = []
    for reason in reasons:
        if reason not in ordered:
            ordered.append(reason)
    return {
        "field_audit_status": "needs_refetch" if ordered else "passed",
        "field_audit_reasons": ordered,
        "field_audit_note": audit_marker_for_reasons(ordered),
        "refetch_stages": refetch_stages_for_reasons(ordered),
    }


def audit_marker_for_reasons(reasons: list[str]) -> str:
    labels = [REASON_LABELS.get(reason, reason) for reason in reasons]
    return f"{SYSTEM_MARKER_PREFIX}{'、'.join(labels)}" if labels else ""


def audit_reason_counts(posts: list[dict[str, Any]], config: dict[str, Any] | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for post in posts:
        reasons = parse_reasons(post.get("field_audit_reasons"))
        if config is not None or not reasons:
            reasons = audit_post_fields(post, config).get("field_audit_reasons", [])
        for reason in reasons:
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def audit_reason_summary(posts: list[dict[str, Any]], config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for reason, count in audit_reason_counts(posts, config).items():
        summary.append(
            {
                "reason": reason,
                "label": REASON_LABELS.get(reason, reason),
                "count": count,
                "stage": REASON_STAGES.get(reason, "coverage" if reason == "coverage" else ""),
            }
        )
    return summary


def audit_reason_notes(posts: list[dict[str, Any]], config: dict[str, Any] | None = None, *, limit: int = 5) -> list[str]:
    return [f"{item['label']}：{item['count']} 条" for item in audit_reason_summary(posts, config)[:limit]]


def is_system_audit_marker(value: Any) -> bool:
    return str(value or "").strip().startswith(SYSTEM_MARKER_PREFIX)


def adoption_status_for_output(post: dict[str, Any], config: dict[str, Any] | None = None) -> str:
    existing = str(post.get("adoption_status") or "").strip()
    if existing and not is_system_audit_marker(existing):
        return existing
    if config is not None:
        return audit_post_fields(post, config).get("field_audit_note", "")
    return audit_marker_for_reasons(parse_reasons(post.get("field_audit_reasons")))


def audit_fields_for_storage(post: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    result = audit_post_fields(post, config)
    return {
        "field_audit_status": result["field_audit_status"],
        "field_audit_reasons": json.dumps(result["field_audit_reasons"], ensure_ascii=False),
        "field_audit_note": result["field_audit_note"],
    }


def refetch_stages_for_reasons(reasons: list[str]) -> list[str]:
    stages: list[str] = []
    for reason in reasons:
        stage = REASON_STAGES.get(reason)
        if stage and stage not in stages:
            stages.append(stage)
    return stages


def audit_refetch_stages(post: dict[str, Any], config: dict[str, Any] | None = None) -> list[str]:
    return audit_post_fields(post, config)["refetch_stages"]
