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
from coverage_expectations import apply_expected_coverage, split_expected_labels
from lark_io import ensure_user_identity
from models import normalize_date
from store import (
    connect,
    enqueue_enrichment_tasks_for_posts,
    query_posts,
    recover_stale_running_tasks_for_posts,
    task_counts_for_posts,
)
from sync_feishu import sync_posts
from sync_status import completion_run_status, enrichment_completion_summary, has_auto_enrichment_work


ROOT = Path(__file__).resolve().parents[1]
ENRICHMENT_STAGES = "detail_time,lead_link,engagement,post_type,article_material"
HUMAN_INTERVENTION_STATUSES = {"human_intervention_required", "login_required", "visitor_preview", "facebook_tab_missing"}
DEFAULT_RESUME_STALE_RUNNING_SECONDS = 1800


def run_command(command: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False, timeout=timeout)


def parse_json_output(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}


def needs_human_intervention(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("human_intervention_required") or payload.get("action_required") == "human_intervention_required":
        return True
    if str(payload.get("status") or payload.get("run_status") or "") in HUMAN_INTERVENTION_STATUSES:
        return True
    nested = payload.get("discover")
    if isinstance(nested, dict) and needs_human_intervention(nested):
        return True
    nested = payload.get("result")
    if isinstance(nested, dict) and needs_human_intervention(nested):
        return True
    return False


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
            include_unknown_date=bool(account_url or account_name),
            account_name=account_name,
            account_url=account_url,
            account_type=account_type,
        ):
            key = post.get("canonical_post_url") or post.get("post_url")
            if key:
                by_key[str(key)] = post
    if not by_key and account_url:
        for post in query_posts(conn, account_url=account_url, account_type=account_type):
            if dates and post.get("posted_date") and post.get("posted_date") not in set(dates):
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
            stage = "human_intervention_required" if needs_human_intervention(discover_payload) else "discover"
            return {
                "ok": False,
                "stage": stage,
                "human_intervention_required": needs_human_intervention(discover_payload),
                "elapsed_ms": int((time.monotonic() - discover_started) * 1000),
                "discover": discover_payload,
            }
        expected_labels = split_expected_labels(getattr(args, "expected_labels", ""))
        discover_payload = apply_expected_coverage(
            discover_payload,
            expected_post_count=int(getattr(args, "expected_post_count", 0) or 0),
            expected_labels=expected_labels,
        )
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
                "expected_coverage": (discover_payload.get("coverage") or {}).get("expected", {}),
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
    human_intervention = [item for item in results if needs_human_intervention(item)]
    return {
        "ok": not failed and not human_intervention,
        "pass": pass_index,
        "human_intervention_required": bool(human_intervention),
        "human_intervention_reasons": [
            reason
            for item in human_intervention
            for reason in (item.get("human_intervention_reasons") or [item.get("reason") or item.get("run_status") or item.get("status")])
            if reason
        ][:10],
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


def discover_has_incomplete_coverage(discover_import: dict[str, Any] | None) -> bool:
    if not discover_import:
        return False
    discover = discover_import.get("discover") if isinstance(discover_import, dict) else {}
    if not isinstance(discover, dict):
        return False
    coverage = discover.get("coverage") if isinstance(discover.get("coverage"), dict) else {}
    if discover.get("coverage_blocked") or discover.get("coverage_incomplete"):
        return True
    if coverage.get("coverage_blocked") or coverage.get("coverage_incomplete"):
        return True
    return discover.get("capture_complete") is False or coverage.get("capture_complete") is False


def discover_coverage_summary(discover_import: dict[str, Any] | None) -> dict[str, Any]:
    if not discover_import:
        return {"source": "not_run", "complete": True, "incomplete": False, "reasons": []}
    discover = discover_import.get("discover") if isinstance(discover_import, dict) else {}
    if not isinstance(discover, dict):
        return {"source": "unknown", "complete": False, "incomplete": True, "reasons": ["missing_discover_report"]}
    coverage = discover.get("coverage") if isinstance(discover.get("coverage"), dict) else {}
    reasons: list[str] = []
    if needs_human_intervention(discover_import):
        reasons.append("human_intervention_required")
    if discover_import.get("ok") is False:
        reasons.append("discover_failed_before_import")
    if discover.get("coverage_blocked") or coverage.get("coverage_blocked"):
        reasons.append("coverage_blocked")
    if discover.get("coverage_incomplete") or coverage.get("coverage_incomplete"):
        reasons.append("coverage_incomplete")
    if discover.get("capture_complete") is False or coverage.get("capture_complete") is False:
        reasons.append("capture_incomplete")
    return {
        "source": "discover",
        "complete": not reasons,
        "incomplete": bool(reasons),
        "reasons": sorted(set(reasons)),
        "message": coverage.get("message") or "",
        "expected": coverage.get("expected") or {},
        "stop_reason": coverage.get("stop_reason") or "",
        "raw_candidate_count": discover.get("raw_candidate_count", 0),
        "post_count": discover.get("post_count", 0),
    }


def shell_quote(value: Any) -> str:
    text = str(value)
    if not text:
        return "''"
    safe = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./:=@%+-"
    if all(char in safe for char in text):
        return text
    return "'" + text.replace("'", "'\"'\"'") + "'"


def command_text(parts: list[Any]) -> str:
    return " ".join(shell_quote(part) for part in parts)


def resume_command(base: list[Any], primary_date: str, *, force_recover_running: bool = False) -> list[Any]:
    command = list(base)
    if primary_date:
        command.extend(["--target-date", primary_date])
    command.append("--resume-only")
    if force_recover_running:
        command.append("--force-recover-running")
    return command


def full_capture_command(
    base: list[Any],
    primary_date: str,
    args: argparse.Namespace,
    *,
    max_snapshots: int | None = None,
) -> list[Any]:
    command = list(base)
    if primary_date:
        command.extend(["--target-date", primary_date])
    snapshot_budget = max_snapshots if max_snapshots is not None else getattr(args, "max_snapshots", None)
    if snapshot_budget:
        command.extend(["--max-snapshots", str(snapshot_budget)])
    if getattr(args, "expected_post_count", 0):
        command.extend(["--expected-post-count", str(args.expected_post_count)])
    if getattr(args, "expected_labels", ""):
        command.extend(["--expected-labels", args.expected_labels])
    return command


def discover_blocked_before_import(discover_coverage: dict[str, Any]) -> bool:
    reasons = set(discover_coverage.get("reasons") or [])
    return "human_intervention_required" in reasons or "discover_failed_before_import" in reasons


def next_commands_for_status(
    *,
    args: argparse.Namespace,
    target_dates: list[str],
    run_status: str,
    completion: dict[str, Any],
    discover_coverage: dict[str, Any],
) -> list[dict[str, Any]]:
    base = [
        "python3",
        "scripts/run_account_job.py",
        "--config",
        args.config,
        "--account-url",
        args.account_url,
        "--account-type",
        args.account_type,
    ]
    if args.account_name:
        base.extend(["--account-name", args.account_name])
    if args.sync:
        base.append("--sync")
    if args.dry_run:
        base.append("--dry-run")
    if getattr(args, "strict_ready_only", False):
        base.append("--strict-ready-only")
    commands: list[dict[str, Any]] = []
    primary_date = target_dates[-1] if target_dates else ""
    if run_status == "coverage_incomplete":
        command = full_capture_command(
            base,
            primary_date,
            args,
            max_snapshots=max(int(args.max_snapshots or 0) + 12, 32),
        )
        expected = discover_coverage.get("expected") if isinstance(discover_coverage, dict) else {}
        expected_message = expected.get("message") if isinstance(expected, dict) else ""
        commands.append(
            {
                "reason": "coverage_incomplete",
                "description": "从账号主页顶部重跑采集，提高快照预算，并保留人工期望覆盖检查。"
                + (f" 当前缺口：{expected_message}" if expected_message else ""),
                "command": command_text(command),
            }
        )
    if run_status == "no_work":
        commands.append(
            {
                "reason": "no_local_work",
                "description": "当前范围没有可续跑的本地候选；从账号主页顶部重新发现候选并继续补抓/同步。",
                "command": command_text(full_capture_command(base, primary_date, args)),
            }
        )
    has_auto_work = has_auto_enrichment_work(completion)
    if run_status in {"coverage_incomplete", "incomplete_pending_tasks", "synced_ledger_incomplete"} or has_auto_work:
        command = resume_command(base, primary_date, force_recover_running=True)
        command.extend(
            [
                "--max-resume-passes",
                str(max(int(args.max_resume_passes or 0), 2)),
            ]
        )
        commands.append(
            {
                "reason": "pending_enrichment",
                "description": "继续同账号同日期的 SQLite 补抓队列，不重新发现主页；同时恢复上次中断遗留的 running 任务。",
                "command": command_text(command),
            }
        )
    if run_status == "needs_codex_summary" or (completion.get("requires_codex_summary_count") and not has_auto_work):
        output = f"exports/summary_requests_{primary_date or 'current'}.json"
        command = [
            "python3",
            "scripts/export_summary_requests.py",
            "--config",
            args.config,
            "--output",
            output,
        ]
        if primary_date:
            command.extend(["--date", primary_date])
        if args.account_name:
            command.extend(["--account-name", args.account_name])
        if args.account_url:
            command.extend(["--account-url", args.account_url])
        if args.account_type:
            command.extend(["--account-type", args.account_type])
        commands.append(
            {
                "reason": "needs_codex_summary",
                "description": "导出需要 Codex 中文概要的文章材料。",
                "command": command_text(command),
            }
        )
    if run_status == "blocked_auth":
        if getattr(args, "resume_only", False):
            command = resume_command(base, primary_date, force_recover_running=True)
            description = "完成飞书用户授权后，继续同账号同日期的本地补抓/同步队列。"
        else:
            command = full_capture_command(base, primary_date, args)
            description = "完成飞书用户授权后，重新从账号主页顶部发现候选，再继续补抓和同步。"
        commands.append(
            {
                "reason": "blocked_auth",
                "description": description,
                "command": command_text(command),
            }
        )
    if run_status == "blocked_opencli":
        commands.append(
            {
                "reason": "blocked_opencli",
                "description": "先检查并尝试修复 OpenCLI Browser Bridge。",
                "command": command_text(["python3", "scripts/check_env.py", "--config", args.config, "--fix-opencli"]),
            }
        )
        commands.append(
            {
                "reason": "rerun_full_capture",
                "description": "OpenCLI Browser Bridge 恢复后，从账号主页顶部重新发现候选并继续补抓/同步。",
                "command": command_text(full_capture_command(base, primary_date, args)),
            }
        )
    if run_status == "human_intervention_required":
        if discover_blocked_before_import(discover_coverage) and not getattr(args, "resume_only", False):
            command = full_capture_command(base, primary_date, args)
            description = "登录/Profile/主页可见性恢复后，从账号主页顶部重新发现候选并继续补抓/同步。"
        else:
            command = resume_command(base, primary_date, force_recover_running=True)
            description = "先在正常 Chrome 里确认 Facebook 已登录、账号主页帖子列表可见，再从本地 SQLite 续跑剩余补抓和同步。"
        commands.append(
            {
                "reason": "human_intervention_required",
                "description": description,
                "command": command_text(command),
            }
        )
    return commands[:4]


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
    if discover_import and needs_human_intervention(discover_import):
        return "human_intervention_required"
    if any(worker_pass.get("human_intervention_required") for worker_pass in worker_passes):
        return "human_intervention_required"
    if discover_has_incomplete_coverage(discover_import):
        return "coverage_incomplete"
    if completion.get("coverage_incomplete_count"):
        return "coverage_incomplete"
    completion_status = completion_run_status(completion, ledger_mode=False)
    if completion_status != "complete":
        return completion_status
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
    parser.add_argument(
        "--resume-stale-running-seconds",
        type=int,
        default=DEFAULT_RESUME_STALE_RUNNING_SECONDS,
        help="Recover scoped running enrichment tasks older than this before resume passes.",
    )
    parser.add_argument(
        "--force-recover-running",
        action="store_true",
        help="Immediately recover scoped running enrichment tasks from a known interrupted previous run.",
    )
    parser.add_argument("--max-text", type=int, default=1500)
    parser.add_argument("--max-snapshots", type=int, default=20)
    parser.add_argument("--expected-post-count", type=int, default=0)
    parser.add_argument("--expected-labels", default="", help="Comma-separated visible relative-time labels from the operator checklist.")
    parser.add_argument(
        "--fail-on-incomplete",
        action="store_true",
        help="Return a nonzero exit code when run_status is not complete, even if ledger sync itself succeeded.",
    )
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
            run_status = "blocked_auth"
            partial_result = {
                "ok": False,
                "run_status": run_status,
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
            }
            partial_result["next_commands"] = next_commands_for_status(
                args=args,
                target_dates=target_dates,
                run_status=run_status,
                completion=completion,
                discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
            )
            print(
                json.dumps(
                    partial_result,
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
            run_status = "blocked_opencli"
            partial_result = {
                "ok": False,
                "run_status": run_status,
                "message": "OpenCLI Browser Bridge 未就绪；已在 Facebook 实时采集前停止。可修复后用同一命令续跑。",
                "target_dates": target_dates,
                "opencli_browser_bridge": opencli_preflight,
                "enrichment_completion": completion,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
            partial_result["next_commands"] = next_commands_for_status(
                args=args,
                target_dates=target_dates,
                run_status=run_status,
                completion=completion,
                discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
            )
            print(
                json.dumps(
                    partial_result,
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        discover_import = discover_and_import(args, target_dates=target_dates)
        if not discover_import.get("ok"):
            current_posts = scoped_posts(
                conn,
                account_name=args.account_name,
                account_url=args.account_url,
                account_type=args.account_type,
                dates=target_dates,
            )
            completion = enrichment_completion_summary(conn, current_posts)
            run_status = (
                "human_intervention_required"
                if needs_human_intervention(discover_import)
                else discover_import.get("stage") or "discover_failed"
            )
            partial_result = {
                "ok": False,
                "run_status": run_status,
                "complete": False,
                "message": "Facebook 页面需要人工处理登录态或可见页面后再续跑。"
                if run_status == "human_intervention_required"
                else "Facebook 主页发现阶段失败；本地已有结果未丢失，可按 next_commands 排查或续跑。",
                "target_dates": target_dates,
                "account_url": args.account_url,
                "account_name": args.account_name,
                "account_type": args.account_type,
                "discover_import": discover_import,
                "enrichment_completion": completion,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
            partial_result["next_commands"] = next_commands_for_status(
                args=args,
                target_dates=target_dates,
                run_status=run_status,
                completion=completion,
                discover_coverage=discover_coverage_summary(discover_import),
            )
            print(
                json.dumps(
                    partial_result,
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
    stale_running_seconds = 0 if args.force_recover_running else max(0, int(args.resume_stale_running_seconds or 0))
    recovered_running_tasks = recover_stale_running_tasks_for_posts(
        conn,
        posts,
        stale_running_seconds=stale_running_seconds,
    )
    enqueue_enrichment_tasks_for_posts(conn, posts)
    worker_passes: list[dict[str, Any]] = []
    resume_passes = 0 if args.status_only else max(0, args.max_resume_passes)
    for index in range(resume_passes):
        completion_before = enrichment_completion_summary(conn, posts)
        if not completion_before.get("open_task_count"):
            break
        worker_pass = run_worker_pass(args, target_dates=target_dates, pass_index=index + 1)
        worker_passes.append(worker_pass)
        if worker_pass.get("human_intervention_required"):
            break
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
        "recovered_running_tasks": recovered_running_tasks,
        "discover_coverage": discover_coverage_summary(discover_import),
        "feishu_auth_preflight": feishu_auth_preflight,
        "opencli_preflight": opencli_preflight,
        "discover_import": discover_import,
        "worker_passes": worker_passes,
        "feishu_sync": sync_result,
        "enrichment_completion": completion,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
    result["next_commands"] = next_commands_for_status(
        args=args,
        target_dates=target_dates,
        run_status=run_status,
        completion=completion,
        discover_coverage=result["discover_coverage"],
    )
    if args.fail_on_incomplete and run_status != "complete" and sync_result.get("ok", True):
        result["exit_status_reason"] = "incomplete_run_status"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.fail_on_incomplete and run_status != "complete":
        return 2
    return 0 if sync_result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
