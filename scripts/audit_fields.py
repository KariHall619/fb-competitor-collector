#!/usr/bin/env python3
"""Audit local posts for missing/refetchable Feishu output fields."""

from __future__ import annotations

import argparse
import json
from typing import Any

from config_loader import load_config
from field_audit import audit_fields_for_storage, audit_post_fields
from store import all_posts, connect, enqueue_enrichment_tasks, update_post_fields


def audit_and_queue(conn, posts: list[dict[str, Any]], config: dict[str, Any], *, fix: bool) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    queued = 0
    passed = 0
    for post in posts:
        audit = audit_post_fields(post, config)
        storage_fields = audit_fields_for_storage(post, config)
        if fix:
            update_post_fields(conn, post, storage_fields)
        if audit["field_audit_status"] == "passed":
            passed += 1
            continue
        item = {
            "post_url": post.get("post_url"),
            "canonical_post_url": post.get("canonical_post_url"),
            "field_audit_status": audit["field_audit_status"],
            "field_audit_reasons": audit["field_audit_reasons"],
            "field_audit_note": audit["field_audit_note"],
            "refetch_stages": audit["refetch_stages"],
        }
        items.append(item)
        if fix:
            next_post = {**post, **storage_fields}
            queued += enqueue_enrichment_tasks(conn, next_post, stages=audit["refetch_stages"])
    reason_counts: dict[str, int] = {}
    stage_counts: dict[str, int] = {}
    for item in items:
        for reason in item["field_audit_reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        for stage in item["refetch_stages"]:
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
    return {
        "ok": True,
        "input": len(posts),
        "passed": passed,
        "needs_refetch": len(items),
        "queued": queued,
        "reason_counts": reason_counts,
        "stage_counts": stage_counts,
        "items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--fix", action="store_true", help="Persist audit fields and queue refetch tasks.")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    config = load_config(args.config)
    conn = connect(config.get("database_path", "data/posts.sqlite"))
    posts = all_posts(conn)
    if args.limit:
        posts = posts[: args.limit]
    result = audit_and_queue(conn, posts, config, fix=args.fix)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
