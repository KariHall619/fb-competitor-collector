#!/usr/bin/env python3
"""Apply Codex-written Chinese article summaries to prepared posts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pipeline_status import crawl_status_for, output_status_for
from story_summary_policy import story_summary_errors
from config_loader import load_config
from store import all_posts, connect, mark_stage_done, update_post_fields


def summary_for_post(post: dict[str, Any], summaries: dict[str, Any]) -> str:
    keys = [
        post.get("post_url"),
        post.get("canonical_post_url"),
        post.get("landing_url"),
        post.get("article_url"),
    ]
    return next((summaries.get(key) for key in keys if key and summaries.get(key)), "")


def applied_fields(post: dict[str, Any], summary: str) -> dict[str, Any]:
    next_post = {**post, "story_summary": summary.strip(), "summary_source": "article"}
    next_post["output_status"] = output_status_for(next_post)
    next_post["crawl_status"] = crawl_status_for(next_post)
    note = next_post.get("note") or ""
    next_post["note"] = "；".join(
        part
        for part in note.split("；")
        if part and part not in {"文章概要待生成", "故事概要需重新生成中文摘要"}
    )
    return {
        "story_summary": next_post["story_summary"],
        "summary_source": "article",
        "note": next_post["note"],
        "output_status": next_post["output_status"],
        "crawl_status": next_post["crawl_status"],
    }


def apply_to_posts(posts: list[dict[str, Any]], summaries: dict[str, Any]) -> dict[str, Any]:
    applied = 0
    missing = []
    rejected = []
    fields_by_key: dict[str, dict[str, Any]] = {}
    for post in posts:
        summary = summary_for_post(post, summaries)
        if not summary:
            missing.append(post.get("post_url"))
            continue
        candidate = {**post, "story_summary": str(summary).strip(), "summary_source": "article"}
        errors = story_summary_errors(candidate)
        if errors:
            rejected.append({"post_url": post.get("post_url"), "errors": errors})
            post["output_status"] = output_status_for(post)
            post["crawl_status"] = crawl_status_for(post)
            continue
        fields = applied_fields(post, str(summary))
        post.update(fields)
        key = str(post.get("canonical_post_url") or post.get("post_url") or "")
        if key:
            fields_by_key[key] = fields
        applied += 1
    return {"applied": applied, "missing": missing, "rejected": rejected, "fields_by_key": fields_by_key}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="")
    parser.add_argument("--input", default="")
    parser.add_argument("--summaries", required=True, help="JSON object keyed by post_url/canonical_post_url/article_url")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    summaries = json.loads(Path(args.summaries).read_text(encoding="utf-8"))
    if args.config and not args.input:
        config = load_config(args.config)
        conn = connect(config.get("database_path", "data/posts.sqlite"))
        posts = all_posts(conn)
        result = apply_to_posts(posts, summaries)
        for post in posts:
            key = str(post.get("canonical_post_url") or post.get("post_url") or "")
            fields = result["fields_by_key"].get(key)
            if not fields:
                continue
            update_post_fields(conn, post, fields)
            mark_stage_done(conn, post, "summary")
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": "sqlite",
                    "applied": result["applied"],
                    "missing": len(result["missing"]),
                    "rejected": len(result["rejected"]),
                    "article_summary_missing": result["missing"],
                    "article_summary_rejected": result["rejected"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if not args.input or not args.output:
        parser.error("--input and --output are required unless --config is used for SQLite mode")

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    posts = payload.get("posts", [])
    result = apply_to_posts(posts, summaries)

    payload["article_summary_applied"] = result["applied"]
    payload["article_summary_missing"] = result["missing"]
    payload["article_summary_rejected"] = result["rejected"]
    payload["ready"] = sum(1 for item in posts if item.get("crawl_status") == "ready")
    payload["ready_for_output"] = sum(1 for item in posts if item.get("output_status") == "ready_for_output")
    payload["partial_review"] = sum(1 for item in posts if item.get("output_status") == "partial_review")
    payload["needs_enrichment"] = sum(1 for item in posts if item.get("crawl_status") == "needs_enrichment")
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "mode": "file",
                "applied": result["applied"],
                "missing": len(result["missing"]),
                "rejected": len(result["rejected"]),
                "output": args.output,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
