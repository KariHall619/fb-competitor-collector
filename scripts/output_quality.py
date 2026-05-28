#!/usr/bin/env python3
"""Final-output quality gate for FB competitor rows."""

from __future__ import annotations

from typing import Any

from models import has_qualified_comment_lead_link


def output_quality_errors(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for index, post in enumerate(posts, 1):
        row_errors = []
        if not post.get("posted_at"):
            row_errors.append("missing_hour_level_posted_at")
        if post.get("summary_source") != "article" or not post.get("story_summary"):
            row_errors.append("missing_article_summary")
        if post.get("lead_link_status") != "qualified" or not (post.get("landing_url") or post.get("article_url")):
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
