#!/usr/bin/env python3
"""Feishu field standards and header alias mapping."""

from __future__ import annotations

import re
from typing import Any

from config_loader import deep_get


ESTIMATED_TIME_SOURCES = {"relative_hour", "relative_estimated", "relative_label"}


DEFAULT_OUTPUT_HEADERS = [
    "账号",
    "账户类型",
    "帖子链接",
    "帖子类型",
    "发帖时间",
    "文章链接",
    "故事概要",
    "互动数据（点赞量）",
    "浏览量",
    "是否采用",
    "对应站内链接",
]

HEADER_ALIASES: dict[str, set[str]] = {
    "account_name": {"账号", "账户", "账号名称", "账户名称", "主页名称", "FB账号", "Facebook账号"},
    "account_url": {"账号主页链接", "主页链接", "FB账户", "Facebook账户", "fb账户"},
    "account_type": {"账户类型", "账号类型", "主页类型", "类型"},
    "post_url": {"帖子链接", "Facebook帖子链接", "FB帖子链接", "FB内容链接", "内容链接"},
    "raw_fb_url": {"原始FB链接", "原始内容链接", "原始帖子链接"},
    "parent_post_url": {"父帖链接", "父帖子链接", "canonical帖子链接"},
    "fb_link_kind": {"FB链接类型", "内容链接类型"},
    "post_type": {"帖子类型", "内容类型"},
    "posted_at": {"发帖时间", "精确发帖时间", "发布时间", "发帖时间精确值"},
    "posted_date": {"发帖日期", "日期"},
    "landing_url": {"文章链接", "落地链接", "落地页链接", "最终落地URL", "引流落地链接"},
    "lead_url_raw": {"评论引流链接", "评论区引流链接", "评论回复引流链接", "原始引流链接"},
    "lead_link_source": {"引流链接位置", "引流来源", "引流链接来源"},
    "lead_link_status": {"引流确认状态", "引流状态"},
    "story_summary": {"故事概要", "摘要", "文章摘要", "内容摘要", "简述"},
    "engagement": {
        "互动数据",
        "互动数据（点赞量）",
        "互动数据(点赞量)",
        "互动数据（浏览量、点赞量）",
        "互动数据(浏览量、点赞量)",
        "互动数据汇总",
    },
    "likes": {"点赞量", "点赞数", "反应数"},
    "views": {"浏览量", "播放量"},
    "comments": {"评论数", "评论量"},
    "shares": {"分享数", "分享量"},
    "adoption_status": {"是否采用", "采用状态"},
    "internal_link": {"对应站内链接", "站内链接"},
    "note": {"备注", "说明", "异常说明"},
    "crawl_status": {"采集状态"},
    "output_status": {"输出状态"},
    "crawled_at": {"采集时间"},
    "source_skill": {"来源", "采集来源"},
    "coverage_note": {"覆盖说明", "抓取覆盖说明"},
}

COMPETITOR_ACCOUNT_HEADERS = {
    "竞品fb账户",
    "竞品FB账户",
    "竞品账户",
    "竞品账号",
    "竞品主页",
    "竞品Facebook账户",
}
INTERNAL_ACCOUNT_HEADERS = {
    "内部FB账户",
    "内部fb账户",
    "内部账户",
    "内部账号",
    "内部主页",
    "内部Facebook账户",
}
GENERIC_ACCOUNT_HEADERS = {"fb账户", "FB账户", "Facebook账户", "账号主页链接", "主页链接"}
ACCOUNT_TYPE_HEADERS = {"账户类型", "账号类型", "主页类型", "类型"}
ACCOUNT_NAME_HEADERS = {"主页名称", "账号名称", "账户名称", "账号"}


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            if isinstance(item, dict):
                chunks.append(str(item.get("link") or item.get("text") or ""))
            else:
                chunks.append(cell_text(item))
        return "".join(chunks).strip()
    if isinstance(value, dict):
        return str(value.get("link") or value.get("text") or "").strip()
    return str(value).strip()


def normalize_header(value: Any) -> str:
    text = cell_text(value).lower()
    text = re.sub(r"[\s_()（）:：,，、/\\-]+", "", text)
    return text


def _alias_lookup(groups: dict[str, set[str]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for field, aliases in groups.items():
        for alias in aliases | {field}:
            lookup[normalize_header(alias)] = field
    return lookup


OUTPUT_HEADER_LOOKUP = _alias_lookup(HEADER_ALIASES)
COMPETITOR_ACCOUNT_LOOKUP = {normalize_header(item) for item in COMPETITOR_ACCOUNT_HEADERS}
INTERNAL_ACCOUNT_LOOKUP = {normalize_header(item) for item in INTERNAL_ACCOUNT_HEADERS}
GENERIC_ACCOUNT_LOOKUP = {normalize_header(item) for item in GENERIC_ACCOUNT_HEADERS}
ACCOUNT_TYPE_LOOKUP = {normalize_header(item) for item in ACCOUNT_TYPE_HEADERS}
ACCOUNT_NAME_LOOKUP = {normalize_header(item) for item in ACCOUNT_NAME_HEADERS}


def output_field_for_header(header: Any) -> str:
    return OUTPUT_HEADER_LOOKUP.get(normalize_header(header), "")


def configured_output_headers(config: dict[str, Any]) -> list[str]:
    headers = deep_get(config, "feishu.field_schema.output_headers", [])
    if isinstance(headers, list):
        cleaned = [cell_text(item) for item in headers if cell_text(item)]
        if cleaned:
            return cleaned
    return list(DEFAULT_OUTPUT_HEADERS)


def current_or_configured_output_headers(config: dict[str, Any], current_headers: list[str] | None) -> list[str]:
    cleaned = [cell_text(item) for item in (current_headers or []) if cell_text(item)]
    return cleaned or configured_output_headers(config)


def engagement_text(post: dict[str, Any], *, include_views: bool = True) -> str:
    parts = []
    if include_views and post.get("views") is not None:
        parts.append(f"浏览量：{post.get('views')}")
    if post.get("likes") is not None:
        parts.append(f"点赞量：{post.get('likes')}")
    if post.get("comments") is not None:
        parts.append(f"评论数：{post.get('comments')}")
    if post.get("shares") is not None:
        parts.append(f"分享数：{post.get('shares')}")
    return "；".join(parts) or post.get("engagement_raw") or post.get("engagement_data") or ""


def output_account_type(value: Any) -> str:
    text = str(value or "").strip()
    if text == "competitor":
        return "竞品"
    if text == "internal":
        return "内部"
    return text


def output_value(post: dict[str, Any], field: str) -> Any:
    if field == "account_name":
        return post.get("account_name") or post.get("account_url") or ""
    if field == "account_type":
        return output_account_type(post.get("account_type"))
    if field == "post_url":
        return post.get("post_url") or post.get("raw_fb_url") or ""
    if field == "posted_at":
        posted_at = post.get("posted_at") or post.get("posted_date") or ""
        if posted_at and str(post.get("time_source") or "") in ESTIMATED_TIME_SOURCES:
            return f"约{posted_at}"
        return posted_at
    if field == "landing_url":
        return post.get("landing_url") or post.get("article_url") or ""
    if field == "engagement":
        current_header = cell_text(post.get("_current_output_header", ""))
        include_views = not current_header or "浏览量" in current_header
        return engagement_text(post, include_views=include_views)
    if field == "adoption_status":
        return post.get("adoption_status", "")
    if field == "internal_link":
        return post.get("internal_link", "")
    if field in {"views", "likes", "comments", "shares"}:
        value = post.get(field)
        return value if value is not None else ""
    return post.get(field, "")


def output_row_for_headers(post: dict[str, Any], headers: list[str]) -> list[Any]:
    row: list[Any] = []
    for header in headers:
        field = output_field_for_header(header)
        if field == "engagement":
            value_post = {**post, "_current_output_header": header}
            row.append(output_value(value_post, field))
        else:
            row.append(output_value(post, field) if field else "")
    return row


def account_column_roles(headers: list[Any]) -> dict[int, str]:
    roles: dict[int, str] = {}
    for index, header in enumerate(headers):
        normalized = normalize_header(header)
        if normalized in COMPETITOR_ACCOUNT_LOOKUP:
            roles[index] = "competitor"
        elif normalized in INTERNAL_ACCOUNT_LOOKUP:
            roles[index] = "internal"
    return roles


def first_column(headers: list[Any], normalized_aliases: set[str]) -> int | None:
    for index, header in enumerate(headers):
        if normalize_header(header) in normalized_aliases:
            return index
    return None


def normalize_account_type(value: Any) -> str:
    text = cell_text(value).lower()
    if "internal" in text or "内部" in text:
        return "internal"
    if "competitor" in text or "竞品" in text:
        return "competitor"
    return ""
