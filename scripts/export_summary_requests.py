#!/usr/bin/env python3
"""Export article material that still needs Codex-written Chinese summaries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from config_loader import load_config
from story_summary_policy import article_material_for_post, has_valid_story_summary, story_summary_errors
from store import all_posts, connect, query_posts


def summary_request_for(post: dict[str, Any]) -> dict[str, Any]:
    material = article_material_for_post(post)
    return {
        "post_url": post.get("post_url") or "",
        "canonical_post_url": post.get("canonical_post_url") or "",
        "article_url": post.get("landing_url") or post.get("article_url") or material.get("article_url") or "",
        "account_name": post.get("account_name") or "",
        "posted_at": post.get("posted_at") or "",
        "current_story_summary": post.get("story_summary") or "",
        "current_summary_errors": story_summary_errors(post),
        "article_material": {
            "title": material.get("title") or "",
            "meta_description": material.get("meta_description") or "",
            "text_excerpt": material.get("text_excerpt") or "",
        },
    }


def needs_summary(post: dict[str, Any], *, only_invalid: bool) -> bool:
    if only_invalid:
        return bool(post.get("story_summary") and post.get("summary_source") == "article" and not has_valid_story_summary(post))
    return not has_valid_story_summary(post) and bool(post.get("landing_url") or post.get("article_url") or article_material_for_post(post))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only-invalid", action="store_true")
    parser.add_argument("--date", default="")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--account-name", default="")
    parser.add_argument("--account-url", default="")
    parser.add_argument("--account-type", default="")
    args = parser.parse_args()

    config = load_config(args.config)
    conn = connect(config.get("database_path", "data/posts.sqlite"))
    scope_enabled = any(
        [
            args.date,
            args.start_date,
            args.end_date,
            args.account_name,
            args.account_url,
            args.account_type,
        ]
    )
    source_posts = (
        query_posts(
            conn,
            date=args.date,
            start_date=args.start_date,
            end_date=args.end_date,
            include_unknown_date=bool(args.date or args.start_date or args.end_date),
            account_name=args.account_name,
            account_url=args.account_url,
            account_type=args.account_type,
        )
        if scope_enabled
        else all_posts(conn)
    )
    posts = [post for post in source_posts if needs_summary(post, only_invalid=args.only_invalid)]
    if args.limit:
        posts = posts[: args.limit]
    requests = [summary_request_for(post) for post in posts]
    payload: dict[str, Any] = {
        "ok": True,
        "summary_language": "zh-CN",
        "instructions": "请根据 article_material 用中文生成故事概要，不要直接复制英文原文、标题或 meta 描述。返回 article_summaries.json，key 使用 post_url、canonical_post_url 或 article_url。",
        "scope": {
            "enabled": scope_enabled,
            "date": args.date,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "account_name": args.account_name,
            "account_url": args.account_url,
            "account_type": args.account_type,
            "source_post_count": len(source_posts),
        },
        "count": len(requests),
        "requests": requests,
    }
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"ok": True, "count": len(requests), "output": args.output, "scope": payload["scope"]},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
