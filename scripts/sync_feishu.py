#!/usr/bin/env python3
"""Sync normalized local records to configured Feishu sheets."""

from __future__ import annotations

import argparse
from typing import Any

from config_loader import load_config
from lark_io import write_rows
from models import POST_HEADERS, output_row
from output_quality import output_quality_errors, ready_for_output
from store import all_posts, connect


def sync_posts(config: dict[str, Any], posts: list[dict[str, Any]], sheet_key: str, mode: str, dry_run: bool) -> dict[str, Any]:
    ready_posts, skipped_posts = ready_for_output(posts)
    errors = output_quality_errors(ready_posts)
    if errors:
        return {"ok": False, "stage": "quality_gate", "errors": errors}
    if not ready_posts:
        return {
            "ok": False,
            "stage": "quality_gate",
            "message": "没有字段完整、可写最终表的记录。",
            "ready_for_output": 0,
            "needs_enrichment_skipped": len(skipped_posts),
        }
    rows = [output_row(post) for post in ready_posts]
    headers = POST_HEADERS if mode == "overwrite" else None
    result = write_rows(config, sheet_key, rows, headers=headers, mode=mode, dry_run=dry_run)
    result["ready_for_output"] = len(ready_posts)
    result["needs_enrichment_skipped"] = len(skipped_posts)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--sheet", default="all_posts")
    parser.add_argument("--mode", choices=["append", "overwrite"], default="append")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    conn = connect(config.get("database_path", "data/posts.sqlite"))
    result = sync_posts(config, all_posts(conn), args.sheet, args.mode, args.dry_run)
    print(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
