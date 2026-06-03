#!/usr/bin/env python3
"""Audit and optionally downgrade invalid local story summaries."""

from __future__ import annotations

import argparse
import json
from typing import Any

from config_loader import load_config
from pipeline_status import crawl_status_for, output_status_for
from story_summary_policy import story_summary_errors
from store import all_posts, connect, enqueue_enrichment_tasks, update_post_fields


def invalid_summary_record(post: dict[str, Any]) -> dict[str, Any] | None:
    if post.get("summary_source") != "article":
        return None
    errors = story_summary_errors(post)
    if not errors:
        return None
    return {
        "post_url": post.get("post_url"),
        "canonical_post_url": post.get("canonical_post_url"),
        "article_url": post.get("landing_url") or post.get("article_url"),
        "story_summary": post.get("story_summary"),
        "errors": errors,
    }


def downgraded_fields(post: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    next_post = {
        **post,
        "summary_source": "pending_article_summary",
        "output_status": "",
        "crawl_status": "",
    }
    next_post["output_status"] = output_status_for(next_post, config)
    next_post["crawl_status"] = crawl_status_for(next_post, config)
    note = str(post.get("note") or "")
    parts = [part for part in note.split("；") if part]
    if "故事概要需重新生成中文摘要" not in parts:
        parts.append("故事概要需重新生成中文摘要")
    return {
        "summary_source": "pending_article_summary",
        "output_status": next_post["output_status"],
        "crawl_status": next_post["crawl_status"],
        "note": "；".join(parts),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--fix", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    config = load_config(args.config)
    conn = connect(config.get("database_path", "data/posts.sqlite"))
    posts = all_posts(conn)
    invalid: list[dict[str, Any]] = []
    fixed = 0
    for post in posts:
        record = invalid_summary_record(post)
        if not record:
            continue
        invalid.append(record)
        if args.limit and len(invalid) >= args.limit:
            break
    if args.fix:
        posts_by_key = {
            str(post.get("canonical_post_url") or post.get("post_url") or ""): post
            for post in posts
            if post.get("canonical_post_url") or post.get("post_url")
        }
        for record in invalid:
            key = str(record.get("canonical_post_url") or record.get("post_url") or "")
            post = posts_by_key.get(key)
            if not post:
                continue
            fields = downgraded_fields(post, config)
            update_post_fields(conn, post, fields)
            stored = {**post, **fields}
            enqueue_enrichment_tasks(conn, stored, stages=["summary"])
            fixed += 1
    print(json.dumps({"ok": True, "invalid": len(invalid), "fixed": fixed, "items": invalid}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
