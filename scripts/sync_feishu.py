#!/usr/bin/env python3
"""Sync normalized local records to configured Feishu sheets."""

from __future__ import annotations

import argparse
import json
from typing import Any

from config_loader import load_config
from field_schema import configured_output_headers, output_row_for_headers
from field_audit import audit_reason_counts, audit_reason_notes, audit_reason_summary
from lark_io import ensure_user_identity, write_rows
from output_quality import audit_output_candidates, output_quality_errors, partial_for_review, ready_for_output
from store import all_posts, connect, enqueue_enrichment_tasks_for_posts, mark_output_synced, row_for_post, upsert_post
from sync_status import annotate_sync_failure, annotate_sync_result, blocked_auth_result, enrichment_completion_summary


def sync_posts(
    config: dict[str, Any],
    posts: list[dict[str, Any]],
    sheet_key: str,
    mode: str,
    dry_run: bool,
    *,
    partial: bool = False,
    audit: bool = False,
    conn: Any | None = None,
    completion_posts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if conn is not None:
        refreshed_posts: list[dict[str, Any]] = []
        for post in posts:
            upsert_post(conn, post, config)
            refreshed_posts.append(row_for_post(conn, post) or post)
        posts = refreshed_posts
        enqueue_enrichment_tasks_for_posts(conn, posts, config)
    completion_scope = completion_posts if completion_posts is not None else posts
    if partial:
        partial_posts, skipped_posts = partial_for_review(posts)
        if not partial_posts:
            result = {
                "ok": False,
                "stage": "partial_gate",
                "message": "没有可供业务预览的 partial_review 记录。",
                "partial_review": 0,
                "skipped": len(skipped_posts),
            }
            if conn is not None:
                return annotate_sync_result(
                    result,
                    enrichment_completion_summary(conn, completion_scope, config),
                    ledger_mode=True,
                )
            return annotate_sync_failure(result)
        output_headers = configured_output_headers(config)
        rows = [output_row_for_headers(post, output_headers, config) for post in partial_posts]
        headers = output_headers if mode == "overwrite" else None
        result = write_rows(config, sheet_key, rows, headers=headers, mode=mode, dry_run=dry_run)
        result["partial_review"] = len(partial_posts)
        result["skipped"] = len(skipped_posts)
        result["formal_output_unchanged"] = True
        if conn is not None:
            return annotate_sync_result(
                result,
                enrichment_completion_summary(conn, completion_scope, config),
                ledger_mode=True,
            )
        return annotate_sync_failure(result)

    if audit:
        output_headers = configured_output_headers(config)
        output_posts, skipped_posts = audit_output_candidates(posts)
        if not output_posts:
            result = {
                "ok": False,
                "stage": "audit_output_gate",
                "message": "没有可写入正式表的候选记录。",
                "output_candidates": 0,
                "skipped": len(skipped_posts),
            }
            if conn is not None:
                return annotate_sync_result(
                    result,
                    enrichment_completion_summary(conn, completion_scope, config),
                    ledger_mode=True,
                )
            return annotate_sync_failure(result)
        rows = [output_row_for_headers(post, output_headers, config) for post in output_posts]
        result = write_rows(config, sheet_key, rows, headers=output_headers, mode="upsert", dry_run=dry_run)
        result["output_candidates"] = len(output_posts)
        result["skipped"] = len(skipped_posts)
        result["audit_output"] = True
        result["audit_missing_field_counts"] = audit_reason_counts(output_posts, config)
        result["audit_missing_field_summary"] = audit_reason_summary(output_posts, config)
        result["audit_missing_field_notes"] = audit_reason_notes(output_posts, config)
        if conn is not None:
            return annotate_sync_result(
                result,
                enrichment_completion_summary(conn, completion_scope, config),
                ledger_mode=True,
            )
        return annotate_sync_failure(result)

    ready_posts, skipped_posts = ready_for_output(posts, config, include_synced=mode == "append")
    errors = output_quality_errors(ready_posts, config)
    if errors:
        result = {"ok": False, "stage": "quality_gate", "errors": errors}
        if conn is not None:
            return annotate_sync_result(
                result,
                enrichment_completion_summary(conn, completion_scope, config),
                ledger_mode=False,
            )
        return annotate_sync_failure(result)
    if not ready_posts:
        result = {
            "ok": False,
            "stage": "quality_gate",
            "message": "没有字段完整、可写最终表的记录。",
            "ready_for_output": 0,
            "needs_enrichment_skipped": len(skipped_posts),
        }
        if conn is not None:
            return annotate_sync_result(
                result,
                enrichment_completion_summary(conn, completion_scope, config),
                ledger_mode=False,
            )
        return annotate_sync_failure(result)
    output_headers = configured_output_headers(config)
    rows = [output_row_for_headers(post, output_headers, config) for post in ready_posts]
    write_mode = "overwrite" if mode == "overwrite" else "upsert"
    result = write_rows(config, sheet_key, rows, headers=output_headers, mode=write_mode, dry_run=dry_run)
    if result.get("ok") and conn is not None and not dry_run:
        mark_output_synced(conn, ready_posts)
    result["ready_for_output"] = len(ready_posts)
    result["needs_enrichment_skipped"] = len(skipped_posts)
    if conn is not None:
        return annotate_sync_result(
            result,
            enrichment_completion_summary(conn, completion_scope, config),
            ledger_mode=False,
        )
    return annotate_sync_failure(result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--sheet", default="all_posts")
    parser.add_argument("--mode", choices=["append", "overwrite"], default="append")
    parser.add_argument("--partial", action="store_true", help="Write partial_review preview rows instead of formal ready rows.")
    parser.add_argument("--audit", action="store_true", help="Write auditable candidates with missing-field markers.")
    parser.add_argument("--sync-audit", "--ledger-sync", dest="audit_alias", action="store_true", help="Alias for --audit.")
    parser.add_argument("--strict-ready-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    if not args.dry_run:
        try:
            ensure_user_identity(config)
        except RuntimeError as exc:
            print(
                json.dumps(
                    {
                        **blocked_auth_result(
                            "飞书真实写入前置检查失败；已在读取本地库/同步前停止。",
                            str(exc),
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
    conn = connect(config.get("database_path", "data/posts.sqlite"))
    result = sync_posts(
        config,
        all_posts(conn),
        args.sheet,
        args.mode,
        args.dry_run,
        partial=args.partial,
        audit=bool(args.audit or args.audit_alias),
        conn=conn,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
