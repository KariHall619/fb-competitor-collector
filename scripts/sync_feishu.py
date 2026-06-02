#!/usr/bin/env python3
"""Sync normalized local records to configured Feishu sheets."""

from __future__ import annotations

import argparse
from typing import Any

from config_loader import load_config
from field_schema import configured_output_headers, output_row_for_headers
from lark_io import write_rows
from output_quality import audit_output_candidates, output_quality_errors, partial_for_review, ready_for_output
from store import all_posts, connect


def sync_posts(
    config: dict[str, Any],
    posts: list[dict[str, Any]],
    sheet_key: str,
    mode: str,
    dry_run: bool,
    *,
    partial: bool = False,
    audit: bool = False,
) -> dict[str, Any]:
    if partial:
        partial_posts, skipped_posts = partial_for_review(posts)
        if not partial_posts:
            return {
                "ok": False,
                "stage": "partial_gate",
                "message": "没有可供业务预览的 partial_review 记录。",
                "partial_review": 0,
                "skipped": len(skipped_posts),
            }
        output_headers = configured_output_headers(config)
        rows = [output_row_for_headers(post, output_headers) for post in partial_posts]
        headers = output_headers if mode == "overwrite" else None
        result = write_rows(config, sheet_key, rows, headers=headers, mode=mode, dry_run=dry_run)
        result["partial_review"] = len(partial_posts)
        result["skipped"] = len(skipped_posts)
        result["formal_output_unchanged"] = True
        return result

    if audit:
        output_headers = configured_output_headers(config)
        output_posts, skipped_posts = audit_output_candidates(posts)
        if not output_posts:
            return {
                "ok": False,
                "stage": "audit_output_gate",
                "message": "没有可写入正式表的候选记录。",
                "output_candidates": 0,
                "skipped": len(skipped_posts),
            }
        rows = [output_row_for_headers(post, output_headers) for post in output_posts]
        result = write_rows(config, sheet_key, rows, headers=output_headers, mode="upsert", dry_run=dry_run)
        result["output_candidates"] = len(output_posts)
        result["skipped"] = len(skipped_posts)
        result["audit_output"] = True
        return result

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
    output_headers = configured_output_headers(config)
    rows = [output_row_for_headers(post, output_headers) for post in ready_posts]
    headers = output_headers if mode == "overwrite" else None
    result = write_rows(config, sheet_key, rows, headers=headers, mode=mode, dry_run=dry_run)
    result["ready_for_output"] = len(ready_posts)
    result["needs_enrichment_skipped"] = len(skipped_posts)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--sheet", default="all_posts")
    parser.add_argument("--mode", choices=["append", "overwrite"], default="append")
    parser.add_argument("--partial", action="store_true", help="Write partial_review preview rows instead of formal ready rows.")
    parser.add_argument("--audit", action="store_true", help="Write auditable candidates with missing-field markers.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    conn = connect(config.get("database_path", "data/posts.sqlite"))
    result = sync_posts(
        config,
        all_posts(conn),
        args.sheet,
        args.mode,
        args.dry_run,
        partial=args.partial,
        audit=args.audit,
    )
    print(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
