#!/usr/bin/env python3
"""Filter local FB post library and optionally sync the result to Feishu."""

from __future__ import annotations

import argparse
import json
from typing import Any

from config_loader import deep_get, load_config
from field_schema import configured_output_headers, output_row_for_headers
from lark_io import ensure_user_identity, write_rows
from models import normalize_date
from output_quality import audit_output_candidates, output_quality_errors, partial_for_review, ready_for_output
from store import connect, query_posts
from sync_status import annotate_sync_result, enrichment_completion_summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--date", default="")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--account-name", default="")
    parser.add_argument("--account-url", default="")
    parser.add_argument("--account-type", default="")
    parser.add_argument("--post-type", default="")
    parser.add_argument("--min-views", type=int)
    parser.add_argument("--min-likes", type=int)
    parser.add_argument("--hot-views", action="store_true")
    parser.add_argument("--hot-likes", action="store_true")
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--sync-audit", action="store_true", help="Write auditable candidates with missing-field markers.")
    parser.add_argument("--sync-partial", action="store_true")
    parser.add_argument("--strict-ready-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    real_feishu_write_requested = not args.dry_run and (args.sync or args.sync_audit or args.sync_partial)
    if real_feishu_write_requested:
        try:
            ensure_user_identity(config)
        except RuntimeError as exc:
            print(
                json.dumps(
                    {
                        "feishu_sync": {
                            "ok": False,
                            "stage": "feishu_auth_preflight",
                            "message": "飞书真实写入前置检查失败；已在查询/同步前停止。",
                            "error": str(exc),
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
    min_views = args.min_views
    min_likes = args.min_likes
    if args.hot_views:
        min_views = int(deep_get(config, "filters.hot_views", 100000))
    if args.hot_likes:
        min_likes = int(deep_get(config, "filters.hot_likes", 100))
    conn = connect(config.get("database_path", "data/posts.sqlite"))
    posts = query_posts(
        conn,
        date=normalize_date(args.date) if args.date else "",
        start_date=normalize_date(args.start_date) if args.start_date else "",
        end_date=normalize_date(args.end_date) if args.end_date else "",
        account_name=args.account_name,
        account_url=args.account_url,
        account_type=args.account_type,
        post_type=args.post_type,
        min_views=min_views,
        min_likes=min_likes,
    )
    hit_rule = ", ".join(
        part
        for part in [
            f"date={args.date}" if args.date else "",
            f"start={args.start_date}" if args.start_date else "",
            f"end={args.end_date}" if args.end_date else "",
            f"account_name={args.account_name}" if args.account_name else "",
            f"account_url={args.account_url}" if args.account_url else "",
            f"account_type={args.account_type}" if args.account_type else "",
            f"post_type={args.post_type}" if args.post_type else "",
            f"views>={min_views}" if min_views is not None else "",
            f"likes>={min_likes}" if min_likes is not None else "",
        ]
        if part
    ) or "all"
    print(json.dumps({"count": len(posts), "hit_rule": hit_rule}, ensure_ascii=False, indent=2))
    if args.sync_partial:
        headers = configured_output_headers(config)
        partial_posts, skipped_posts = partial_for_review(posts)
        if not partial_posts:
            result = annotate_sync_result(
                {
                    "ok": False,
                    "stage": "partial_gate",
                    "message": "筛选结果中没有可供业务预览的 partial_review 记录。",
                    "partial_review": 0,
                    "skipped": len(skipped_posts),
                },
                enrichment_completion_summary(conn, posts),
                ledger_mode=True,
            )
            print(
                json.dumps(
                    {"feishu_sync": result},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        rows = [output_row_for_headers(post, headers) for post in partial_posts]
        result = write_rows(
            config,
            "filter_result",
            rows,
            headers=headers,
            mode="overwrite",
            dry_run=args.dry_run,
        )
        result["partial_review"] = len(partial_posts)
        result["skipped"] = len(skipped_posts)
        result["formal_output_unchanged"] = True
        result = annotate_sync_result(
            result,
            enrichment_completion_summary(conn, posts),
            ledger_mode=True,
        )
        print(json.dumps({"feishu_sync": result}, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    if (args.sync or args.sync_audit) and not args.strict_ready_only:
        headers = configured_output_headers(config)
        output_posts, skipped_posts = audit_output_candidates(posts)
        if not output_posts:
            result = annotate_sync_result(
                {
                    "ok": False,
                    "stage": "audit_output_gate",
                    "message": "筛选结果中没有可写入正式表的候选记录。",
                    "output_candidates": 0,
                    "skipped": len(skipped_posts),
                },
                enrichment_completion_summary(conn, posts),
                ledger_mode=True,
            )
            print(
                json.dumps(
                    {"feishu_sync": result},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        rows = [output_row_for_headers(post, headers) for post in output_posts]
        result = write_rows(
            config,
            "filter_result",
            rows,
            headers=headers,
            mode="upsert",
            dry_run=args.dry_run,
        )
        result["output_candidates"] = len(output_posts)
        result["skipped"] = len(skipped_posts)
        result["audit_output"] = True
        result = annotate_sync_result(
            result,
            enrichment_completion_summary(conn, posts),
            ledger_mode=True,
        )
        print(json.dumps({"feishu_sync": result}, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    if args.sync and args.strict_ready_only:
        headers = configured_output_headers(config)
        ready_posts, skipped_posts = ready_for_output(posts)
        errors = output_quality_errors(ready_posts)
        if errors:
            result = annotate_sync_result(
                {
                    "ok": False,
                    "stage": "quality_gate",
                    "errors": errors,
                },
                enrichment_completion_summary(conn, posts),
                ledger_mode=False,
            )
            print(
                json.dumps(
                    {"feishu_sync": result},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        if not ready_posts:
            result = annotate_sync_result(
                {
                    "ok": False,
                    "stage": "quality_gate",
                    "message": "筛选结果中没有字段完整、可写最终表的记录。",
                    "ready_for_output": 0,
                    "needs_enrichment_skipped": len(skipped_posts),
                },
                enrichment_completion_summary(conn, posts),
                ledger_mode=False,
            )
            print(
                json.dumps(
                    {"feishu_sync": result},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        rows = [output_row_for_headers(post, headers) for post in ready_posts]
        result = write_rows(
            config,
            "filter_result",
            rows,
            headers=headers,
            mode="overwrite",
            dry_run=args.dry_run,
        )
        result["ready_for_output"] = len(ready_posts)
        result["needs_enrichment_skipped"] = len(skipped_posts)
        result = annotate_sync_result(
            result,
            enrichment_completion_summary(conn, posts),
            ledger_mode=False,
        )
        print(json.dumps({"feishu_sync": result}, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
