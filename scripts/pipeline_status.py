#!/usr/bin/env python3
"""Pipeline status helpers for capture and enrichment rows."""

from __future__ import annotations

from typing import Any

from models import COMMENT_LEAD_SOURCES, ESTIMATED_TIME_SOURCES, has_qualified_comment_lead_link


PARTIAL_REVIEW = "partial_review"
NEEDS_ENRICHMENT = "needs_enrichment"
READY_FOR_OUTPUT = "ready_for_output"
BLOCKED = "blocked"
OUTPUT_SYNCED = "output_synced"

FINAL_STATUSES = {READY_FOR_OUTPUT, OUTPUT_SYNCED}
OPEN_STATUSES = {PARTIAL_REVIEW, NEEDS_ENRICHMENT}


def has_confirmed_time(post: dict[str, Any]) -> bool:
    return bool(
        post.get("posted_at")
        and post.get("time_confirmed")
        and str(post.get("time_source") or "") not in ESTIMATED_TIME_SOURCES
    )


def has_article_summary(post: dict[str, Any]) -> bool:
    return bool(post.get("story_summary") and post.get("summary_source") == "article")


def has_partial_review_signal(post: dict[str, Any]) -> bool:
    return bool(
        post.get("post_url")
        and (
            post.get("account_name")
            or post.get("account_url")
            or post.get("posted_date")
            or post.get("relative_time_text")
            or post.get("posted_at")
        )
        and (
            post.get("story_summary")
            or post.get("article_url")
            or post.get("landing_url")
            or post.get("raw_payload")
        )
    )


def missing_enrichment_stages(post: dict[str, Any]) -> list[str]:
    stages: list[str] = []
    if not has_confirmed_time(post):
        stages.append("detail_time")
    if not has_qualified_comment_lead_link(post):
        stages.append("lead_link")
    if post.get("landing_url") or post.get("article_url"):
        if not post.get("article_material") and not has_article_summary(post):
            stages.append("article_material")
    if not has_article_summary(post):
        stages.append("summary")
    return stages


def output_status_for(post: dict[str, Any]) -> str:
    explicit = str(post.get("output_status") or "")
    if explicit in {BLOCKED, OUTPUT_SYNCED}:
        return explicit
    if (
        post.get("post_url")
        and has_confirmed_time(post)
        and has_article_summary(post)
        and has_qualified_comment_lead_link(post)
    ):
        return READY_FOR_OUTPUT
    if has_partial_review_signal(post):
        return PARTIAL_REVIEW
    return NEEDS_ENRICHMENT


def crawl_status_for(post: dict[str, Any]) -> str:
    status = output_status_for(post)
    if status == READY_FOR_OUTPUT:
        return READY_FOR_OUTPUT
    if status == OUTPUT_SYNCED:
        return OUTPUT_SYNCED
    if status == BLOCKED:
        return BLOCKED
    return NEEDS_ENRICHMENT


def is_partial_output(post: dict[str, Any]) -> bool:
    return output_status_for(post) == PARTIAL_REVIEW


def has_qualified_lead_source(post: dict[str, Any]) -> bool:
    return post.get("lead_link_source") in COMMENT_LEAD_SOURCES
