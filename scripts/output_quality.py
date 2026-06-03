#!/usr/bin/env python3
"""Final-output quality gate for FB competitor rows."""

from __future__ import annotations

from typing import Any

from field_audit import audit_post_fields
from models import ESTIMATED_TIME_SOURCES, has_qualified_comment_lead_link
from story_summary_policy import story_summary_errors
from value_utils import parse_bool


def output_quality_errors(posts: list[dict[str, Any]], config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for index, post in enumerate(posts, 1):
        row_errors = []
        if not post.get("posted_at"):
            row_errors.append("missing_hour_level_posted_at")
        if not parse_bool(post.get("time_confirmed")) or post.get("time_source") in ESTIMATED_TIME_SOURCES:
            row_errors.append("unconfirmed_or_estimated_posted_at")
        summary_errors = story_summary_errors(post)
        if post.get("summary_source") != "article":
            row_errors.append("missing_article_summary")
        elif summary_errors:
            row_errors.extend(summary_errors)
        if not has_qualified_comment_lead_link(post):
            row_errors.append("missing_qualified_comment_lead_link")
        if config is not None:
            for reason in audit_post_fields(post, config).get("field_audit_reasons", []):
                error = f"field_audit_{reason}"
                if error not in row_errors:
                    row_errors.append(error)
        if row_errors:
            errors.append(
                {
                    "index": index,
                    "post_url": post.get("post_url"),
                    "errors": row_errors,
                }
            )
    return errors


def ready_for_output(
    posts: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ready = [
        post
        for post in posts
        if post.get("output_status") == "ready_for_output"
        and (config is None or audit_post_fields(post, config).get("field_audit_status") == "passed")
    ]
    skipped = [post for post in posts if post not in ready]
    return ready, skipped


def partial_for_review(posts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    partial = [post for post in posts if post.get("output_status") in {"partial_review", "ready_for_output"}]
    skipped = [post for post in posts if post.get("output_status") not in {"partial_review", "ready_for_output"}]
    return partial, skipped


def audit_output_candidates(posts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates = [
        post
        for post in posts
        if post.get("post_url")
        and (post.get("account_name") or post.get("account_url"))
        and post.get("output_status") not in {"blocked"}
    ]
    skipped = [post for post in posts if post not in candidates]
    return candidates, skipped
