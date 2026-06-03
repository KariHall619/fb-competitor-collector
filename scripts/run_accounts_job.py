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
MACHINE_RECOVERABLE_STATUSES = {
    "coverage_incomplete",
    "incomplete_pending_tasks",
    "needs_codex_summary",
    "summary_auto_apply_failed",
    "captured_not_synced",
    "resumed_not_synced",
    "synced_ledger_incomplete",
    "sync_failed",
    "quality_gate",
    "audit_output_gate",
    "partial_gate",
    "quality_threshold_failed",
    "no_work",
}
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
    "no_local_work",
    "pending_enrichment",
    "prepare_failed",
    "import_failed",
    "needs_codex_summary",
    "summary_auto_apply_failed",
    "captured_not_synced",
    "resumed_not_synced",
    "quality_threshold_failed",
    "sync_failed",
    "quality_gate",
    "audit_output_gate",
    "partial_gate",
}
AUTO_FOLLOW_REASON_PRIORITY = {
    "pending_enrichment": 10,
    "prepare_failed": 18,
    "import_failed": 19,
    "needs_codex_summary": 20,
    "summary_auto_apply_failed": 30,
    "captured_not_synced": 35,
    "resumed_not_synced": 35,
    "sync_failed": 40,
    "quality_gate": 45,
    "audit_output_gate": 46,
    "partial_gate": 47,
    "quality_threshold_failed": 50,
    "coverage_incomplete": 80,
    "no_local_work": 90,
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
    if int(getattr(args, "expected_post_count", 0) or 0) > 0:
        command.extend(["--expected-post-count", str(args.expected_post_count)])
    if getattr(args, "expected_labels", ""):
        command.extend(["--expected-labels", str(args.expected_labels)])
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
    raw_quality_summary = payload.get("quality_summary")
    quality_summary_present = isinstance(raw_quality_summary, dict)
    quality_summary = raw_quality_summary if quality_summary_present else {}
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
    guard_reasons = account_completion_guard_reasons(result, quality_summary_present=quality_summary_present)
    if result["complete"] and guard_reasons:
        result["reported_complete"] = True
        result["reported_run_status"] = result["run_status"]
        result["complete"] = False
        result["completion_guard_reasons"] = guard_reasons
        result["run_status"] = guarded_run_status(guard_reasons)
        result["completion_blockers"] = [
            {
                "code": "batch_completion_guard",
                "label": "批量完成复核未通过",
                "severity": "completion_guard",
                "message": "子账号作业自报 complete，但质量摘要仍显示覆盖、补抓或最终字段缺口；批量入口继续自动恢复，不把该账号计为完成。",
                "next_action": "继续同账号机器可恢复命令，直到 quality_summary 不再有缺口。",
                "metrics": {"reasons": guard_reasons},
            },
            *result["completion_blockers"],
        ]
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


def _set_arg_value(command: list[str], flag: str, value: str) -> None:
    if flag in command:
        try:
            command[command.index(flag) + 1] = value
            return
        except IndexError:
            pass
    command.extend([flag, value])


def _append_flag(command: list[str], flag: str) -> None:
    if flag not in command:
        command.append(flag)


def _remove_flag(command: list[str], flag: str) -> None:
    while flag in command:
        command.pop(command.index(flag))


def _counted_stage(summary: dict[str, Any], stages: set[str]) -> bool:
    for source_key in ("open_task_stage_counts", "missing_stage_counts"):
        source = summary.get(source_key)
        if not isinstance(source, dict):
            continue
        for stage, count in source.items():
            if str(stage or "") in stages and _int_value(count) > 0:
                return True
    for item in summary.get("top_field_gaps") or []:
        if not isinstance(item, dict):
            continue
        stage = str(item.get("stage") or FIELD_REASON_STAGES.get(str(item.get("reason") or ""), "") or "")
        if stage in stages and _int_value(item.get("count")) > 0:
            return True
    return False


def synthesized_resume_command(args: argparse.Namespace, account: dict[str, Any]) -> list[str]:
    command = account_job_command(args, account)
    _append_flag(command, "--resume-only")
    _append_flag(command, "--force-recover-running")
    _set_arg_value(command, "--max-resume-passes", str(max(1, int(args.max_resume_passes or 0))))
    _set_arg_value(command, "--enrichment-limit", str(max(1, int(args.enrichment_limit or 0))))
    return command


def synthesized_full_capture_command(args: argparse.Namespace, account: dict[str, Any], *, increase_snapshot_budget: bool = False) -> list[str]:
    command = account_job_command(args, account)
    if not args.resume_only:
        _remove_flag(command, "--resume-only")
        _remove_flag(command, "--force-recover-running")
    if increase_snapshot_budget:
        next_max = max(int(args.max_snapshots or 0) + 12, 32)
        next_min = max(int(args.min_snapshots or 0), 6)
        _set_arg_value(command, "--max-snapshots", str(next_max))
        _set_arg_value(command, "--min-snapshots", str(next_min))
    return command


def synthesized_auto_follow_command(summary: dict[str, Any], account: dict[str, Any], args: argparse.Namespace) -> list[str]:
    """Create a same-account recovery command when a child omitted next_commands.

    The child account job should normally emit next_commands. This fallback keeps
    batch jobs from handing machine-runnable missing fields back to the operator
    merely because an older or failed child path forgot to include the command.
    """

    if summary.get("complete"):
        return []
    run_status = str(summary.get("run_status") or "")
    if run_status in ACCOUNT_HARD_BLOCKERS or run_status == "worker_failed":
        return []
    if run_status in {"captured_not_synced", "resumed_not_synced"} and not bool(summary.get("requested_sync")):
        return []
    if run_status and run_status not in MACHINE_RECOVERABLE_STATUSES and not summary.get("open_task_count"):
        return []

    auto_stages = {"detail_time", "lead_link", "engagement", "post_type", "article_material", "summary"}
    if _int_value(summary.get("open_task_count")) > 0 or _counted_stage(summary, auto_stages):
        return synthesized_resume_command(args, account)
    if run_status in {"incomplete_pending_tasks", "synced_ledger_incomplete", "needs_codex_summary", "summary_auto_apply_failed"}:
        return synthesized_resume_command(args, account)
    if bool(summary.get("requested_sync")) and run_status in {
        "captured_not_synced",
        "resumed_not_synced",
        "sync_failed",
        "quality_gate",
        "audit_output_gate",
        "partial_gate",
    }:
        return synthesized_resume_command(args, account)
    if str(summary.get("coverage_health") or "") == "incomplete" or run_status == "coverage_incomplete":
        return synthesized_full_capture_command(args, account, increase_snapshot_budget=True)
    if run_status in {"no_work", "quality_threshold_failed"}:
        return synthesized_full_capture_command(args, account)
    return []


def next_auto_follow_command(summary: dict[str, Any], account: dict[str, Any], args: argparse.Namespace) -> list[str]:
    if summary.get("complete") or str(summary.get("run_status") or "") in ACCOUNT_HARD_BLOCKERS:
        return []
    original_requested_sync = bool(summary.get("requested_sync"))
    candidates: list[tuple[int, int, str]] = []
    for item in summary.get("next_commands") or []:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason") or "")
        command_text = str(item.get("command") or "")
        if reason not in AUTO_FOLLOW_REASONS:
            continue
        if reason in {"captured_not_synced", "resumed_not_synced"} and not original_requested_sync:
            continue
        if not command_for_current_account(command_text, account):
            continue
        candidates.append((AUTO_FOLLOW_REASON_PRIORITY.get(reason, 100), len(candidates), command_text))
    if not candidates:
        return synthesized_auto_follow_command(summary, account, args)
    command_text = sorted(candidates, key=lambda item: (item[0], item[1]))[0][2]
    try:
        command = shlex.split(command_text)
    except ValueError:
        return []
    if command and command[0] in {"python", "python3"}:
        command[0] = os.environ.get("PYTHON") or sys.executable
    return command


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _has_positive_counts(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return any(_int_value(count) > 0 for count in value.values())


def _has_positive_field_gaps(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if isinstance(item, dict) and _int_value(item.get("count")) > 0:
            return True
    return False


def account_completion_guard_reasons(summary: dict[str, Any], *, quality_summary_present: bool = True) -> list[str]:
    reasons: list[str] = []
    if not quality_summary_present:
        reasons.append("missing_quality_summary")
        return reasons
    if str(summary.get("coverage_health") or "") == "incomplete":
        reasons.append("coverage_incomplete")
    if _int_value(summary.get("open_task_count")) > 0:
        reasons.append("open_enrichment_tasks")
    if _has_positive_counts(summary.get("open_task_stage_counts")):
        reasons.append("open_stage_counts")
    if _has_positive_counts(summary.get("missing_stage_counts")):
        reasons.append("missing_stage_counts")
    if _has_positive_field_gaps(summary.get("top_field_gaps")):
        reasons.append("field_gaps")
    post_count = _int_value(summary.get("post_count"))
    final_usable_count = _int_value(summary.get("final_usable_count"))
    if post_count > 0 and final_usable_count < post_count:
        reasons.append("final_usable_incomplete")
    if post_count > 0 and _float_value(summary.get("final_usable_rate")) < 1.0:
        reasons.append("final_usable_rate_below_one")
    ordered: list[str] = []
    for reason in reasons:
        if reason not in ordered:
            ordered.append(reason)
    return ordered


def guarded_run_status(reasons: list[str]) -> str:
    if "coverage_incomplete" in reasons:
        return "coverage_incomplete"
    if "missing_quality_summary" in reasons:
        return "account_completion_unverified"
    return "incomplete_pending_tasks"


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
    if int(getattr(args, "expected_post_count", 0) or 0) > 0:
        command.extend(["--expected-post-count", str(args.expected_post_count)])
    if getattr(args, "expected_labels", ""):
        command.extend(["--expected-labels", str(args.expected_labels)])
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
        summary["requested_sync"] = bool(args.sync)
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
        follow = next_auto_follow_command(summary, account, args)
        if not follow:
            if result.returncode not in {0, 2}:
                attempts[-1]["auto_follow_stopped_reason"] = "non_followable_returncode"
                attempts[-1]["non_followable_returncode"] = result.returncode
            break
        if result.returncode not in {0, 2}:
            attempts[-1]["auto_follow_nonstandard_returncode"] = result.returncode
        if attempt_index >= base_attempt_limit:
            if attempt_index < hard_attempt_limit:
                attempts[-1]["auto_follow_extended_after_budget"] = True
                attempts[-1]["auto_follow_extended_reason"] = "followable_next_command"
                if improved:
                    attempts[-1]["auto_follow_extended_quality_improved"] = True
                extended_after_budget += 1
            else:
                attempts[-1]["auto_follow_stopped_reason"] = "max_attempts_reached"
                attempts[-1]["next_auto_follow_available"] = True
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


def _batch_next_command_entry(account: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_url": account.get("account_url") or "",
        "account_name": account.get("account_name") or "",
        "account_type": account.get("account_type") or "",
        "run_status": account.get("run_status") or "",
        "reason": item.get("reason") or "",
        "description": item.get("description") or "",
        "command": item.get("command"),
    }


def _append_batch_next_command(
    commands: list[dict[str, Any]],
    account: dict[str, Any],
    item: dict[str, Any],
    *,
    limit: int,
) -> bool:
    if len(commands) >= limit:
        return True
    commands.append(_batch_next_command_entry(account, item))
    return len(commands) >= limit


def _batch_opencli_recovery_items(args: argparse.Namespace) -> list[dict[str, Any]]:
    return [
        {
            "reason": "blocked_opencli",
            "description": "先检查并尝试修复 OpenCLI Browser Bridge。",
            "command": command_text(["python3", "scripts/check_env.py", "--config", args.config, "--fix-opencli"]),
        },
        {
            "reason": "rerun_batch_after_opencli",
            "description": "OpenCLI Browser Bridge 恢复后，按原批量范围重新运行所有目标账号，避免只续跑单个账号而漏掉后续采集、补抓或同步。",
            "command": command_text(batch_retry_command(args, fix_opencli=True)),
        },
    ]


def _batch_auth_recovery_items(args: argparse.Namespace) -> list[dict[str, Any]]:
    return [
        {
            "reason": "blocked_auth",
            "description": "先检查并尝试修复飞书用户授权。",
            "command": command_text(["python3", "scripts/check_env.py", "--config", args.config, "--fix-auth"]),
        },
        {
            "reason": "rerun_batch_after_auth",
            "description": "飞书用户授权恢复后，按原批量范围重新运行所有目标账号，避免只续跑单个账号而漏掉后续采集、补抓或同步。",
            "command": command_text(batch_retry_command(args)),
        },
    ]


def _append_hard_blocker_commands(
    commands: list[dict[str, Any]],
    account: dict[str, Any],
    args: argparse.Namespace,
    *,
    limit: int,
    blocker_reason: str,
    batch_rerun_reason: str,
    fallback_items: list[dict[str, Any]],
) -> bool:
    child_items = [
        item
        for item in account.get("next_commands") or []
        if isinstance(item, dict) and item.get("command")
    ]
    env_index = next((index for index, item in enumerate(child_items) if str(item.get("reason") or "") == blocker_reason), None)
    batch_index = next((index for index, item in enumerate(child_items) if str(item.get("reason") or "") == batch_rerun_reason), None)
    env_item = child_items[env_index] if env_index is not None else fallback_items[0]
    batch_item = child_items[batch_index] if batch_index is not None else fallback_items[1]
    if _append_batch_next_command(commands, account, env_item, limit=limit):
        return True
    if _append_batch_next_command(commands, account, batch_item, limit=limit):
        return True
    used_child_indexes = {index for index in (env_index, batch_index) if index is not None}
    for index, item in enumerate(child_items):
        if index in used_child_indexes:
            continue
        if _append_batch_next_command(commands, account, item, limit=limit):
            return True
    return len(commands) >= limit


def _append_blocked_opencli_commands(
    commands: list[dict[str, Any]],
    account: dict[str, Any],
    args: argparse.Namespace,
    *,
    limit: int,
) -> bool:
    return _append_hard_blocker_commands(
        commands,
        account,
        args,
        limit=limit,
        blocker_reason="blocked_opencli",
        batch_rerun_reason="rerun_batch_after_opencli",
        fallback_items=_batch_opencli_recovery_items(args),
    )


def _append_blocked_auth_commands(
    commands: list[dict[str, Any]],
    account: dict[str, Any],
    args: argparse.Namespace,
    *,
    limit: int,
) -> bool:
    return _append_hard_blocker_commands(
        commands,
        account,
        args,
        limit=limit,
        blocker_reason="blocked_auth",
        batch_rerun_reason="rerun_batch_after_auth",
        fallback_items=_batch_auth_recovery_items(args),
    )


def synthesized_batch_next_command(account: dict[str, Any], args: argparse.Namespace) -> dict[str, Any] | None:
    if account.get("complete") or str(account.get("run_status") or "") in ACCOUNT_HARD_BLOCKERS:
        return None
    command = next_auto_follow_command(account, account, args)
    if not command:
        return None
    reason = (
        "auto_follow_exhausted"
        if account.get("auto_follow_exhausted")
        else "synthesized_account_recovery"
    )
    return {
        "reason": reason,
        "description": "账号作业仍未完整，但没有可转发的子命令；批量入口按当前状态合成同账号续跑命令，避免等待用户再次提醒。",
        "command": command_text(command),
    }


def next_commands_for_batch(account_results: list[dict[str, Any]], args: argparse.Namespace, *, limit: int = 8) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for account in account_results:
        if account.get("complete"):
            continue
        if str(account.get("run_status") or "") == "blocked_opencli":
            if _append_blocked_opencli_commands(commands, account, args, limit=limit):
                return commands
            continue
        if str(account.get("run_status") or "") == "blocked_auth":
            if _append_blocked_auth_commands(commands, account, args, limit=limit):
                return commands
            continue
        added_for_account = False
        for item in account.get("next_commands") or []:
            if not isinstance(item, dict) or not item.get("command"):
                continue
            commands.append(_batch_next_command_entry(account, item))
            added_for_account = True
            if len(commands) >= limit:
                return commands
        if added_for_account:
            continue
        synthesized = synthesized_batch_next_command(account, args)
        if synthesized:
            commands.append(_batch_next_command_entry(account, synthesized))
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
    parser.add_argument("--expected-post-count", type=int, default=0)
    parser.add_argument("--expected-labels", default="")
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
        "next_commands": next_commands_for_batch(account_results, args),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if aggregate["complete"]:
        return 0
    return 0 if args.allow_incomplete_success else 2


if __name__ == "__main__":
    raise SystemExit(main())
