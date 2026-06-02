#!/usr/bin/env python3
"""Resumable account-level capture, enrichment, and Feishu ledger sync."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from check_env import check_opencli
from config_loader import load_config
from lark_io import ensure_user_identity
from models import normalize_date
from store import (
    connect,
    enqueue_enrichment_tasks_for_posts,
    query_posts,
    task_counts_for_posts,
)
from sync_feishu import sync_posts
from sync_status import enrichment_completion_summary


ROOT = Path(__file__).resolve().parents[1]
ENRICHMENT_STAGES = "detail_time,lead_link,engagement,post_type,article_material"


def run_command(command: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False, timeout=timeout)


def parse_json_output(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}


def normalize_date_text(value: str) -> str:
    return normalize_date(value) if value.strip() else ""


def dates_for_last_hours(hours: int, *, timezone_name: str) -> list[str]:
    tz = ZoneInfo(timezone_name)
    now = datetime.now(tz)
    start = now - timedelta(hours=hours)
    current = start.date()
    end = now.date()
    dates: list[str] = []
    while current <= end:
        dates.append(current.strftime("%y%m%d"))
        current = current + timedelta(days=1)
    return dates


def scoped_posts(
    conn: Any,
    *,
    account_name: str,
    account_url: str,
    account_type: str,
    dates: list[str],
) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for date in dates:
        for post in query_posts(
            conn,
            date=date,
            account_name=account_name,
            account_url=account_url,
            account_type=account_type,
        ):
            key = post.get("canonical_post_url") or post.get("post_url")
            if key:
                by_key[str(key)] = post
    if not by_key and account_url:
        for post in query_posts(conn, account_url=account_url, account_type=account_type):
            if dates and post.get("posted_date") not in set(dates):
                continue
            key = post.get("canonical_post_url") or post.get("post_url")
            if key:
                by_key[str(key)] = post
    return sorted(by_key.values(), key=lambda item: (item.get("posted_date") or "", item.get("id") or 0))


def import_prepared(
    config_path: str,
    prepared_path: Path,
    *,
    account_name: str,
    account_url: str,
    account_type: str,
) -> dict[str, Any]:
    result = run_command(
        [
            "python3",
            "scripts/import_existing_result.py",
            "--config",
            config_path,
            "--input",
            str(prepared_path),
            "--account-name",
            account_name,
            "--account-url",
            account_url,
            "--account-type",
            account_type,
            "--no-sync",
        ]
    )
    payload = parse_json_output(result)
    payload["returncode"] = result.returncode
    return payload


def discover_and_import(
    args: argparse.Namespace,
    *,
    target_dates: list[str],
) -> dict[str, Any]:
    discover_started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="fb-account-job-") as temp_dir:
        temp = Path(temp_dir)
        raw_path = temp / "raw.json"
        discover = run_command(
            [
                "node",
                "scripts/opencli_extract_current_tab.mjs",
                "--config",
                args.config,
                "--account-url",
                args.account_url,
                "--max-text",
                str(args.max_text),
                "--max-snapshots",
                str(args.max_snapshots),
            ]
        )
        discover_payload = parse_json_output(discover)
        discover_payload["returncode"] = discover.returncode
        if discover.returncode != 0 or not discover_payload.get("ok"):
            return {
                "ok": False,
                "stage": "discover",
                "elapsed_ms": int((time.monotonic() - discover_started) * 1000),
                "discover": discover_payload,
            }
        raw_path.write_text(json.dumps(discover_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        imports: list[dict[str, Any]] = []
        prepared_counts: dict[str, int] = {}
        for target_date in target_dates:
            prepared_path = temp / f"prepared_{target_date}.json"
            prepare = run_command(
                [
                    "python3",
                    "scripts/prepare_capture_result.py",
                    "--input",
                    str(raw_path),
                    "--output",
                    str(prepared_path),
                    "--target-date",
                    target_date,
                    "--account-url",
                    args.account_url,
                    "--account-name",
                    args.account_name,
                    "--account-type",
                    args.account_type,
                ]
            )
            if prepare.returncode != 0:
                return {
                    "ok": False,
                    "stage": "prepare",
                    "target_date": target_date,
                    "stdout": prepare.stdout,
                    "stderr": prepare.stderr,
                    "returncode": prepare.returncode,
                }
            prepared_payload = json.loads(prepared_path.read_text(encoding="utf-8"))
            prepared_counts[target_date] = int(prepared_payload.get("prepared") or 0)
            if prepared_counts[target_date]:
                imports.append(
                    {
                        "target_date": target_date,
                        "import": import_prepared(
                            args.config,
                            prepared_path,
                            account_name=args.account_name,
                            account_url=args.account_url,
                            account_type=args.account_type,
                        ),
                    }
                )

        return {
            "ok": True,
            "stage": "discover_import",
            "elapsed_ms": int((time.monotonic() - discover_started) * 1000),
            "target_dates": target_dates,
            "discover": {
                "raw_candidate_count": discover_payload.get("raw_candidate_count", 0),
                "post_count": discover_payload.get("post_count", 0),
                "capture_complete": discover_payload.get("capture_complete", True),
                "coverage": discover_payload.get("coverage", {}),
                "coverage_blocked": discover_payload.get("coverage_blocked", False),
                "coverage_incomplete": discover_payload.get("coverage_incomplete", False),
            },
            "prepared_counts": prepared_counts,
            "imports": imports,
        }


def run_worker_pass(args: argparse.Namespace, *, target_dates: list[str], pass_index: int) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for target_date in target_dates:
        command = [
            "python3",
            "scripts/enrichment_worker.py",
            "--config",
            args.config,
            "--stages",
            ENRICHMENT_STAGES,
            "--date",
            target_date,
            "--account-url",
            args.account_url,
            "--account-type",
            args.account_type,
            "--limit",
            str(args.enrichment_limit),
        ]
        if args.account_name:
            command.extend(["--account-name", args.account_name])
        if target_date:
            command.extend(["--target-date", target_date])
        worker = run_command(command)
        payload = parse_json_output(worker)
        payload["returncode"] = worker.returncode
        payload["target_date"] = target_date
        results.append(payload)
    failed = [item for item in results if item.get("returncode") not in {0, 1}]
    return {
        "ok": not failed,
        "pass": pass_index,
        "results": results,
    }


def run_sync(
    config: dict[str, Any],
    args: argparse.Namespace,
    posts: list[dict[str, Any]],
    conn: Any,
) -> dict[str, Any]:
    if not args.sync:
        completion = enrichment_completion_summary(conn, posts)
        return {
            "ok": True,
            "skipped": True,
            "stage": "sync_disabled",
            "run_status": "not_synced",
            "enrichment_completion": completion,
        }
    if not args.dry_run:
        if not getattr(args, "feishu_preflight_done", False):
            try:
                ensure_user_identity(config)
            except RuntimeError as exc:
                return {
                    "ok": False,
                    "stage": "feishu_auth_preflight",
                    "run_status": "blocked_auth",
                    "message": "飞书真实写入前置检查失败；本地候选和补抓队列已保留，可刷新登录后续跑同一命令。",
                    "error": str(exc),
                }
    return sync_posts(
        config,
        posts,
        "all_posts",
        "append",
        args.dry_run,
        audit=not args.strict_ready_only,
        partial=False,
        conn=conn,
    )


def summarize_job_status(
    *,
    preflight: dict[str, Any],
    discover_import: dict[str, Any] | None,
    worker_passes: list[dict[str, Any]],
    sync_result: dict[str, Any],
    completion: dict[str, Any],
) -> str:
    if not preflight.get("ok"):
        return "blocked_opencli"
    if sync_result.get("run_status") == "blocked_auth":
        return "blocked_auth"
    if completion.get("requires_codex_summary_count"):
        return "needs_codex_summary"
    if completion.get("coverage_incomplete_count"):
        return "coverage_incomplete"
    if completion.get("has_incomplete_enrichment"):
        return "incomplete_pending_tasks"
    if sync_result.get("ok") and not sync_result.get("skipped"):
        return "complete"
    if discover_import and discover_import.get("ok"):
        return "captured_not_synced"
    if worker_passes:
        return "resumed_not_synced"
    return "no_work"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--account-url", required=True)
    parser.add_argument("--account-name", default="")
    parser.add_argument("--account-type", default="competitor")
    parser.add_argument("--target-date", default="")
    parser.add_argument("--last-hours", type=int, default=24)
    parser.add_argument("--resume-only", action="store_true", help="Skip homepage discovery and resume SQLite enrichment/sync only.")
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--strict-ready-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-resume-passes", type=int, default=2)
    parser.add_argument("--status-only", action="store_true", help="Report resumable status and optional dry-run sync without running enrichment.")
    parser.add_argument("--enrichment-limit", type=int, default=50)
    parser.add_argument("--max-text", type=int, default=1500)
    parser.add_argument("--max-snapshots", type=int, default=20)
    args = parser.parse_args()

    started = time.monotonic()
    config = load_config(args.config)
    timezone_name = str(config.get("timezone") or "Asia/Shanghai")
    target_dates = [normalize_date_text(args.target_date)] if args.target_date else dates_for_last_hours(args.last_hours, timezone_name=timezone_name)

    conn = connect(config.get("database_path", "data/posts.sqlite"))
    feishu_auth_preflight = {"ok": True, "skipped": True}
    if args.sync and not args.dry_run:
        current_posts = scoped_posts(
            conn,
            account_name=args.account_name,
            account_url=args.account_url,
            account_type=args.account_type,
            dates=target_dates,
        )
        try:
            auth_payload = ensure_user_identity(config)
            feishu_auth_preflight = {
                "ok": True,
                "identity": auth_payload.get("identity"),
                "tokenStatus": auth_payload.get("tokenStatus"),
                "userName": auth_payload.get("userName"),
                "auth_recovery": auth_payload.get("_auth_recovery", {}),
            }
            setattr(args, "feishu_preflight_done", True)
        except RuntimeError as exc:
            completion = enrichment_completion_summary(conn, current_posts)
            print(
                json.dumps(
                    {
                        "ok": False,
                        "run_status": "blocked_auth",
                        "complete": False,
                        "message": "飞书真实写入前置检查失败；已在 Facebook 采集和补抓前停止。修复登录后可用同一命令续跑。",
                        "target_dates": target_dates,
                        "account_url": args.account_url,
                        "account_name": args.account_name,
                        "account_type": args.account_type,
                        "feishu_auth_preflight": {
                            "ok": False,
                            "stage": "feishu_auth_preflight",
                            "error": str(exc),
                        },
                        "enrichment_completion": completion,
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
    opencli_preflight = {"ok": True, "skipped": True}
    discover_import: dict[str, Any] | None = None
    if not args.resume_only:
        opencli_preflight = check_opencli(
            config.get("opencli_command") or [config.get("opencli_path", "opencli")],
            daemon_port=int(config.get("opencli_daemon_port", 19825) or 19825),
            auto_fix=True,
        )
        if not opencli_preflight.get("ok"):
            current_posts = scoped_posts(
                conn,
                account_name=args.account_name,
                account_url=args.account_url,
                account_type=args.account_type,
                dates=target_dates,
            )
            completion = enrichment_completion_summary(conn, current_posts)
            print(
                json.dumps(
                    {
                        "ok": False,
                        "run_status": "blocked_opencli",
                        "message": "OpenCLI Browser Bridge 未就绪；已在 Facebook 实时采集前停止。可修复后用同一命令续跑。",
                        "target_dates": target_dates,
                        "opencli_browser_bridge": opencli_preflight,
                        "enrichment_completion": completion,
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        discover_import = discover_and_import(args, target_dates=target_dates)
        if not discover_import.get("ok"):
            print(
                json.dumps(
                    {
                        "ok": False,
                        "run_status": discover_import.get("stage") or "discover_failed",
                        "target_dates": target_dates,
                        "discover_import": discover_import,
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1

    posts = scoped_posts(
        conn,
        account_name=args.account_name,
        account_url=args.account_url,
        account_type=args.account_type,
        dates=target_dates,
    )
    enqueue_enrichment_tasks_for_posts(conn, posts)
    worker_passes: list[dict[str, Any]] = []
    resume_passes = 0 if args.status_only else max(0, args.max_resume_passes)
    for index in range(resume_passes):
        completion_before = enrichment_completion_summary(conn, posts)
        if not completion_before.get("open_task_count"):
            break
        worker_passes.append(run_worker_pass(args, target_dates=target_dates, pass_index=index + 1))
        posts = scoped_posts(
            conn,
            account_name=args.account_name,
            account_url=args.account_url,
            account_type=args.account_type,
            dates=target_dates,
        )
        enqueue_enrichment_tasks_for_posts(conn, posts)

    posts = scoped_posts(
        conn,
        account_name=args.account_name,
        account_url=args.account_url,
        account_type=args.account_type,
        dates=target_dates,
    )
    sync_result = run_sync(config, args, posts, conn)
    completion = enrichment_completion_summary(conn, posts)
    run_status = summarize_job_status(
        preflight=opencli_preflight,
        discover_import=discover_import,
        worker_passes=worker_passes,
        sync_result=sync_result,
        completion=completion,
    )
    result = {
        "ok": bool(sync_result.get("ok", True)),
        "run_status": run_status,
        "complete": run_status == "complete",
        "target_dates": target_dates,
        "account_url": args.account_url,
        "account_name": args.account_name,
        "account_type": args.account_type,
        "post_count": len(posts),
        "task_counts": task_counts_for_posts(conn, posts),
        "feishu_auth_preflight": feishu_auth_preflight,
        "opencli_preflight": opencli_preflight,
        "discover_import": discover_import,
        "worker_passes": worker_passes,
        "feishu_sync": sync_result,
        "enrichment_completion": completion,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if sync_result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
