#!/usr/bin/env python3
"""Run resumable account jobs for every configured target account."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from typing import Any

from config_loader import load_config
from lark_io import ensure_user_identity
from read_accounts import read_accounts


ACCOUNT_HARD_BLOCKERS = {"blocked_auth", "blocked_opencli", "human_intervention_required"}
MAX_AUTO_FOLLOW_ATTEMPTS = 50
STAGE_REMAINING_WEIGHTS = {
    "coverage": 700,
    "detail_time": 600,
    "lead_link": 500,
    "engagement": 400,
    "post_type": 300,
    "article_material": 200,
    "summary": 100,
}
FIELD_REASON_STAGES = {
    "exact_time": "detail_time",
    "lead_link": "lead_link",
    "likes": "engagement",
    "comments": "engagement",
    "shares": "engagement",
    "likes_low": "engagement",
    "post_type": "post_type",
    "article_summary": "summary",
    "coverage": "coverage",
}
AUTO_FOLLOW_REASONS = {
    "coverage_incomplete",
    "pending_enrichment",
    "needs_codex_summary",
    "summary_auto_apply_failed",
    "quality_threshold_failed",
    "sync_failed",
    "quality_gate",
    "audit_output_gate",
    "partial_gate",
}


def command_text(command: list[Any]) -> str:
    return " ".join(shlex.quote(str(part)) for part in command)


def run_command(command: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False, timeout=timeout)


def parse_json_output(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        payload = json.loads(result.stdout)
        return payload if isinstance(payload, dict) else {"ok": False, "stdout": result.stdout, "stderr": result.stderr}
    except json.JSONDecodeError:
        return {
            "ok": False,
            "run_status": "account_job_failed",
            "complete": False,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }


def opencli_command(config: dict[str, Any]) -> list[str]:
    command = config.get("opencli_command")
    if isinstance(command, list) and command:
        return [str(item) for item in command]
    return [str(config.get("opencli_path") or "opencli")]


def run_opencli(config: dict[str, Any], args: list[str]) -> subprocess.CompletedProcess[str]:
    return run_command([*opencli_command(config), *args])


def prepare_account_tab(config: dict[str, Any], account: dict[str, Any], *, enabled: bool = True) -> dict[str, Any]:
    if not enabled:
        return {"ok": True, "skipped": True, "reason": "disabled"}
    account_url = str(account.get("account_url") or "").strip()
    if not account_url:
        return {"ok": False, "stage": "open_account_tab", "error": "missing account_url"}
    session = str(config.get("opencli_session") or "fb-competitor")
    result = run_opencli(config, ["browser", session, "tab", "new", account_url])
    payload = parse_json_output(result)
    ok = bool(result.returncode == 0 and payload.get("page"))
    return {
        "ok": ok,
        "stage": "open_account_tab",
        "returncode": result.returncode,
        "account_url": account_url,
        "tab": {
            "page": payload.get("page") or "",
            "url": account_url,
        },
        "error": "" if ok else (result.stderr or result.stdout or "OpenCLI tab open failed"),
    }


def close_account_tab(config: dict[str, Any], tab: dict[str, Any] | None) -> dict[str, Any]:
    page = str((tab or {}).get("page") or "").strip()
    if not page:
        return {"ok": True, "skipped": True, "reason": "no_tab"}
    session = str(config.get("opencli_session") or "fb-competitor")
    result = run_opencli(config, ["browser", session, "tab", "close", page])
    return {
        "ok": result.returncode == 0,
        "stage": "close_account_tab",
        "returncode": result.returncode,
        "tab": {"page": page, "url": (tab or {}).get("url") or ""},
        "error": "" if result.returncode == 0 else (result.stderr or result.stdout or "OpenCLI tab close failed"),
    }


def enabled_accounts(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for account in accounts:
        if account.get("enabled") is False:
            continue
        if not str(account.get("account_url") or "").strip():
            continue
        result.append(account)
    return result


def filter_accounts(
    accounts: list[dict[str, Any]],
    *,
    account_type: str = "",
    account_url: str = "",
    account_name: str = "",
    limit: int = 0,
) -> list[dict[str, Any]]:
    filtered = enabled_accounts(accounts)
    if account_type:
        filtered = [account for account in filtered if str(account.get("account_type") or "") == account_type]
    if account_url:
        filtered = [account for account in filtered if str(account.get("account_url") or "") == account_url]
    if account_name:
        needle = account_name.lower()
        filtered = [account for account in filtered if needle in str(account.get("account_name") or "").lower()]
    if limit > 0:
        filtered = filtered[:limit]
    return filtered


def account_job_command(args: argparse.Namespace, account: dict[str, Any]) -> list[str]:
    command = [
        os.environ.get("PYTHON") or sys.executable,
        "scripts/run_account_job.py",
        "--config",
        args.config,
        "--account-url",
        str(account.get("account_url") or ""),
        "--account-name",
        str(account.get("account_name") or ""),
        "--account-type",
        str(account.get("account_type") or "competitor"),
        "--max-snapshots",
        str(args.max_snapshots),
        "--min-snapshots",
        str(args.min_snapshots),
        "--max-resume-passes",
        str(args.max_resume_passes),
        "--enrichment-limit",
        str(args.enrichment_limit),
        "--resume-stale-running-seconds",
        str(args.resume_stale_running_seconds),
    ]
    if args.target_date:
        command.extend(["--target-date", args.target_date])
    else:
        command.extend(["--last-hours", str(args.last_hours)])
    if args.resume_only:
        command.append("--resume-only")
    if args.force_recover_running:
        command.append("--force-recover-running")
    if args.status_only:
        command.append("--status-only")
    if args.sync:
        command.append("--sync")
    if args.dry_run:
        command.append("--dry-run")
    if not args.allow_incomplete_success:
        command.append("--fail-on-incomplete")
    if args.allow_incomplete_success:
        command.append("--allow-incomplete-success")
    if args.require_coverage_complete:
        command.append("--require-coverage-complete")
    threshold_args = {
        "--min-ledger-usable-rate": args.min_ledger_usable_rate,
        "--min-final-usable-rate": args.min_final_usable_rate,
        "--min-completion-rate": args.min_completion_rate,
        "--min-expected-post-coverage-rate": args.min_expected_post_coverage_rate,
        "--min-expected-label-coverage-rate": args.min_expected_label_coverage_rate,
    }
    for flag, value in threshold_args.items():
        if float(value or 0.0) > 0:
            command.extend([flag, str(value)])
    return command


def summarize_account_result(payload: dict[str, Any], *, returncode: int) -> dict[str, Any]:
    quality_summary = payload.get("quality_summary") if isinstance(payload.get("quality_summary"), dict) else {}
    result = {
        "account_name": payload.get("account_name") or "",
        "account_url": payload.get("account_url") or "",
        "account_type": payload.get("account_type") or "",
        "ok": payload.get("ok"),
        "returncode": returncode,
        "run_status": payload.get("run_status") or "account_job_failed",
        "complete": bool(payload.get("complete")),
        "post_count": int(payload.get("post_count") or 0),
        "coverage_health": quality_summary.get("coverage_health") or "",
        "ledger_candidate_count": int(quality_summary.get("ledger_candidate_count") or 0),
        "final_usable_count": int(quality_summary.get("final_usable_count") or 0),
        "final_usable_rate": float(quality_summary.get("final_usable_rate") or 0.0),
        "open_task_count": int(quality_summary.get("open_task_count") or 0),
        "open_task_stage_counts": quality_summary.get("open_task_stage_counts") if isinstance(quality_summary.get("open_task_stage_counts"), dict) else {},
        "missing_stage_counts": quality_summary.get("missing_stage_counts") if isinstance(quality_summary.get("missing_stage_counts"), dict) else {},
        "top_field_gaps": quality_summary.get("top_field_gaps") if isinstance(quality_summary.get("top_field_gaps"), list) else [],
        "completion_blockers": payload.get("completion_blockers") if isinstance(payload.get("completion_blockers"), list) else [],
        "next_commands": payload.get("next_commands") if isinstance(payload.get("next_commands"), list) else [],
    }
    if not payload.get("account_url") and isinstance(payload.get("stderr"), str):
        result["error"] = payload.get("stderr")[:500]
    return result


def command_for_current_account(command_text: str, account: dict[str, Any]) -> bool:
    if not command_text:
        return False
    try:
        parts = shlex.split(command_text)
    except ValueError:
        return False
    if "scripts/run_account_job.py" not in parts:
        return False
    if "--account-url" not in parts:
        return False
    try:
        return parts[parts.index("--account-url") + 1] == str(account.get("account_url") or "")
    except (IndexError, ValueError):
        return False


def next_auto_follow_command(summary: dict[str, Any], account: dict[str, Any]) -> list[str]:
    if summary.get("complete") or str(summary.get("run_status") or "") in ACCOUNT_HARD_BLOCKERS:
        return []
    for item in summary.get("next_commands") or []:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason") or "")
        command_text = str(item.get("command") or "")
        if reason not in AUTO_FOLLOW_REASONS:
            continue
        if not command_for_current_account(command_text, account):
            continue
        try:
            command = shlex.split(command_text)
        except ValueError:
            return []
        if command and command[0] in {"python", "python3"}:
            command[0] = os.environ.get("PYTHON") or sys.executable
        return command
    return []


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def stage_remaining_score(summary: dict[str, Any]) -> int:
    counts: dict[str, int] = {}
    for source in (summary.get("missing_stage_counts"), summary.get("open_task_stage_counts")):
        if not isinstance(source, dict):
            continue
        for stage, count in source.items():
            text_stage = str(stage or "")
            if not text_stage:
                continue
            counts[text_stage] = max(counts.get(text_stage, 0), _int_value(count))
    for item in summary.get("top_field_gaps") or []:
        if not isinstance(item, dict):
            continue
        stage = str(item.get("stage") or FIELD_REASON_STAGES.get(str(item.get("reason") or ""), "") or "")
        if not stage:
            continue
        counts[stage] = max(counts.get(stage, 0), _int_value(item.get("count")))
    return sum(STAGE_REMAINING_WEIGHTS.get(stage, 50) * count for stage, count in counts.items())


def account_progress_key(summary: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
    """Return a monotonic-ish quality key for deciding whether auto-follow is still useful."""

    return (
        int(summary.get("final_usable_count") or 0),
        int(summary.get("ledger_candidate_count") or 0),
        -int(summary.get("open_task_count") or 0),
        -stage_remaining_score(summary),
        int(round(float(summary.get("final_usable_rate") or 0.0) * 10000)),
        int(summary.get("post_count") or 0),
    )


def account_quality_improved(previous: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    if previous is None:
        return False
    return account_progress_key(current) > account_progress_key(previous)


def batch_retry_command(args: argparse.Namespace, *, fix_opencli: bool = False) -> list[Any]:
    command: list[Any] = [
        os.environ.get("PYTHON") or sys.executable,
        "scripts/run_accounts_job.py",
        "--config",
        args.config,
    ]
    if args.target_date:
        command.extend(["--target-date", args.target_date])
    else:
        command.extend(["--last-hours", str(args.last_hours)])
    if args.account_type:
        command.extend(["--account-type", args.account_type])
    if args.account_url:
        command.extend(["--account-url", args.account_url])
    if args.account_name:
        command.extend(["--account-name", args.account_name])
    if int(args.limit or 0) > 0:
        command.extend(["--limit", str(args.limit)])
    if args.resume_only:
        command.append("--resume-only")
    if args.force_recover_running:
        command.append("--force-recover-running")
    if args.sync:
        command.append("--sync")
    if args.dry_run:
        command.append("--dry-run")
    if args.allow_incomplete_success:
        command.append("--allow-incomplete-success")
    if not args.open_account_tabs:
        command.append("--no-open-account-tabs")
    elif fix_opencli:
        command.append("--open-account-tabs")
    command.extend(["--auto-follow-attempts", str(args.auto_follow_attempts)])
    command.extend(["--max-snapshots", str(args.max_snapshots)])
    command.extend(["--min-snapshots", str(args.min_snapshots)])
    command.extend(["--max-resume-passes", str(args.max_resume_passes)])
    command.extend(["--enrichment-limit", str(args.enrichment_limit)])
    command.extend(["--resume-stale-running-seconds", str(args.resume_stale_running_seconds)])
    if args.require_coverage_complete:
        command.append("--require-coverage-complete")
    threshold_args = {
        "--min-ledger-usable-rate": args.min_ledger_usable_rate,
        "--min-final-usable-rate": args.min_final_usable_rate,
        "--min-completion-rate": args.min_completion_rate,
        "--min-expected-post-coverage-rate": args.min_expected_post_coverage_rate,
        "--min-expected-label-coverage-rate": args.min_expected_label_coverage_rate,
    }
    for flag, value in threshold_args.items():
        if float(value or 0.0) > 0:
            command.extend([flag, str(value)])
    return command


def run_account_until_settled(args: argparse.Namespace, account: dict[str, Any]) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    command = account_job_command(args, account)
    base_attempt_limit = 1 if args.status_only else max(1, int(args.auto_follow_attempts or 0))
    hard_attempt_limit = base_attempt_limit if args.status_only else max(base_attempt_limit, MAX_AUTO_FOLLOW_ATTEMPTS)
    final_summary: dict[str, Any] | None = None
    previous_summary: dict[str, Any] | None = None
    extended_after_budget = 0
    for attempt_index in range(1, hard_attempt_limit + 1):
        command_key = subprocess.list2cmdline(command)
        result = run_command(command)
        payload = parse_json_output(result)
        payload.setdefault("account_url", account.get("account_url") or "")
        payload.setdefault("account_name", account.get("account_name") or "")
        payload.setdefault("account_type", account.get("account_type") or "competitor")
        summary = summarize_account_result(payload, returncode=result.returncode)
        summary["command"] = command_key
        attempts.append(
            {
                "attempt": attempt_index,
                "command": command_key,
                "returncode": result.returncode,
                "run_status": summary.get("run_status"),
                "complete": summary.get("complete"),
            }
        )
        final_summary = summary
        improved = account_quality_improved(previous_summary, summary)
        attempts[-1]["quality_progress_key"] = list(account_progress_key(summary))
        if improved:
            attempts[-1]["quality_improved"] = True
        if summary.get("complete") or str(summary.get("run_status") or "") in ACCOUNT_HARD_BLOCKERS:
            break
        if result.returncode not in {0, 2}:
            break
        follow = next_auto_follow_command(summary, account)
        if not follow:
            break
        if attempt_index >= base_attempt_limit:
            if improved and attempt_index < hard_attempt_limit:
                attempts[-1]["auto_follow_extended_after_budget"] = True
                extended_after_budget += 1
            else:
                attempts[-1]["auto_follow_stopped_reason"] = "max_attempts_reached"
                attempts[-1]["next_auto_follow_available"] = True
                if attempt_index >= hard_attempt_limit:
                    attempts[-1]["auto_follow_hard_limit_reached"] = True
                break
        follow_key = subprocess.list2cmdline(follow)
        if follow_key == command_key:
            attempts[-1]["auto_follow_repeated_command"] = True
        previous_summary = summary
        command = follow
    if final_summary is None:
        final_summary = {
            "account_name": account.get("account_name") or "",
            "account_url": account.get("account_url") or "",
            "account_type": account.get("account_type") or "competitor",
            "ok": False,
            "returncode": 1,
            "run_status": "account_job_failed",
            "complete": False,
            "post_count": 0,
            "coverage_health": "",
            "ledger_candidate_count": 0,
            "final_usable_count": 0,
            "final_usable_rate": 0.0,
            "open_task_count": 0,
            "open_task_stage_counts": {},
            "missing_stage_counts": {},
            "top_field_gaps": [],
            "completion_blockers": [],
            "next_commands": [],
            "command": subprocess.list2cmdline(command),
        }
    final_summary["attempts"] = attempts
    final_summary["auto_follow_attempted"] = len(attempts) > 1
    final_summary["auto_follow_attempt_limit"] = base_attempt_limit
    final_summary["auto_follow_hard_attempt_limit"] = hard_attempt_limit
    final_summary["auto_follow_extended_after_budget_count"] = extended_after_budget
    final_summary["auto_follow_exhausted"] = bool(
        attempts and attempts[-1].get("auto_follow_stopped_reason") == "max_attempts_reached"
    )
    return final_summary


def account_open_blocker(account: dict[str, Any], open_result: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    return {
        "account_name": account.get("account_name") or "",
        "account_url": account.get("account_url") or "",
        "account_type": account.get("account_type") or "competitor",
        "ok": False,
        "returncode": int(open_result.get("returncode") or 1),
        "run_status": "blocked_opencli",
        "complete": False,
        "post_count": 0,
        "coverage_health": "",
        "ledger_candidate_count": 0,
        "final_usable_count": 0,
        "final_usable_rate": 0.0,
        "open_task_count": 0,
        "top_field_gaps": [],
        "completion_blockers": [
            {
                "code": "blocked_opencli",
                "label": "OpenCLI账号主页打开失败",
                "severity": "hard_blocker",
                "message": "批量作业无法打开目标 Facebook 账号主页标签页。",
                "next_action": "确认 OpenCLI Browser Bridge 已连接正常 Chrome，且 Facebook 登录态可用后重试批量作业。",
                "metrics": {"open_account_tab": open_result},
            }
        ],
        "next_commands": [
            {
                "reason": "blocked_opencli",
                "description": "OpenCLI Browser Bridge 恢复后重新运行批量账号作业。",
                "command": "python3 scripts/check_env.py --config config/settings.yaml --fix-opencli",
            },
            {
                "reason": "rerun_batch_after_opencli",
                "description": "OpenCLI Browser Bridge 恢复后，按原批量范围重新运行所有目标账号，避免只修环境但忘记继续采集/补抓/同步。",
                "command": command_text(batch_retry_command(args, fix_opencli=True)),
            }
        ],
        "open_account_tab": open_result,
    }


def aggregate_status(account_results: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [item for item in account_results if item.get("complete")]
    hard_blocked = [item for item in account_results if str(item.get("run_status") or "") in ACCOUNT_HARD_BLOCKERS]
    needs_codex_summary = [item for item in account_results if item.get("run_status") == "needs_codex_summary"]
    summary_failed = [item for item in account_results if item.get("run_status") == "summary_auto_apply_failed"]
    incomplete = [
        item
        for item in account_results
        if not item.get("complete") and str(item.get("run_status") or "") not in ACCOUNT_HARD_BLOCKERS
    ]
    status_counts: dict[str, int] = {}
    for item in account_results:
        status = str(item.get("run_status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    all_complete = bool(account_results) and len(completed) == len(account_results)
    if all_complete:
        run_status = "complete"
    elif hard_blocked:
        run_status = "accounts_blocked"
    elif summary_failed:
        run_status = "accounts_summary_failed"
    elif needs_codex_summary:
        run_status = "accounts_need_codex_summary"
    elif incomplete:
        run_status = "accounts_incomplete"
    else:
        run_status = "no_accounts"
    return {
        "run_status": run_status,
        "complete": all_complete,
        "account_status_counts": dict(sorted(status_counts.items())),
        "accounts_completed": len(completed),
        "accounts_hard_blocked": len(hard_blocked),
        "accounts_summary_failed": len(summary_failed),
        "accounts_needing_codex_summary": len(needs_codex_summary),
        "accounts_incomplete": len(incomplete),
        "ledger_candidate_count": sum(int(item.get("ledger_candidate_count") or 0) for item in account_results),
        "final_usable_count": sum(int(item.get("final_usable_count") or 0) for item in account_results),
        "open_task_count": sum(int(item.get("open_task_count") or 0) for item in account_results),
    }


def next_commands_for_batch(account_results: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for account in account_results:
        if account.get("complete"):
            continue
        for item in account.get("next_commands") or []:
            if not isinstance(item, dict) or not item.get("command"):
                continue
            commands.append(
                {
                    "account_url": account.get("account_url") or "",
                    "account_name": account.get("account_name") or "",
                    "account_type": account.get("account_type") or "",
                    "run_status": account.get("run_status") or "",
                    "reason": item.get("reason") or "",
                    "description": item.get("description") or "",
                    "command": item.get("command"),
                }
            )
            if len(commands) >= limit:
                return commands
    return commands


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--target-date", default="")
    parser.add_argument("--last-hours", type=int, default=24)
    parser.add_argument("--account-type", default="")
    parser.add_argument("--account-url", default="")
    parser.add_argument("--account-name", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume-only", action="store_true")
    parser.add_argument("--force-recover-running", action="store_true")
    parser.add_argument("--status-only", action="store_true")
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.set_defaults(open_account_tabs=True)
    parser.add_argument("--open-account-tabs", dest="open_account_tabs", action="store_true")
    parser.add_argument("--no-open-account-tabs", dest="open_account_tabs", action="store_false")
    parser.add_argument("--fail-on-incomplete", action="store_true")
    parser.add_argument(
        "--allow-incomplete-success",
        action="store_true",
        help="Compatibility mode: return 0 and do not force child account jobs to fail when incomplete.",
    )
    parser.add_argument(
        "--auto-follow-attempts",
        type=int,
        default=8,
        help="Maximum run_account_job attempts per account, following machine-runnable next_commands before reporting incomplete.",
    )
    parser.add_argument("--max-snapshots", type=int, default=32)
    parser.add_argument("--min-snapshots", type=int, default=6)
    parser.add_argument("--max-resume-passes", type=int, default=8)
    parser.add_argument("--enrichment-limit", type=int, default=50)
    parser.add_argument("--resume-stale-running-seconds", type=int, default=1800)
    parser.add_argument("--require-coverage-complete", action="store_true")
    parser.add_argument("--min-ledger-usable-rate", type=float, default=0.0)
    parser.add_argument("--min-final-usable-rate", type=float, default=0.0)
    parser.add_argument("--min-completion-rate", type=float, default=0.0)
    parser.add_argument("--min-expected-post-coverage-rate", type=float, default=0.0)
    parser.add_argument("--min-expected-label-coverage-rate", type=float, default=0.0)
    args = parser.parse_args()

    started = time.monotonic()
    config = load_config(args.config)
    feishu_auth_preflight = {"ok": True, "skipped": True}
    if args.sync and not args.dry_run:
        try:
            auth_payload = ensure_user_identity(config)
            feishu_auth_preflight = {
                "ok": True,
                "identity": auth_payload.get("identity"),
                "tokenStatus": auth_payload.get("tokenStatus"),
                "userName": auth_payload.get("userName"),
                "auth_recovery": auth_payload.get("_auth_recovery", {}),
            }
        except RuntimeError as exc:
            retry_command = command_text(batch_retry_command(args))
            print(
                json.dumps(
                    {
                        "ok": False,
                        "run_status": "blocked_auth",
                        "complete": False,
                        "message": "飞书真实写入前置检查失败；已在打开 Facebook 账号主页、采集和补抓前停止。",
                        "feishu_auth_preflight": {
                            "ok": False,
                            "stage": "feishu_auth_preflight",
                            "error": str(exc),
                        },
                        "next_actions": ["完成飞书用户授权后，重新运行同一批量账号作业。"],
                        "next_commands": [
                            {
                                "reason": "blocked_auth",
                                "description": "完成飞书用户授权后，按原批量范围继续采集、补抓和同步。",
                                "command": retry_command,
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
    try:
        accounts = filter_accounts(
            read_accounts(config),
            account_type=args.account_type,
            account_url=args.account_url,
            account_name=args.account_name,
            limit=max(0, int(args.limit or 0)),
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "run_status": "accounts_load_failed",
                    "complete": False,
                    "error": str(exc),
                    "next_actions": ["修复飞书账号源配置、权限或表头后重新运行批量账号作业。"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    account_results: list[dict[str, Any]] = []
    opened_account_tabs: list[tuple[int, dict[str, Any]]] = []
    for account in accounts:
        open_result = prepare_account_tab(config, account, enabled=bool(args.open_account_tabs) and not args.resume_only and not args.status_only)
        if not open_result.get("ok"):
            account_results.append(account_open_blocker(account, open_result, args))
            continue
        if isinstance(open_result.get("tab"), dict) and open_result["tab"].get("page"):
            opened_account_tabs.append((len(account_results), open_result["tab"]))
        account_summary = run_account_until_settled(args, account)
        account_summary["open_account_tab"] = open_result
        account_results.append(account_summary)

    account_tab_cleanup: list[dict[str, Any]] = []
    for account_index, tab in reversed(opened_account_tabs):
        close_result = close_account_tab(config, tab)
        account_tab_cleanup.append(close_result)
        if 0 <= account_index < len(account_results):
            account_results[account_index]["close_account_tab"] = close_result

    aggregate = aggregate_status(account_results)
    payload = {
        "ok": aggregate["complete"],
        "run_status": aggregate["run_status"],
        "complete": aggregate["complete"],
        "account_count": len(account_results),
        "target_date": args.target_date,
        "last_hours": args.last_hours if not args.target_date else None,
        "feishu_auth_preflight": feishu_auth_preflight,
        **aggregate,
        "accounts": account_results,
        "account_tab_cleanup": {
            "opened": len(opened_account_tabs),
            "attempted": len(account_tab_cleanup),
            "closed": sum(1 for item in account_tab_cleanup if item.get("ok")),
            "failed": sum(1 for item in account_tab_cleanup if not item.get("ok")),
            "items": account_tab_cleanup,
        },
        "next_commands": next_commands_for_batch(account_results),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if aggregate["complete"]:
        return 0
    return 0 if args.allow_incomplete_success else 2


if __name__ == "__main__":
    raise SystemExit(main())
