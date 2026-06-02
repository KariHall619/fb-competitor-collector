#!/usr/bin/env python3
"""Import existing skill output into SQLite and optionally sync to Feishu."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from config_loader import load_config
from field_schema import configured_output_headers, output_row_for_headers
from models import normalize_post
from output_quality import audit_output_candidates, output_quality_errors, partial_for_review
from store import connect, enqueue_enrichment_tasks_for_posts, mark_output_synced, upsert_posts
from sync_status import annotate_sync_result, enrichment_completion_summary
from lark_io import ensure_user_identity, write_rows


def load_records(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            if "posts" in data:
                data = data["posts"]
            elif "items" in data:
                data = data["items"]
            else:
                data = [data]
        return list(data)
    if p.suffix.lower() == ".csv":
        with p.open(newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))
    raise ValueError(f"Unsupported input type: {p.suffix}")


def load_metadata(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if p.suffix.lower() != ".json":
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--input", required=True)
    parser.add_argument("--account-name", default="")
    parser.add_argument("--account-url", default="")
    parser.add_argument("--account-type", default="competitor")
    parser.add_argument("--source-skill", default="manual-import")
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--sync-audit", action="store_true", help="Write auditable candidates with missing-field markers.")
    parser.add_argument("--sync-partial", action="store_true")
    parser.add_argument("--strict-ready-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-sync", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    real_feishu_write_requested = (
        not args.dry_run
        and not args.no_sync
        and (args.sync or args.sync_audit or args.sync_partial)
    )
    if real_feishu_write_requested:
        try:
            ensure_user_identity(config)
        except RuntimeError as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "stage": "feishu_auth_preflight",
                        "message": "飞书真实写入前置检查失败；已在导入/写库前停止。",
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
    defaults = {
        "account_name": args.account_name,
        "account_url": args.account_url,
        "account_type": args.account_type,
        "source_skill": args.source_skill,
    }
    raw_records = load_records(args.input)
    metadata = load_metadata(args.input)
    posts = [normalize_post(record, defaults) for record in raw_records]
    conn = connect(config.get("database_path", "data/posts.sqlite"))
    try:
        result = upsert_posts(conn, posts)
    except sqlite3.OperationalError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "stage": "sqlite_write",
                    "error": str(exc),
                    "database_path": config.get("database_path", "data/posts.sqlite"),
                    "message": "本地内容库不可写，已停止导入；请确认当前执行环境有项目目录写权限。",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    task_result = enqueue_enrichment_tasks_for_posts(conn, result.get("sync_candidates") or posts)
    import_summary = {
        "input": len(raw_records),
        "inserted": len(result["inserted"]),
        "updated": result["updated"],
        "errors": result["errors"],
        "enrichment_tasks": task_result,
    }

    should_sync = args.sync and not args.no_sync
    should_sync_audit = (args.sync or args.sync_audit) and not args.no_sync and not args.strict_ready_only
    should_sync_strict = should_sync and args.strict_ready_only
    should_sync_partial = args.sync_partial and not args.no_sync
    if should_sync_partial:
        sync_candidates = result.get("sync_candidates") or result["inserted"]
        partial_posts, skipped_posts = partial_for_review(sync_candidates)
        if not partial_posts:
            print(
                json.dumps(
                    {**import_summary,
                        "feishu_sync": {
                            "ok": False,
                            "stage": "partial_gate",
                            "message": "当前没有可供业务预览的 partial_review 记录。",
                            "partial_review": 0,
                            "skipped": len(skipped_posts),
                            "enrichment_completion": enrichment_completion_summary(conn, sync_candidates),
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        headers = configured_output_headers(config)
        rows = [output_row_for_headers(post, headers) for post in partial_posts]
        sync_result = write_rows(
            config,
            "filter_result",
            rows,
            headers=headers,
            mode="overwrite",
            dry_run=args.dry_run,
        )
        sync_result["partial_review"] = len(partial_posts)
        sync_result["skipped"] = len(skipped_posts)
        sync_result["formal_output_unchanged"] = True
        sync_result = annotate_sync_result(
            sync_result,
            enrichment_completion_summary(conn, sync_candidates),
            ledger_mode=True,
        )
        print(json.dumps({**import_summary, "feishu_sync": sync_result}, ensure_ascii=False, indent=2))
        return 0 if sync_result.get("ok") else 1

    if should_sync_audit:
        sync_candidates = result.get("sync_candidates") or result["inserted"]
        headers = configured_output_headers(config)
        output_posts, skipped_posts = audit_output_candidates(sync_candidates)
        if not output_posts:
            print(
                json.dumps(
                    {**import_summary,
                        "feishu_sync": {
                            "ok": False,
                            "stage": "audit_output_gate",
                            "message": "当前没有可写入正式表的候选记录。",
                            "output_candidates": 0,
                            "skipped": len(skipped_posts),
                            "enrichment_completion": enrichment_completion_summary(conn, sync_candidates),
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        rows = [output_row_for_headers(post, headers) for post in output_posts]
        sync_result = write_rows(
            config,
            "all_posts",
            rows,
            headers=headers,
            mode="upsert",
            dry_run=args.dry_run,
        )
        sync_result["output_candidates"] = len(output_posts)
        sync_result["skipped"] = len(skipped_posts)
        sync_result["audit_output"] = True
        sync_result = annotate_sync_result(
            sync_result,
            enrichment_completion_summary(conn, sync_candidates),
            ledger_mode=True,
        )
        print(json.dumps({**import_summary, "feishu_sync": sync_result}, ensure_ascii=False, indent=2))
        return 0 if sync_result.get("ok") else 1

    if should_sync_strict:
        sync_candidates = result.get("sync_candidates") or result["inserted"]
        ready_posts = [post for post in sync_candidates if post.get("output_status") == "ready_for_output"]
        quality_errors = output_quality_errors(ready_posts)
        if quality_errors:
            print(
                json.dumps(
                    {**import_summary,
                        "feishu_sync": {
                            "ok": False,
                            "stage": "quality_gate",
                            "message": "同步已停止：存在完全缺失发帖时间、未生成文章来源中文概要，或缺少评论/回复引流落地链接的记录。",
                            "errors": quality_errors,
                            "enrichment_completion": enrichment_completion_summary(conn, sync_candidates),
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        skipped = len(sync_candidates) - len(ready_posts)
        if not ready_posts:
            print(
                json.dumps(
                    {**import_summary,
                        "feishu_sync": {
                            "ok": False,
                            "stage": "quality_gate",
                            "message": "同步已停止：当前没有字段完整、可写最终表的记录；候选已保存在本地库，需继续补齐发帖时间、摘要和评论/回复引流落地链接。",
                            "ready_for_output": 0,
                            "needs_enrichment_skipped": skipped,
                            "enrichment_completion": enrichment_completion_summary(conn, sync_candidates),
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        headers = configured_output_headers(config)
        rows = [output_row_for_headers(post, headers) for post in ready_posts]
        sync_result = write_rows(
            config,
            "all_posts",
            rows,
            headers=None,
            mode="append",
            dry_run=args.dry_run,
        )
        if sync_result.get("ok") and not args.dry_run:
            mark_output_synced(conn, ready_posts)
        sync_result["ready_for_output"] = len(ready_posts)
        sync_result["needs_enrichment_skipped"] = skipped
        sync_result = annotate_sync_result(
            sync_result,
            enrichment_completion_summary(conn, sync_candidates),
            ledger_mode=False,
        )
        print(json.dumps({**import_summary, "feishu_sync": sync_result}, ensure_ascii=False, indent=2))
        return 0 if sync_result.get("ok") else 1
    print(json.dumps(import_summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
