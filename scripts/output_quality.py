#!/usr/bin/env python3
"""Final-output quality gate for FB competitor rows."""

from __future__ import annotations

from typing import Any

from models import COMMENT_LEAD_SOURCES, ESTIMATED_TIME_SOURCES
from story_summary_policy import story_summary_errors

def has_qualified_comment_lead_link(post: dict[str, Any]) -> bool:
    return (
        post.get("lead_link_status") == "qualified"
        and post.get("lead_link_source") in COMMENT_LEAD_SOURCES
        and bool(post.get("landing_url") or post.get("article_url"))
    )


def output_quality_errors(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for index, post in enumerate(posts, 1):
        row_errors = []
        if not post.get("posted_at"):
            row_errors.append("missing_hour_level_posted_at")
        if not post.get("time_confirmed") or post.get("time_source") in ESTIMATED_TIME_SOURCES:
            row_errors.append("unconfirmed_or_estimated_posted_at")
        summary_errors = story_summary_errors(post)
        if post.get("summary_source") != "article":
            row_errors.append("missing_article_summary")
        elif summary_errors:
            row_errors.extend(summary_errors)
        if not has_qualified_comment_lead_link(post):
            row_errors.append("missing_qualified_comment_lead_link")
        if row_errors:
            errors.append(
                {
                    "index": index,
                    "post_url": post.get("post_url"),
                    "errors": row_errors,
                }
            )
    return errors


def ready_for_output(posts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ready = [post for post in posts if post.get("output_status") == "ready_for_output"]
    skipped = [post for post in posts if post.get("output_status") != "ready_for_output"]
    return ready, skipped


def partial_for_review(posts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    partial = [post for post in posts if post.get("output_status") in {"partial_review", "ready_for_output"}]
    skipped = [post for post in posts if post.get("output_status") not in {"partial_review", "ready_for_output"}]
    return partial, skipped
