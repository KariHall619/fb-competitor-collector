#!/usr/bin/env python3
"""Resumable account-level capture, enrichment, and Feishu ledger sync."""

from __future__ import annotations

import argparse
import json
import sqlite3
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
from discovery_retry import attach_auto_retry_report, needs_snapshot_budget_retry, retry_snapshot_budget
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
WORKER_OPERATIONAL_STATUSES = {"complete", "failed", "human_intervention_required", "incomplete_pending_tasks", "needs_codex_summary"}
DEFAULT_RESUME_STALE_RUNNING_SECONDS = 1800
DEFAULT_MAX_RESUME_PASSES = 8
MAX_AUTO_RESUME_PASSES = 20
OPENCLI_REQUIRED_STAGES = {"detail_time", "lead_link", "engagement", "post_type"}
STAGE_LABELS = {
    "detail_time": "精确时间",
    "lead_link": "引流链接",
    "engagement": "互动数据",
    "post_type": "帖子类型",
    "article_material": "文章素材",
    "summary": "文章概要",
    "coverage": "覆盖不足",
}
STAGE_ORDER = {
    "coverage": 0,
    "detail_time": 1,
    "lead_link": 2,
    "engagement": 3,
    "post_type": 4,
    "article_material": 5,
    "summary": 6,
}


def run_command(command: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False, timeout=timeout)


def parse_json_output(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}


def worker_result_is_operational(payload: dict[str, Any]) -> bool:
    """Return True when enrichment_worker returned a structured operational state."""

    status = str(payload.get("run_status") or payload.get("status") or "")
    if status not in WORKER_OPERATIONAL_STATUSES:
        return False
    return payload.get("returncode") in {0, 1, 2}


def worker_failure_reasons(payload: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    status = str(payload.get("run_status") or payload.get("status") or "").strip()
    if status and status not in WORKER_OPERATIONAL_STATUSES:
        reasons.append(f"unknown_status:{status}")
    if not status:
        reasons.append("non_json_worker_output")
    for key in ("error", "stderr", "stdout"):
        text = str(payload.get(key) or "").strip()
        if not text:
            continue
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        if first_line and first_line not in reasons:
            reasons.append(first_line[:240])
    if not reasons:
        reasons.append(str(payload.get("run_status") or payload.get("status") or "worker_failed"))
    return reasons[:5]


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


def discover_homepage_once(
    args: argparse.Namespace,
    *,
    max_snapshots: int,
    min_snapshots: int,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any], int]:
    started = time.monotonic()
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
            str(max_snapshots),
            "--min-snapshots",
            str(min_snapshots),
        ]
    )
    payload = parse_json_output(discover)
    payload["returncode"] = discover.returncode
    payload["snapshot_budget"] = {
        "max_snapshots": int(max_snapshots),
        "min_snapshots": int(min_snapshots),
    }
    return discover, payload, int((time.monotonic() - started) * 1000)


def expected_coverage_incomplete(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    expected = coverage.get("expected") if isinstance(coverage.get("expected"), dict) else {}
    return bool(expected.get("enabled") and expected.get("ok") is False)


def discover_homepage_with_retry(args: argparse.Namespace) -> tuple[subprocess.CompletedProcess[str], dict[str, Any], dict[str, Any]]:
    max_snapshots = int(args.max_snapshots or 0)
    min_snapshots = int(args.min_snapshots or 0)
    expected_labels = split_expected_labels(getattr(args, "expected_labels", ""))
    discover, payload, elapsed_ms = discover_homepage_once(
        args,
        max_snapshots=max_snapshots,
        min_snapshots=min_snapshots,
    )
    payload = apply_expected_coverage(
        payload,
        expected_post_count=int(getattr(args, "expected_post_count", 0) or 0),
        expected_labels=expected_labels,
    )
    attempts = [
        {
            "attempt": 1,
            "max_snapshots": max_snapshots,
            "min_snapshots": min_snapshots,
            "returncode": discover.returncode,
            "ok": bool(payload.get("ok")),
            "post_count": payload.get("post_count", 0),
            "coverage_incomplete": bool(payload.get("coverage_incomplete") or (payload.get("coverage") or {}).get("coverage_incomplete")),
            "expected_coverage_failed": expected_coverage_incomplete(payload),
            "stop_reason": (payload.get("coverage") or {}).get("stop_reason") or "",
            "elapsed_ms": elapsed_ms,
        }
    ]
    needs_retry = needs_snapshot_budget_retry(payload) or expected_coverage_incomplete(payload)
    if discover.returncode == 0 and payload.get("ok") and needs_retry:
        raised_max = retry_snapshot_budget(max_snapshots, minimum=32)
        retry_discover, retry_payload, retry_elapsed_ms = discover_homepage_once(
            args,
            max_snapshots=raised_max,
            min_snapshots=min_snapshots,
        )
        retry_payload = apply_expected_coverage(
            retry_payload,
            expected_post_count=int(getattr(args, "expected_post_count", 0) or 0),
            expected_labels=expected_labels,
        )
        attempts.append(
            {
                "attempt": 2,
                "max_snapshots": raised_max,
                "min_snapshots": min_snapshots,
                "returncode": retry_discover.returncode,
                "ok": bool(retry_payload.get("ok")),
                "post_count": retry_payload.get("post_count", 0),
                "coverage_incomplete": bool(
                    retry_payload.get("coverage_incomplete") or (retry_payload.get("coverage") or {}).get("coverage_incomplete")
                ),
                "expected_coverage_failed": expected_coverage_incomplete(retry_payload),
                "stop_reason": (retry_payload.get("coverage") or {}).get("stop_reason") or "",
                "elapsed_ms": retry_elapsed_ms,
            }
        )
        if retry_discover.returncode == 0 and retry_payload.get("ok"):
            retry_payload = attach_auto_retry_report(retry_payload, attempts=attempts)
            return retry_discover, retry_payload, {"attempted": True, "attempts": attempts}
    payload = attach_auto_retry_report(payload, attempts=attempts if len(attempts) > 1 else [])
    return discover, payload, {"attempted": len(attempts) > 1, "attempts": attempts}


def discover_and_import(
    args: argparse.Namespace,
    *,
    target_dates: list[str],
) -> dict[str, Any]:
    discover_started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="fb-account-job-") as temp_dir:
        temp = Path(temp_dir)
        raw_path = temp / "raw.json"
        discover, discover_payload, discover_retry = discover_homepage_with_retry(args)
        if discover.returncode != 0 or not discover_payload.get("ok"):
            stage = "human_intervention_required" if needs_human_intervention(discover_payload) else "discover"
            return {
                "ok": False,
                "stage": stage,
                "human_intervention_required": needs_human_intervention(discover_payload),
                "elapsed_ms": int((time.monotonic() - discover_started) * 1000),
                "discover": discover_payload,
                "discover_retry": discover_retry,
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
                prepare_payload = parse_json_output(prepare)
                prepare_payload["returncode"] = prepare.returncode
                return {
                    "ok": False,
                    "stage": "prepare",
                    "run_status": "prepare_failed",
                    "complete": False,
                    "message": "主页候选发现后标准化失败；本次未导入本地库，也未写入飞书。",
                    "target_date": target_date,
                    "elapsed_ms": int((time.monotonic() - discover_started) * 1000),
                    "discover": {
                        "raw_candidate_count": discover_payload.get("raw_candidate_count", 0),
                        "post_count": discover_payload.get("post_count", 0),
                        "capture_complete": discover_payload.get("capture_complete", True),
                        "coverage": discover_payload.get("coverage", {}),
                        "coverage_blocked": discover_payload.get("coverage_blocked", False),
                        "coverage_incomplete": discover_payload.get("coverage_incomplete", False),
                        "expected_coverage": (discover_payload.get("coverage") or {}).get("expected", {}),
                        "auto_retry": (discover_payload.get("coverage") or {}).get("auto_retry", {}),
                    },
                    "prepare": prepare_payload,
                    "returncode": prepare.returncode,
                }
            try:
                prepared_payload = json.loads(prepared_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                return {
                    "ok": False,
                    "stage": "prepare",
                    "run_status": "prepare_failed",
                    "complete": False,
                    "message": "候选标准化命令返回成功，但输出文件不可读取；本次未导入本地库，也未写入飞书。",
                    "target_date": target_date,
                    "elapsed_ms": int((time.monotonic() - discover_started) * 1000),
                    "discover": {
                        "raw_candidate_count": discover_payload.get("raw_candidate_count", 0),
                        "post_count": discover_payload.get("post_count", 0),
                        "capture_complete": discover_payload.get("capture_complete", True),
                        "coverage": discover_payload.get("coverage", {}),
                        "coverage_blocked": discover_payload.get("coverage_blocked", False),
                        "coverage_incomplete": discover_payload.get("coverage_incomplete", False),
                        "expected_coverage": (discover_payload.get("coverage") or {}).get("expected", {}),
                        "auto_retry": (discover_payload.get("coverage") or {}).get("auto_retry", {}),
                    },
                    "prepare": {
                        "ok": False,
                        "run_status": "prepare_failed",
                        "stage": "output_load",
                        "complete": False,
                        "output_path": str(prepared_path),
                        "error": str(exc),
                    },
                    "returncode": 1,
                }
            prepared_counts[target_date] = int(prepared_payload.get("prepared") or 0)
            if prepared_counts[target_date]:
                import_payload = import_prepared(
                    args.config,
                    prepared_path,
                    account_name=args.account_name,
                    account_url=args.account_url,
                    account_type=args.account_type,
                )
                if import_payload.get("returncode") != 0 or import_payload.get("ok") is False:
                    return {
                        "ok": False,
                        "stage": "import",
                        "run_status": "import_failed",
                        "complete": False,
                        "message": "候选标准化后本地入库失败；本次采集作业未完成，不能把已有输出视为最终结果。",
                        "target_date": target_date,
                        "elapsed_ms": int((time.monotonic() - discover_started) * 1000),
                        "discover": {
                            "raw_candidate_count": discover_payload.get("raw_candidate_count", 0),
                            "post_count": discover_payload.get("post_count", 0),
                            "capture_complete": discover_payload.get("capture_complete", True),
                            "coverage": discover_payload.get("coverage", {}),
                            "coverage_blocked": discover_payload.get("coverage_blocked", False),
                            "coverage_incomplete": discover_payload.get("coverage_incomplete", False),
                            "expected_coverage": (discover_payload.get("coverage") or {}).get("expected", {}),
                            "auto_retry": (discover_payload.get("coverage") or {}).get("auto_retry", {}),
                        },
                        "prepared_counts": prepared_counts,
                        "prepared": prepared_counts[target_date],
                        "import": import_payload,
                        "returncode": import_payload.get("returncode") or 1,
                    }
                imports.append(
                    {
                        "target_date": target_date,
                        "import": import_payload,
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
                "auto_retry": (discover_payload.get("coverage") or {}).get("auto_retry", {}),
            },
            "discover_retry": discover_retry,
            "prepared_counts": prepared_counts,
            "imports": imports,
        }


def completion_requires_opencli(completion: dict[str, Any]) -> bool:
    stage_counts: dict[str, Any] = {}
    for key in ("open_task_stage_counts", "missing_stage_counts"):
        value = completion.get(key)
        if isinstance(value, dict):
            stage_counts.update(value)
    return any(stage in OPENCLI_REQUIRED_STAGES and int(count or 0) > 0 for stage, count in stage_counts.items())


def should_run_worker_for_completion(completion: dict[str, Any]) -> bool:
    if not completion.get("open_task_count"):
        return False
    if completion.get("has_summary_only_work") and not has_pre_summary_auto_enrichment_work(completion):
        return False
    return True


def run_worker_pass(
    args: argparse.Namespace,
    *,
    target_dates: list[str],
    pass_index: int,
    stages: str = ENRICHMENT_STAGES,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for target_date in target_dates:
        command = [
            "python3",
            "scripts/enrichment_worker.py",
            "--config",
            args.config,
            "--stages",
            stages,
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
    failed = [
        item
        for item in results
        if not worker_result_is_operational(item)
    ]
    human_intervention = [item for item in results if needs_human_intervention(item)]
    worker_failed = bool(failed)
    failure_reasons: list[str] = []
    for item in failed:
        for reason in worker_failure_reasons(item):
            if reason and reason not in failure_reasons:
                failure_reasons.append(reason)
    retry_later_seen = any(bool(item.get("retry_later")) for item in results)
    retry_later_count = sum(_int_metric(item.get("requeued")) for item in results if item.get("retry_later"))
    retry_later_reasons: list[str] = []
    codex_summary_required_count = sum(_int_metric(item.get("codex_summary_required_count")) for item in results)
    codex_summary_required_urls: list[str] = []
    for item in results:
        for url in item.get("codex_summary_required_urls") or []:
            text_url = str(url or "").strip()
            if text_url and text_url not in codex_summary_required_urls:
                codex_summary_required_urls.append(text_url)
        if not item.get("retry_later"):
            continue
        for reason in item.get("retry_later_reasons") or [item.get("run_status") or item.get("status") or "retry_later"]:
            text = str(reason or "").strip()
            if text and text not in retry_later_reasons:
                retry_later_reasons.append(text)
    return {
        "ok": not failed and not human_intervention,
        "pass": pass_index,
        "worker_failed": worker_failed,
        "worker_failure_reasons": failure_reasons[:10],
        "human_intervention_required": bool(human_intervention),
        "human_intervention_reasons": [
            reason
            for item in human_intervention
            for reason in (item.get("human_intervention_reasons") or [item.get("reason") or item.get("run_status") or item.get("status")])
            if reason
        ][:10],
        "retry_later": retry_later_seen,
        "retry_later_count": retry_later_count,
        "retry_later_reasons": retry_later_reasons[:10],
        "codex_summary_required": bool(codex_summary_required_count),
        "codex_summary_required_count": codex_summary_required_count,
        "codex_summary_required_urls": codex_summary_required_urls[:10],
        "stages": stages,
        "results": results,
    }


def auto_resume_pass_limit(value: Any) -> int:
    try:
        configured = int(value or 0)
    except (TypeError, ValueError):
        configured = 0
    if configured <= 0:
        return DEFAULT_MAX_RESUME_PASSES
    return min(configured, MAX_AUTO_RESUME_PASSES)


def completion_progress_key(completion: dict[str, Any]) -> tuple[int, int, int, int, int]:
    return (
        _int_metric(completion.get("open_task_count")),
        _int_metric(completion.get("auto_open_task_count")),
        _int_metric(completion.get("incomplete_post_count")),
        _int_metric(completion.get("requires_codex_summary_count")),
        _int_metric(completion.get("coverage_incomplete_count")),
    )


def completion_improved(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return completion_progress_key(after) < completion_progress_key(before)


def run_sync(
    config: dict[str, Any],
    args: argparse.Namespace,
    posts: list[dict[str, Any]],
    conn: Any,
) -> dict[str, Any]:
    if not args.sync:
        completion = enrichment_completion_summary(conn, posts, config)
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


def run_worker_passes_for_job(
    args: argparse.Namespace,
    conn: Any,
    posts: list[dict[str, Any]],
    config: dict[str, Any],
    target_dates: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    worker_passes: list[dict[str, Any]] = []
    resume_passes = 0 if args.status_only else auto_resume_pass_limit(args.max_resume_passes)
    for index in range(resume_passes):
        completion_before = enrichment_completion_summary(conn, posts, config)
        if not should_run_worker_for_completion(completion_before):
            break
        worker_pass = run_worker_pass(args, target_dates=target_dates, pass_index=index + 1)
        worker_passes.append(worker_pass)
        if worker_pass.get("human_intervention_required"):
            break
        if worker_pass.get("worker_failed"):
            break
        posts = scoped_posts(
            conn,
            account_name=args.account_name,
            account_url=args.account_url,
            account_type=args.account_type,
            dates=target_dates,
        )
        enqueue_enrichment_tasks_for_posts(conn, posts, config)
        completion_after = enrichment_completion_summary(conn, posts, config)
        worker_pass["completion_before"] = {
            "open_task_count": _int_metric(completion_before.get("open_task_count")),
            "auto_open_task_count": _int_metric(completion_before.get("auto_open_task_count")),
            "incomplete_post_count": _int_metric(completion_before.get("incomplete_post_count")),
            "requires_codex_summary_count": _int_metric(completion_before.get("requires_codex_summary_count")),
        }
        worker_pass["completion_after"] = {
            "open_task_count": _int_metric(completion_after.get("open_task_count")),
            "auto_open_task_count": _int_metric(completion_after.get("auto_open_task_count")),
            "incomplete_post_count": _int_metric(completion_after.get("incomplete_post_count")),
            "requires_codex_summary_count": _int_metric(completion_after.get("requires_codex_summary_count")),
        }
        worker_pass["made_progress"] = completion_improved(completion_before, completion_after)
        if worker_pass.get("retry_later"):
            break
    return worker_passes, posts


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


def summary_requests_output_path_for_dates(target_dates: list[str]) -> str:
    dates = [date for date in target_dates if date]
    if not dates:
        suffix = "current"
    elif len(dates) == 1:
        suffix = dates[0]
    else:
        suffix = f"{dates[0]}_{dates[-1]}"
    return f"exports/summary_requests_{suffix}.json"


def append_summary_scope_args(command: list[Any], args: argparse.Namespace, target_dates: list[str]) -> None:
    dates = [date for date in target_dates if date]
    if len(dates) == 1:
        command.extend(["--date", dates[0]])
    elif len(dates) > 1:
        command.extend(["--start-date", dates[0], "--end-date", dates[-1]])
    if args.account_name:
        command.extend(["--account-name", args.account_name])
    if args.account_url:
        command.extend(["--account-url", args.account_url])
    if args.account_type:
        command.extend(["--account-type", args.account_type])


def resume_command(base: list[Any], primary_date: str, *, force_recover_running: bool = False) -> list[Any]:
    command = list(base)
    if primary_date:
        command.extend(["--target-date", primary_date])
    command.append("--resume-only")
    if force_recover_running:
        command.append("--force-recover-running")
    return command


def append_quality_threshold_args(command: list[Any], args: argparse.Namespace) -> None:
    if getattr(args, "require_coverage_complete", False):
        command.append("--require-coverage-complete")
    rate_args = [
        ("min_ledger_usable_rate", "--min-ledger-usable-rate"),
        ("min_final_usable_rate", "--min-final-usable-rate"),
        ("min_completion_rate", "--min-completion-rate"),
        ("min_expected_post_coverage_rate", "--min-expected-post-coverage-rate"),
        ("min_expected_label_coverage_rate", "--min-expected-label-coverage-rate"),
    ]
    for attr, flag in rate_args:
        value = _rate_threshold(getattr(args, attr, 0.0))
        if value > 0.0:
            command.extend([flag, str(value)])


def has_scoped_enrichment_resume_work(completion: dict[str, Any]) -> bool:
    if completion.get("has_summary_only_work") and not _int_metric(completion.get("auto_open_task_count")):
        return False
    if _int_metric(completion.get("auto_open_task_count")) > 0:
        return True
    for key in ("open_task_stage_counts", "missing_stage_counts"):
        counts = completion.get(key)
        if not isinstance(counts, dict):
            continue
        for stage, count in counts.items():
            if str(stage or "") == "coverage":
                continue
            if str(stage or "") == "summary":
                continue
            if _int_metric(count) > 0:
                return True
    if "open_task_stage_counts" not in completion and "missing_stage_counts" not in completion:
        return _int_metric(completion.get("open_task_count")) > 0
    return False


def append_resume_pass_budget(command: list[Any], args: argparse.Namespace) -> None:
    command.extend(["--max-resume-passes", str(auto_resume_pass_limit(getattr(args, "max_resume_passes", 0)))])
    command.extend(["--enrichment-limit", str(max(1, _int_metric(getattr(args, "enrichment_limit", 50)) or 50))])
    command.extend(
        [
            "--resume-stale-running-seconds",
            str(max(0, _int_metric(getattr(args, "resume_stale_running_seconds", DEFAULT_RESUME_STALE_RUNNING_SECONDS)))),
        ]
    )


def resume_sync_command(base: list[Any], primary_date: str, args: argparse.Namespace) -> list[Any]:
    command = list(base)
    if "--sync" not in command:
        command.append("--sync")
    if primary_date:
        command.extend(["--target-date", primary_date])
    command.extend(["--resume-only", "--force-recover-running"])
    append_resume_pass_budget(command, args)
    return command


def full_capture_command(
    base: list[Any],
    primary_date: str,
    args: argparse.Namespace,
    *,
    max_snapshots: int | None = None,
    min_snapshots: int | None = None,
) -> list[Any]:
    command = list(base)
    if primary_date:
        command.extend(["--target-date", primary_date])
    snapshot_budget = max_snapshots if max_snapshots is not None else getattr(args, "max_snapshots", None)
    if snapshot_budget:
        command.extend(["--max-snapshots", str(snapshot_budget)])
    min_snapshot_budget = min_snapshots if min_snapshots is not None else getattr(args, "min_snapshots", None)
    if min_snapshot_budget:
        command.extend(["--min-snapshots", str(min_snapshot_budget)])
    if getattr(args, "expected_post_count", 0):
        command.extend(["--expected-post-count", str(args.expected_post_count)])
    if getattr(args, "expected_labels", ""):
        command.extend(["--expected-labels", args.expected_labels])
    append_resume_pass_budget(command, args)
    return command


def discover_blocked_before_import(discover_coverage: dict[str, Any]) -> bool:
    reasons = set(discover_coverage.get("reasons") or [])
    return "human_intervention_required" in reasons or "discover_failed_before_import" in reasons


def sync_failed_status(sync_result: dict[str, Any]) -> str:
    """Return the account-job status that should represent a sync failure."""

    if sync_result.get("ok", True):
        return ""
    status = str(sync_result.get("run_status") or sync_result.get("stage") or "sync_failed")
    if status == "blocked_auth":
        return "blocked_auth"
    if status in {"quality_gate", "audit_output_gate", "partial_gate"}:
        return status
    return "sync_failed"


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
    append_quality_threshold_args(base, args)
    commands: list[dict[str, Any]] = []
    primary_date = target_dates[-1] if target_dates else ""
    hard_blockers = {"blocked_opencli", "blocked_auth", "human_intervention_required", "worker_failed"}
    if run_status not in hard_blockers and (
        run_status in {"incomplete_pending_tasks", "synced_ledger_incomplete"}
        or has_scoped_enrichment_resume_work(completion)
    ):
        command = resume_command(base, primary_date, force_recover_running=True)
        append_resume_pass_budget(command, args)
        commands.append(
            {
                "reason": "pending_enrichment",
                "description": "继续同账号同日期的 SQLite 补抓队列，不重新发现主页；同时恢复上次中断遗留的 running 任务。覆盖不足仍需后续重跑主页，但已入库候选应先补齐详情字段并回写飞书。",
                "command": command_text(command),
            }
        )
    if run_status == "coverage_incomplete":
        command = full_capture_command(
            base,
            primary_date,
            args,
            max_snapshots=max(int(args.max_snapshots or 0) + 12, 32),
            min_snapshots=max(int(getattr(args, "min_snapshots", 0) or 0), 6),
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
    if run_status == "prepare_failed":
        commands.append(
            {
                "reason": "prepare_failed",
                "description": "主页候选已发现但标准化失败；修复输入结构或标准化错误后，从账号主页顶部重新发现候选并继续补抓/同步。",
                "command": command_text(full_capture_command(base, primary_date, args)),
            }
        )
    if run_status == "import_failed":
        commands.append(
            {
                "reason": "import_failed",
                "description": "标准化候选已生成但本地入库失败；修复 SQLite/配置/导入错误后，重新运行同一账号采集命令。",
                "command": command_text(full_capture_command(base, primary_date, args)),
            }
        )
    if run_status == "worker_failed":
        command = resume_command(base, primary_date, force_recover_running=True)
        append_resume_pass_budget(command, args)
        commands.append(
            {
                "reason": "worker_failed",
                "description": "补抓执行器返回了非结构化失败；修复脚本或环境错误后继续同账号队列，不重新发现主页。",
                "command": command_text(command),
            }
        )
    if run_status == "summary_auto_apply_failed":
        output = summary_requests_output_path_for_dates(target_dates)
        command = [
            "python3",
            "scripts/export_summary_requests.py",
            "--config",
            args.config,
            "--output",
            output,
        ]
        append_summary_scope_args(command, args, target_dates)
        commands.append(
            {
                "reason": "summary_auto_apply_failed",
                "description": "自动中文概要生成或应用失败；重新导出 scoped summary requests，修复概要 JSON 后应用并续跑。",
                "command": command_text(command),
            }
        )
        retry_command = resume_command(base, primary_date, force_recover_running=True)
        append_resume_pass_budget(retry_command, args)
        commands.append(
            {
                "reason": "summary_auto_apply_failed",
                "description": "重新执行同账号概要阶段和同步；批量作业会优先使用这条机器可续跑命令，避免停在概要导出后等待人工追问。",
                "command": command_text(retry_command),
            }
        )
    if run_status == "needs_codex_summary" or (
        run_status != "summary_auto_apply_failed"
        and completion.get("requires_codex_summary_count")
        and not has_pre_summary_auto_enrichment_work(completion)
    ):
        output = summary_requests_output_path_for_dates(target_dates)
        command = [
            "python3",
            "scripts/export_summary_requests.py",
            "--config",
            args.config,
            "--output",
            output,
        ]
        append_summary_scope_args(command, args, target_dates)
        commands.append(
            {
                "reason": "needs_codex_summary",
                "description": "导出需要 Codex 中文概要的文章材料。",
                "command": command_text(command),
            }
        )
        retry_command = resume_command(base, primary_date, force_recover_running=True)
        append_resume_pass_budget(retry_command, args)
        commands.append(
            {
                "reason": "needs_codex_summary",
                "description": "继续同账号概要自动生成/应用和同步；如果自动概要失败，再使用上一条导出命令做人工修复。",
                "command": command_text(retry_command),
            }
        )
    if run_status == "blocked_auth":
        if getattr(args, "resume_only", False):
            command = resume_command(base, primary_date, force_recover_running=True)
            append_resume_pass_budget(command, args)
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
    if run_status in {"sync_failed", "quality_gate", "audit_output_gate", "partial_gate"}:
        command = resume_command(base, primary_date, force_recover_running=True)
        append_resume_pass_budget(command, args)
        commands.append(
            {
                "reason": run_status,
                "description": "同步或输出门未完成；保留本地 SQLite 结果，修复对应问题后续跑同一账号同日期同步。",
                "command": command_text(command),
            }
        )
    if run_status in {"captured_not_synced", "resumed_not_synced"}:
        commands.append(
            {
                "reason": run_status,
                "description": "本地采集/补抓已完成但未写入飞书；继续同账号同步，让飞书台账更新到最新结果。",
                "command": command_text(resume_sync_command(base, primary_date, args)),
            }
        )
    if run_status == "quality_threshold_failed":
        coverage_failed = bool(
            discover_coverage.get("incomplete")
            or completion.get("coverage_incomplete_count")
            or _int_metric(completion.get("post_count")) == 0
        )
        if coverage_failed:
            command = full_capture_command(
                base,
                primary_date,
                args,
                max_snapshots=max(int(getattr(args, "max_snapshots", 0) or 0) + 12, 32),
                min_snapshots=max(int(getattr(args, "min_snapshots", 0) or 0), 6),
            )
            description = "质量阈值未达标且覆盖/候选不足；从账号主页顶部重新发现候选并继续补抓/同步。"
        elif has_scoped_enrichment_resume_work(completion):
            command = resume_command(base, primary_date, force_recover_running=True)
            append_resume_pass_budget(command, args)
            description = "质量阈值未达标且仍有补抓缺口；继续同账号补抓队列并同步最新字段。"
        else:
            command = resume_command(base, primary_date, force_recover_running=True)
            command.append("--status-only")
            append_resume_pass_budget(command, args)
            description = "质量阈值未达标；当前没有可直接推进的补抓/覆盖命令，续跑同账号状态检查确认是否已恢复。"
        commands.append(
            {
                "reason": "quality_threshold_failed",
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
        if getattr(args, "resume_only", False):
            command = resume_command(base, primary_date, force_recover_running=True)
            append_resume_pass_budget(command, args)
            commands.append(
                {
                    "reason": "resume_after_opencli",
                    "description": "OpenCLI Browser Bridge 恢复后，继续同账号同日期的 SQLite 补抓队列，不重新发现主页。",
                    "command": command_text(command),
                }
            )
            return commands[:4]
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
            append_resume_pass_budget(command, args)
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
    summary_auto_apply: dict[str, Any] | None = None,
) -> str:
    if not preflight.get("ok"):
        return "blocked_opencli"
    sync_failed = sync_failed_status(sync_result)
    if sync_failed == "blocked_auth":
        return "blocked_auth"
    if discover_import and needs_human_intervention(discover_import):
        return "human_intervention_required"
    if any(worker_pass.get("human_intervention_required") for worker_pass in worker_passes):
        return "human_intervention_required"
    if any(worker_pass.get("worker_failed") for worker_pass in worker_passes):
        return "worker_failed"
    if summary_auto_apply and summary_auto_apply.get("ok") is False:
        return "summary_auto_apply_failed"
    if discover_has_incomplete_coverage(discover_import):
        return "coverage_incomplete"
    if completion.get("coverage_incomplete_count"):
        return "coverage_incomplete"
    completion_status = completion_run_status(completion, ledger_mode=False)
    if completion_status != "complete":
        return completion_status
    if sync_failed:
        return sync_failed
    if sync_result.get("ok") and not sync_result.get("skipped"):
        return "complete"
    if discover_import and discover_import.get("ok"):
        return "captured_not_synced"
    if worker_passes:
        return "resumed_not_synced"
    return "no_work"


def _int_metric(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_metric(value: Any) -> float:
    try:
        return round(float(value or 0.0), 4)
    except (TypeError, ValueError):
        return 0.0


def _sync_skipped(sync_result: dict[str, Any]) -> bool:
    skipped = sync_result.get("skipped")
    if isinstance(skipped, bool):
        return skipped
    return str(sync_result.get("stage") or "") == "sync_disabled" or str(sync_result.get("run_status") or "") == "not_synced"


def worker_retry_summary(worker_passes: list[dict[str, Any]]) -> dict[str, Any]:
    count = 0
    retry_later_seen = False
    reasons: list[str] = []
    for worker_pass in worker_passes:
        retry_later_seen = retry_later_seen or bool(worker_pass.get("retry_later"))
        count += _int_metric(worker_pass.get("retry_later_count"))
        for reason in worker_pass.get("retry_later_reasons") or []:
            text = str(reason or "").strip()
            if text and text not in reasons:
                reasons.append(text)
    return {
        "retry_later": retry_later_seen,
        "retry_later_count": count,
        "retry_later_reasons": reasons[:10],
    }


def worker_summary_requirement_summary(worker_passes: list[dict[str, Any]]) -> dict[str, Any]:
    count = 0
    urls: list[str] = []
    for worker_pass in worker_passes:
        count += _int_metric(worker_pass.get("codex_summary_required_count"))
        for url in worker_pass.get("codex_summary_required_urls") or []:
            text = str(url or "").strip()
            if text and text not in urls:
                urls.append(text)
    return {
        "codex_summary_required": count > 0,
        "codex_summary_required_count": count,
        "codex_summary_required_urls": urls[:10],
    }


def worker_failure_summary(worker_passes: list[dict[str, Any]]) -> dict[str, Any]:
    count = 0
    reasons: list[str] = []
    for worker_pass in worker_passes:
        if not worker_pass.get("worker_failed"):
            continue
        count += 1
        for reason in worker_pass.get("worker_failure_reasons") or []:
            text = str(reason or "").strip()
            if text and text not in reasons:
                reasons.append(text)
    return {
        "worker_failed": count > 0,
        "worker_failed_pass_count": count,
        "worker_failure_reasons": reasons[:10],
    }


def has_pre_summary_auto_enrichment_work(completion: dict[str, Any]) -> bool:
    """Return True only when article material still needs machine fetching.

    Post type, engagement, exact time, and lead-link refetches can continue in
    parallel with summary generation once article material exists. Blocking
    summaries on those independent OpenCLI stages leaves Feishu rows with empty
    story summaries even though the summary source is already available.
    """

    for key in ("open_task_stage_counts", "missing_stage_counts"):
        counts = completion.get(key)
        if not isinstance(counts, dict):
            continue
        if _int_metric(counts.get("article_material")) > 0:
            return True
    return False


def export_summary_requests_for_job(args: argparse.Namespace, target_dates: list[str], completion: dict[str, Any]) -> dict[str, Any]:
    if getattr(args, "status_only", False):
        return {"ok": True, "skipped": True, "reason": "status_only"}
    if not completion.get("requires_codex_summary_count"):
        return {"ok": True, "skipped": True, "reason": "no_codex_summary_required"}
    if has_pre_summary_auto_enrichment_work(completion):
        return {"ok": True, "skipped": True, "reason": "auto_enrichment_still_pending"}
    output = summary_requests_output_path_for_dates(target_dates)
    output_path = ROOT / output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command: list[str] = [
        "python3",
        "scripts/export_summary_requests.py",
        "--config",
        args.config,
        "--output",
        output,
    ]
    append_summary_scope_args(command, args, target_dates)
    exported = run_command(command)
    payload = parse_json_output(exported)
    payload["returncode"] = exported.returncode
    payload["command"] = command_text(command)
    payload["output"] = output
    payload["output_path"] = str(output_path)
    payload["ok"] = exported.returncode == 0 and bool(payload.get("ok", True))
    if not payload["ok"]:
        payload["stdout"] = exported.stdout
        payload["stderr"] = exported.stderr
        payload.setdefault("stage", "summary_requests_export")
        payload.setdefault("run_status", "summary_requests_export_failed")
    return payload


def auto_generate_and_apply_summaries(
    args: argparse.Namespace,
    target_dates: list[str],
    completion: dict[str, Any],
) -> dict[str, Any]:
    if getattr(args, "status_only", False):
        return {"ok": True, "skipped": True, "reason": "status_only"}
    required_summary_count = _int_metric(completion.get("requires_codex_summary_count"))
    if not required_summary_count:
        return {"ok": True, "skipped": True, "reason": "no_codex_summary_required"}
    if has_pre_summary_auto_enrichment_work(completion):
        return {"ok": True, "skipped": True, "reason": "auto_enrichment_still_pending"}

    output = summary_requests_output_path_for_dates(target_dates)
    requests_path = ROOT / output
    summaries_output = output.replace("summary_requests", "article_summaries")
    summaries_path = ROOT / summaries_output
    requests_path.parent.mkdir(parents=True, exist_ok=True)
    summaries_path.parent.mkdir(parents=True, exist_ok=True)

    export_command: list[str] = [
        "python3",
        "scripts/export_summary_requests.py",
        "--config",
        args.config,
        "--output",
        output,
    ]
    append_summary_scope_args(export_command, args, target_dates)
    exported = run_command(export_command)
    export_payload = parse_json_output(exported)
    export_payload["returncode"] = exported.returncode
    export_payload["command"] = command_text(export_command)
    export_payload["output"] = output
    export_payload["output_path"] = str(requests_path)
    export_ok = exported.returncode == 0 and bool(export_payload.get("ok", True))
    if not export_ok:
        return {
            "ok": False,
            "stage": "summary_requests_export",
            "run_status": "summary_auto_apply_failed",
            "export": export_payload,
            "message": "概要请求导出失败；未生成或应用中文概要。",
        }

    generate_command = [
        "python3",
        "scripts/generate_article_summaries.py",
        "--input",
        output,
        "--output",
        summaries_output,
    ]
    generated = run_command(generate_command)
    generate_payload = parse_json_output(generated)
    generate_payload["returncode"] = generated.returncode
    generate_payload["command"] = command_text(generate_command)
    generate_payload["output"] = summaries_output
    generate_payload["output_path"] = str(summaries_path)
    generated_summary_count = max(
        _int_metric(generate_payload.get("generated")),
        _int_metric(generate_payload.get("generated_request_count")),
        _int_metric(generate_payload.get("summary_key_count")),
    )
    generate_ok = bool(generate_payload.get("ok", True)) and (
        generated.returncode == 0 or (generated.returncode == 2 and generated_summary_count > 0)
    )
    if not generate_ok:
        return {
            "ok": False,
            "stage": "summary_generate",
            "run_status": "summary_auto_apply_failed",
            "export": export_payload,
            "generate": generate_payload,
            "message": "中文概要生成失败或没有可应用概要；未应用概要。",
        }

    apply_command: list[str] = [
        "python3",
        "scripts/apply_article_summaries.py",
        "--config",
        args.config,
        "--summaries",
        summaries_output,
    ]
    append_summary_scope_args(apply_command, args, target_dates)
    if args.dry_run:
        apply_command.append("--dry-run")
    applied = run_command(apply_command)
    apply_payload = parse_json_output(applied)
    apply_payload["returncode"] = applied.returncode
    apply_payload["command"] = command_text(apply_command)
    applied_count = _int_metric(apply_payload.get("applied"))
    apply_ok = applied.returncode == 0 and bool(apply_payload.get("ok", True)) and applied_count > 0
    if applied.returncode == 0 and bool(apply_payload.get("ok", True)) and applied_count == 0:
        apply_payload["ok"] = False
        apply_payload["run_status"] = "summary_apply_noop"
        apply_payload["message"] = "概要应用命令成功返回，但没有任何 scoped 帖子被更新；不能把概要阶段视为完成。"
    return {
        "ok": apply_ok,
        "stage": "summary_auto_apply",
        "run_status": "summary_applied" if apply_ok else "summary_auto_apply_failed",
        "export": export_payload,
        "generate": generate_payload,
        "partial_generation": generated.returncode != 0,
        "generated_summary_count": generated_summary_count,
        "applied_summary_count": applied_count,
        "required_summary_count": required_summary_count,
        "apply": apply_payload,
        "requests_output": output,
        "summaries_output": summaries_output,
    }


def _count_dict(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    counts: dict[str, int] = {}
    for key, count in value.items():
        stage = str(key or "").strip()
        if not stage:
            continue
        number = _int_metric(count)
        if number > 0:
            counts[stage] = number
    return dict(sorted(counts.items(), key=lambda item: (STAGE_ORDER.get(item[0], 99), item[0])))


def _stage_pressure(open_counts: dict[str, int], missing_counts: dict[str, int]) -> list[dict[str, Any]]:
    pressure: list[dict[str, Any]] = []
    stages = sorted(set(open_counts) | set(missing_counts), key=lambda stage: (STAGE_ORDER.get(stage, 99), stage))
    for stage in stages:
        open_task_count = open_counts.get(stage, 0)
        missing_post_count = missing_counts.get(stage, 0)
        pressure.append(
            {
                "stage": stage,
                "label": STAGE_LABELS.get(stage, stage),
                "open_task_count": open_task_count,
                "missing_post_count": missing_post_count,
                "total_pressure": open_task_count + missing_post_count,
                "requires_opencli": stage in OPENCLI_REQUIRED_STAGES,
                "requires_codex": stage == "summary",
            }
        )
    return pressure


def _stage_pressure_notes(stage_pressure: list[dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    for item in stage_pressure[:6]:
        parts: list[str] = []
        missing_post_count = _int_metric(item.get("missing_post_count"))
        open_task_count = _int_metric(item.get("open_task_count"))
        if missing_post_count:
            parts.append(f"缺 {missing_post_count} 条")
        if open_task_count:
            parts.append(f"待跑 {open_task_count} 个任务")
        if not parts:
            continue
        notes.append(f"{item.get('label') or item.get('stage')}：" + "，".join(parts))
    return notes


def completion_blockers_for_summary(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Return ordered blockers that explain why this scoped job is not complete."""

    blockers: list[dict[str, Any]] = []

    def add(
        code: str,
        *,
        label: str,
        severity: str,
        priority: int,
        message: str,
        next_action: str,
        metrics: dict[str, Any] | None = None,
        recoverable: bool = True,
    ) -> None:
        blockers.append(
            {
                "code": code,
                "label": label,
                "severity": severity,
                "priority": priority,
                "recoverable": recoverable,
                "message": message,
                "next_action": next_action,
                "metrics": metrics or {},
            }
        )

    run_status = str(summary.get("run_status") or "")
    if run_status == "blocked_auth":
        add(
            "blocked_auth",
            label="飞书授权阻塞",
            severity="hard_blocker",
            priority=0,
            message="飞书用户授权未通过，真实写入已在采集前停止。",
            next_action="先恢复 lark-cli 用户授权，再按 next_commands 续跑同一账号作业。",
            metrics={"feishu_sync": summary.get("feishu_sync", {})},
        )
    if run_status == "blocked_opencli":
        add(
            "blocked_opencli",
            label="OpenCLI 未就绪",
            severity="hard_blocker",
            priority=1,
            message="OpenCLI Browser Bridge 未就绪，无法进行主页发现或详情补抓。",
            next_action="先运行 check_env.py --fix-opencli 并确认业务 Chrome 的 Browser Bridge 已连接。",
            metrics={"open_task_count": summary.get("open_task_count", 0)},
        )
    if run_status == "human_intervention_required":
        add(
            "human_intervention_required",
            label="需要人工恢复登录/Profile",
            severity="hard_blocker",
            priority=2,
            message="Facebook 登录态、访客预览、验证码或错误 Chrome Profile 阻塞了采集。",
            next_action="在正常 Chrome 中恢复目标账号主页可见后，按 next_commands 从主页或本地队列续跑。",
        )

    if summary.get("coverage_health") == "incomplete":
        add(
            "coverage_incomplete",
            label="覆盖不足",
            severity="coverage",
            priority=10,
            message="主页采集覆盖未完整，已发现候选可以进台账，但本次不能声明抓全。",
            next_action="从账号主页顶部重跑采集，保留 expected count/labels，必要时提高 --max-snapshots。",
            metrics={
                "coverage_reasons": summary.get("coverage_reasons", []),
                "expected_post_coverage_rate": summary.get("expected_post_coverage_rate", 0.0),
                "expected_label_coverage_rate": summary.get("expected_label_coverage_rate", 0.0),
                "discovered_post_count": summary.get("discovered_post_count", 0),
                "raw_candidate_count": summary.get("raw_candidate_count", 0),
                "coverage_stop_reason": summary.get("coverage_stop_reason", ""),
            },
        )

    if summary.get("worker_retry_later"):
        add(
            "worker_retry_later",
            label="详情补抓锁竞争已重排",
            severity="resumable",
            priority=15,
            message="详情补抓遇到同一 OpenCLI session 的临时锁竞争，相关任务已重排。",
            next_action="继续使用 next_commands 续跑同账号补抓队列；不要把它当作完成或数据失败。",
            metrics={
                "retry_later_count": summary.get("worker_retry_later_count", 0),
                "retry_later_reasons": summary.get("worker_retry_later_reasons", []),
            },
        )
    if summary.get("worker_failed"):
        add(
            "worker_failed",
            label="补抓执行器异常",
            severity="worker_failure",
            priority=16,
            message="补抓执行器没有返回可识别的结构化状态，不能把本次运行当作正常补抓未完成。",
            next_action="先查看 worker_failure_reasons，修复脚本或环境错误后按 next_commands 继续同账号队列。",
            metrics={
                "worker_failed_pass_count": summary.get("worker_failed_pass_count", 0),
                "worker_failure_reasons": summary.get("worker_failure_reasons", []),
            },
        )
    if summary.get("run_status") == "summary_auto_apply_failed":
        add(
            "summary_auto_apply_failed",
            label="中文概要自动应用失败",
            severity="summary_failure",
            priority=18,
            message="文章素材已进入概要阶段，但自动生成或应用中文概要失败，不能把本次运行当作完整。",
            next_action="查看 summary_auto_apply 的 export/generate/apply 结果，修复概要 JSON 后重新应用并续跑同步。",
        )

    for item in summary.get("stage_pressure") or []:
        if not isinstance(item, dict):
            continue
        stage = str(item.get("stage") or "")
        if stage in {"coverage", "summary"}:
            continue
        total = _int_metric(item.get("total_pressure"))
        if total <= 0:
            continue
        label = str(item.get("label") or stage)
        add(
            f"stage_{stage}",
            label=f"{label}待补抓",
            severity="auto_enrichment",
            priority=20 + STAGE_ORDER.get(stage, 20),
            message=f"{label}仍有缺口，阻止最终完整可用。",
            next_action="OpenCLI 可用后继续同账号 SQLite 补抓队列。",
            metrics={
                "stage": stage,
                "open_task_count": _int_metric(item.get("open_task_count")),
                "missing_post_count": _int_metric(item.get("missing_post_count")),
                "total_pressure": total,
                "requires_opencli": bool(item.get("requires_opencli")),
            },
        )

    if summary.get("requires_codex_summary_count") or any(
        isinstance(item, dict) and item.get("stage") == "summary" and _int_metric(item.get("total_pressure")) > 0
        for item in summary.get("stage_pressure") or []
    ):
        add(
            "codex_summary_required",
            label="需要 Codex 中文概要",
            severity="codex_required",
            priority=40,
            message="文章素材已具备或概要任务已排队，但仍缺基于文章材料的中文概要。",
            next_action="先导出 scoped summary requests，写好中文概要后运行 apply_article_summaries。",
            metrics={"requires_codex_summary_count": summary.get("requires_codex_summary_count", 0)},
        )

    top_field_gaps = summary.get("top_field_gaps") if isinstance(summary.get("top_field_gaps"), list) else []
    if top_field_gaps and summary.get("final_usable_rate", 0.0) < 1.0:
        add(
            "field_gaps",
            label="最终可用字段缺口",
            severity="quality_gap",
            priority=50,
            message="存在最终输出字段缺口，台账行可见但不能算完整可用。",
            next_action="按 top_field_gaps 优先补精确时间、引流链接、概要、互动数据或帖子类型。",
            metrics={"top_field_gaps": top_field_gaps[:5]},
        )

    feishu_sync = summary.get("feishu_sync") if isinstance(summary.get("feishu_sync"), dict) else {}
    if feishu_sync.get("enabled") and feishu_sync.get("ok") is False:
        add(
            "feishu_sync_failed",
            label="飞书同步未完成",
            severity="sync",
            priority=60,
            message="本地 SQLite 结果仍在，但飞书写入或输出门未完成。",
            next_action="修复飞书授权、表格、输出门或网络问题后，按 next_commands 续跑同步。",
            metrics={"feishu_sync": feishu_sync},
        )

    thresholds = summary.get("quality_thresholds") if isinstance(summary.get("quality_thresholds"), dict) else {}
    if thresholds and not thresholds.get("ok", True):
        add(
            "quality_threshold_failed",
            label="质量阈值未达标",
            severity="acceptance_gate",
            priority=70,
            message="本次运行未达到显式覆盖率或可用率验收阈值。",
            next_action="按 quality_thresholds.failures 和前面的 blocker 先补覆盖或最终可用字段，再续跑状态检查。",
            metrics={"failures": thresholds.get("failures", [])},
        )

    blockers.sort(key=lambda item: (item["priority"], item["code"]))
    return blockers[:10]


def _rate_threshold(value: Any) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(1.0, number)), 4)


def quality_thresholds_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "require_coverage_complete": bool(getattr(args, "require_coverage_complete", False)),
        "min_ledger_usable_rate": _rate_threshold(getattr(args, "min_ledger_usable_rate", 0.0)),
        "min_final_usable_rate": _rate_threshold(getattr(args, "min_final_usable_rate", 0.0)),
        "min_completion_rate": _rate_threshold(getattr(args, "min_completion_rate", 0.0)),
        "min_expected_post_coverage_rate": _rate_threshold(getattr(args, "min_expected_post_coverage_rate", 0.0)),
        "min_expected_label_coverage_rate": _rate_threshold(getattr(args, "min_expected_label_coverage_rate", 0.0)),
    }


def evaluate_quality_thresholds(summary: dict[str, Any], thresholds: dict[str, Any] | None = None) -> dict[str, Any]:
    thresholds = thresholds or {}
    normalized = {
        "require_coverage_complete": bool(thresholds.get("require_coverage_complete", False)),
        "min_ledger_usable_rate": _rate_threshold(thresholds.get("min_ledger_usable_rate", 0.0)),
        "min_final_usable_rate": _rate_threshold(thresholds.get("min_final_usable_rate", 0.0)),
        "min_completion_rate": _rate_threshold(thresholds.get("min_completion_rate", 0.0)),
        "min_expected_post_coverage_rate": _rate_threshold(thresholds.get("min_expected_post_coverage_rate", 0.0)),
        "min_expected_label_coverage_rate": _rate_threshold(thresholds.get("min_expected_label_coverage_rate", 0.0)),
    }
    enabled = normalized["require_coverage_complete"] or any(
        normalized[key] > 0.0
        for key in (
            "min_ledger_usable_rate",
            "min_final_usable_rate",
            "min_completion_rate",
            "min_expected_post_coverage_rate",
            "min_expected_label_coverage_rate",
        )
    )
    failures: list[dict[str, Any]] = []
    if normalized["require_coverage_complete"] and summary.get("coverage_health") != "complete":
        failures.append(
            {
                "metric": "coverage_health",
                "actual": summary.get("coverage_health"),
                "required": "complete",
                "message": "覆盖未达完整要求；需要从账号主页顶部重跑或补足期望覆盖检查。",
            }
        )
    rate_checks = [
        ("ledger_usable_rate", "min_ledger_usable_rate", "台账可见率低于阈值。"),
        ("final_usable_rate", "min_final_usable_rate", "最终完整可用率低于阈值。"),
        ("completion_rate", "min_completion_rate", "补抓完成率低于阈值。"),
        ("expected_post_coverage_rate", "min_expected_post_coverage_rate", "期望帖子数量覆盖率低于阈值。"),
        ("expected_label_coverage_rate", "min_expected_label_coverage_rate", "期望时间标签覆盖率低于阈值。"),
    ]
    for metric, threshold_key, message in rate_checks:
        threshold = normalized[threshold_key]
        if threshold <= 0.0:
            continue
        actual = _float_metric(summary.get(metric))
        if actual < threshold:
            failures.append(
                {
                    "metric": metric,
                    "actual": actual,
                    "required_min": threshold,
                    "message": message,
                }
            )
    actions: list[str] = []
    failed_metrics = {failure.get("metric") for failure in failures}
    if {"coverage_health", "expected_post_coverage_rate", "expected_label_coverage_rate"}.intersection(failed_metrics):
        actions.append("覆盖率未达标：从账号主页顶部重跑采集，必要时提高 --max-snapshots，并保留 expected count/labels。")
    if {"completion_rate", "final_usable_rate"}.intersection(failed_metrics):
        actions.append("可用率未达标：继续运行同账号补抓队列，优先处理 quality_summary.top_field_gaps。")
    if "ledger_usable_rate" in failed_metrics:
        actions.append("台账可见率未达标：检查候选是否缺 Facebook 帖子链接或账号信息，修复后重新导入/同步。")
    return {
        "enabled": enabled,
        "ok": not failures,
        "thresholds": normalized,
        "failures": failures,
        "next_actions": actions[:4],
    }


def account_job_quality_summary(
    *,
    run_status: str,
    discover_coverage: dict[str, Any] | None,
    completion: dict[str, Any] | None,
    sync_result: dict[str, Any] | None = None,
    thresholds: dict[str, Any] | None = None,
    worker_retry: dict[str, Any] | None = None,
    worker_failure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the small business-facing quality summary for an account job."""

    discover_coverage = discover_coverage or {"source": "not_run", "complete": True, "incomplete": False, "reasons": []}
    completion = completion or {}
    sync_result = sync_result or {}
    worker_retry = worker_retry or {}
    worker_failure = worker_failure or {}
    coverage_source = str(discover_coverage.get("source") or "unknown")
    coverage_incomplete = bool(discover_coverage.get("incomplete")) or str(completion.get("coverage_health") or "") == "incomplete"
    coverage_health = "incomplete" if coverage_incomplete else ("not_run" if coverage_source == "not_run" else "complete")
    coverage_reasons = sorted(
        {
            str(reason)
            for reason in (discover_coverage.get("reasons") or [])
            if str(reason)
        }
    )
    expected_coverage = discover_coverage.get("expected") if isinstance(discover_coverage.get("expected"), dict) else {}
    open_task_stage_counts = _count_dict(completion.get("open_task_stage_counts"))
    missing_stage_counts = _count_dict(completion.get("missing_stage_counts"))
    stage_pressure = _stage_pressure(open_task_stage_counts, missing_stage_counts)
    sync_enabled = bool(sync_result) and not _sync_skipped(sync_result)
    next_actions: list[str] = []
    for source in (sync_result.get("next_actions"), completion.get("next_actions")):
        if not isinstance(source, list):
            continue
        for action in source:
            text = str(action or "").strip()
            if text and text not in next_actions:
                next_actions.append(text)

    summary = {
        "run_status": run_status,
        "complete": run_status == "complete",
        "coverage_source": coverage_source,
        "coverage_complete": coverage_health == "complete",
        "coverage_health": coverage_health,
        "coverage_reasons": coverage_reasons,
        "coverage_message": str(discover_coverage.get("message") or ""),
        "coverage_stop_reason": str(discover_coverage.get("stop_reason") or ""),
        "discovered_post_count": _int_metric(discover_coverage.get("post_count")),
        "raw_candidate_count": _int_metric(discover_coverage.get("raw_candidate_count")),
        "expected_coverage_enabled": bool(expected_coverage.get("enabled")),
        "expected_post_coverage_rate": _float_metric(expected_coverage.get("post_count_coverage_rate")),
        "expected_label_coverage_rate": _float_metric(expected_coverage.get("label_coverage_rate")),
        "post_count": _int_metric(completion.get("post_count")),
        "ledger_candidate_count": _int_metric(completion.get("ledger_candidate_count")),
        "ledger_usable_rate": _float_metric(completion.get("ledger_usable_rate")),
        "ready_or_synced_posts": _int_metric(completion.get("ready_or_synced_posts")),
        "final_usable_count": _int_metric(completion.get("final_usable_count")),
        "final_usable_rate": _float_metric(completion.get("final_usable_rate")),
        "completion_rate": _float_metric(completion.get("completion_rate")),
        "incomplete_post_count": _int_metric(completion.get("incomplete_post_count")),
        "coverage_incomplete_count": _int_metric(completion.get("coverage_incomplete_count")),
        "open_task_count": _int_metric(completion.get("open_task_count")),
        "auto_open_task_count": _int_metric(completion.get("auto_open_task_count")),
        "requires_codex_summary_count": _int_metric(completion.get("requires_codex_summary_count")),
        "worker_retry_later": bool(worker_retry.get("retry_later")),
        "worker_retry_later_count": _int_metric(worker_retry.get("retry_later_count")),
        "worker_retry_later_reasons": worker_retry.get("retry_later_reasons") if isinstance(worker_retry.get("retry_later_reasons"), list) else [],
        "worker_failed": bool(worker_failure.get("worker_failed")),
        "worker_failed_pass_count": _int_metric(worker_failure.get("worker_failed_pass_count")),
        "worker_failure_reasons": worker_failure.get("worker_failure_reasons") if isinstance(worker_failure.get("worker_failure_reasons"), list) else [],
        "open_task_stage_counts": open_task_stage_counts,
        "missing_stage_counts": missing_stage_counts,
        "stage_pressure": stage_pressure,
        "stage_pressure_notes": _stage_pressure_notes(stage_pressure),
        "top_field_gaps": completion.get("top_field_gaps") if isinstance(completion.get("top_field_gaps"), list) else [],
        "field_gap_notes": completion.get("field_gap_notes") if isinstance(completion.get("field_gap_notes"), list) else [],
        "feishu_sync": {
            "enabled": sync_enabled,
            "ok": sync_result.get("ok") if sync_result else None,
            "run_status": sync_result.get("run_status") or sync_result.get("stage") or ("not_synced" if not sync_enabled else ""),
            "dry_run": bool(sync_result.get("dry_run")),
            "audit_output": bool(sync_result.get("audit_output")),
            "output_candidates": _int_metric(sync_result.get("output_candidates")),
            "rows": _int_metric(sync_result.get("rows")),
            "skipped": sync_result.get("skipped", False),
        },
        "next_actions": next_actions[:4],
    }
    threshold_result = evaluate_quality_thresholds(summary, thresholds)
    summary["quality_thresholds"] = threshold_result
    for action in threshold_result.get("next_actions") or []:
        if action not in next_actions:
            next_actions.append(action)
    summary["next_actions"] = next_actions[:4]
    summary["completion_blockers"] = completion_blockers_for_summary(summary)
    return summary


def emit_result(result: dict[str, Any]) -> None:
    quality_summary = result.get("quality_summary")
    if isinstance(quality_summary, dict):
        result["completion_blockers"] = quality_summary.get("completion_blockers", [])
    print(json.dumps(result, ensure_ascii=False, indent=2))


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
    parser.add_argument(
        "--max-resume-passes",
        type=int,
        default=DEFAULT_MAX_RESUME_PASSES,
        help="Maximum enrichment passes inside one account job; 0 uses the default automatic budget.",
    )
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
    parser.add_argument("--max-snapshots", type=int, default=32)
    parser.add_argument("--min-snapshots", type=int, default=6)
    parser.add_argument("--expected-post-count", type=int, default=0)
    parser.add_argument("--expected-labels", default="", help="Comma-separated visible relative-time labels from the operator checklist.")
    parser.add_argument(
        "--fail-on-incomplete",
        action="store_true",
        help="Return a nonzero exit code when run_status is not complete, even if ledger sync itself succeeded.",
    )
    parser.add_argument(
        "--allow-incomplete-success",
        action="store_true",
        help="Compatibility mode: return 0 for incomplete ledger/status reports. Business capture jobs should not use this.",
    )
    parser.add_argument(
        "--require-coverage-complete",
        action="store_true",
        help="Fail the account job when homepage/expected coverage is not complete.",
    )
    parser.add_argument("--min-ledger-usable-rate", type=float, default=0.0, help="Fail when ledger candidate rate is below this 0-1 threshold.")
    parser.add_argument("--min-final-usable-rate", type=float, default=0.0, help="Fail when strict final usable rate is below this 0-1 threshold.")
    parser.add_argument("--min-completion-rate", type=float, default=0.0, help="Fail when enrichment completion rate is below this 0-1 threshold.")
    parser.add_argument(
        "--min-expected-post-coverage-rate",
        type=float,
        default=0.0,
        help="Fail when expected-post-count coverage is below this 0-1 threshold.",
    )
    parser.add_argument(
        "--min-expected-label-coverage-rate",
        type=float,
        default=0.0,
        help="Fail when expected-label coverage is below this 0-1 threshold.",
    )
    args = parser.parse_args()

    started = time.monotonic()
    config = load_config(args.config)
    timezone_name = str(config.get("timezone") or "Asia/Shanghai")
    target_dates = [normalize_date_text(args.target_date)] if args.target_date else dates_for_last_hours(args.last_hours, timezone_name=timezone_name)

    database_path = config.get("database_path", "data/posts.sqlite")
    try:
        conn = connect(database_path)
    except sqlite3.Error as exc:
        run_status = "import_failed"
        partial_result = {
            "ok": False,
            "stage": "sqlite_connect",
            "run_status": run_status,
            "complete": False,
            "message": "本地内容库不可打开；已在 Facebook 采集、补抓和飞书写入前停止。",
            "error": str(exc),
            "database_path": str(database_path),
            "target_dates": target_dates,
            "account_url": args.account_url,
            "account_name": args.account_name,
            "account_type": args.account_type,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
        partial_result["quality_summary"] = account_job_quality_summary(
            run_status=run_status,
            discover_coverage={"source": "not_run", "complete": False, "incomplete": True, "reasons": ["sqlite_connect"]},
            completion={},
            sync_result={},
            thresholds=quality_thresholds_from_args(args),
        )
        partial_result["next_commands"] = next_commands_for_status(
            args=args,
            target_dates=target_dates,
            run_status=run_status,
            completion={},
            discover_coverage={"source": "not_run", "complete": False, "incomplete": True, "reasons": ["sqlite_connect"]},
        )
        emit_result(partial_result)
        return 1
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
            completion = enrichment_completion_summary(conn, current_posts, config)
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
            partial_result["quality_summary"] = account_job_quality_summary(
                run_status=run_status,
                discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
                completion=completion,
                sync_result={
                    "ok": False,
                    "stage": "feishu_auth_preflight",
                    "run_status": "blocked_auth",
                    "next_actions": ["完成飞书用户授权后，重新运行同一账号作业。"],
                },
                thresholds=quality_thresholds_from_args(args),
            )
            partial_result["next_commands"] = next_commands_for_status(
                args=args,
                target_dates=target_dates,
                run_status=run_status,
                completion=completion,
                discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
            )
            emit_result(partial_result)
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
            completion = enrichment_completion_summary(conn, current_posts, config)
            run_status = "blocked_opencli"
            partial_result = {
                "ok": False,
                "run_status": run_status,
                "complete": False,
                "message": "OpenCLI Browser Bridge 未就绪；已在 Facebook 实时采集前停止。可修复后用同一命令续跑。",
                "target_dates": target_dates,
                "opencli_browser_bridge": opencli_preflight,
                "enrichment_completion": completion,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
            partial_result["quality_summary"] = account_job_quality_summary(
                run_status=run_status,
                discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
                completion=completion,
                sync_result={},
                thresholds=quality_thresholds_from_args(args),
            )
            partial_result["next_commands"] = next_commands_for_status(
                args=args,
                target_dates=target_dates,
                run_status=run_status,
                completion=completion,
                discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
            )
            emit_result(partial_result)
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
            completion = enrichment_completion_summary(conn, current_posts, config)
            run_status = (
                "human_intervention_required"
                if needs_human_intervention(discover_import)
                else discover_import.get("run_status") or discover_import.get("stage") or "discover_failed"
            )
            discover_coverage = discover_coverage_summary(discover_import)
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
            partial_result["quality_summary"] = account_job_quality_summary(
                run_status=run_status,
                discover_coverage=discover_coverage,
                completion=completion,
                sync_result={},
                thresholds=quality_thresholds_from_args(args),
            )
            partial_result["next_commands"] = next_commands_for_status(
                args=args,
                target_dates=target_dates,
                run_status=run_status,
                completion=completion,
                discover_coverage=discover_coverage,
            )
            emit_result(partial_result)
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
    enqueue_enrichment_tasks_for_posts(conn, posts, config)
    completion_before_worker = enrichment_completion_summary(conn, posts, config)
    if args.resume_only and not args.status_only and completion_requires_opencli(completion_before_worker):
        opencli_preflight = check_opencli(
            config.get("opencli_command") or [config.get("opencli_path", "opencli")],
            daemon_port=int(config.get("opencli_daemon_port", 19825) or 19825),
            auto_fix=True,
        )
        if not opencli_preflight.get("ok"):
            preflight_worker_passes: list[dict[str, Any]] = []
            if _int_metric((completion_before_worker.get("open_task_stage_counts") or {}).get("article_material")) > 0:
                preflight_worker_passes.append(
                    run_worker_pass(
                        args,
                        target_dates=target_dates,
                        pass_index=1,
                        stages="article_material",
                    )
                )
                posts = scoped_posts(
                    conn,
                    account_name=args.account_name,
                    account_url=args.account_url,
                    account_type=args.account_type,
                    dates=target_dates,
                )
                enqueue_enrichment_tasks_for_posts(conn, posts, config)
                completion_before_worker = enrichment_completion_summary(conn, posts, config)
            preflight_summary_auto_apply = auto_generate_and_apply_summaries(args, target_dates, completion_before_worker)
            if preflight_summary_auto_apply.get("ok") and not preflight_summary_auto_apply.get("skipped"):
                posts = scoped_posts(
                    conn,
                    account_name=args.account_name,
                    account_url=args.account_url,
                    account_type=args.account_type,
                    dates=target_dates,
                )
                enqueue_enrichment_tasks_for_posts(conn, posts, config)
                completion_before_worker = enrichment_completion_summary(conn, posts, config)
                sync_result = run_sync(config, args, posts, conn)
                posts = scoped_posts(
                    conn,
                    account_name=args.account_name,
                    account_url=args.account_url,
                    account_type=args.account_type,
                    dates=target_dates,
                )
                completion_before_worker = enrichment_completion_summary(conn, posts, config)
            else:
                sync_result = {}
            run_status = "blocked_opencli"
            partial_result = {
                "ok": False,
                "run_status": run_status,
                "complete": False,
                "message": "OpenCLI Browser Bridge 未就绪；已在详情补抓前停止，避免把可预判的环境问题写成补抓失败任务。",
                "target_dates": target_dates,
                "account_url": args.account_url,
                "account_name": args.account_name,
                "account_type": args.account_type,
                "post_count": len(posts),
                "task_counts": task_counts_for_posts(conn, posts),
                "recovered_running_tasks": recovered_running_tasks,
                "opencli_preflight": opencli_preflight,
                "preflight_worker_passes": preflight_worker_passes,
                "summary_auto_apply": preflight_summary_auto_apply,
                "feishu_sync": sync_result,
                "enrichment_completion": completion_before_worker,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
            partial_result["quality_summary"] = account_job_quality_summary(
                run_status=run_status,
                discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
                completion=completion_before_worker,
                sync_result=sync_result,
                thresholds=quality_thresholds_from_args(args),
            )
            partial_result["next_commands"] = next_commands_for_status(
                args=args,
                target_dates=target_dates,
                run_status=run_status,
                completion=completion_before_worker,
                discover_coverage={"source": "not_run", "complete": True, "incomplete": False, "reasons": []},
            )
            emit_result(partial_result)
            return 1
    worker_passes, posts = run_worker_passes_for_job(args, conn, posts, config, target_dates)

    posts = scoped_posts(
        conn,
        account_name=args.account_name,
        account_url=args.account_url,
        account_type=args.account_type,
        dates=target_dates,
    )
    completion_after_worker = enrichment_completion_summary(conn, posts, config)
    summary_auto_apply = auto_generate_and_apply_summaries(args, target_dates, completion_after_worker)
    if summary_auto_apply.get("ok") and not summary_auto_apply.get("skipped"):
        posts = scoped_posts(
            conn,
            account_name=args.account_name,
            account_url=args.account_url,
            account_type=args.account_type,
            dates=target_dates,
        )
        enqueue_enrichment_tasks_for_posts(conn, posts, config)
    sync_result = run_sync(config, args, posts, conn)
    posts = scoped_posts(
        conn,
        account_name=args.account_name,
        account_url=args.account_url,
        account_type=args.account_type,
        dates=target_dates,
    )
    completion = enrichment_completion_summary(conn, posts, config)
    retry_summary = worker_retry_summary(worker_passes)
    failure_summary = worker_failure_summary(worker_passes)
    summary_requirement = worker_summary_requirement_summary(worker_passes)
    run_status = summarize_job_status(
        preflight=opencli_preflight,
        discover_import=discover_import,
        worker_passes=worker_passes,
        sync_result=sync_result,
        completion=completion,
        summary_auto_apply=summary_auto_apply,
    )
    summary_requests_export = (
        summary_auto_apply.get("export")
        if isinstance(summary_auto_apply.get("export"), dict)
        else export_summary_requests_for_job(args, target_dates, completion)
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
        "worker_retry_later": retry_summary["retry_later"],
        "worker_retry_later_count": retry_summary["retry_later_count"],
        "worker_retry_later_reasons": retry_summary["retry_later_reasons"],
        "worker_failed": failure_summary["worker_failed"],
        "worker_failed_pass_count": failure_summary["worker_failed_pass_count"],
        "worker_failure_reasons": failure_summary["worker_failure_reasons"],
        "worker_codex_summary_required": summary_requirement["codex_summary_required"],
        "worker_codex_summary_required_count": summary_requirement["codex_summary_required_count"],
        "worker_codex_summary_required_urls": summary_requirement["codex_summary_required_urls"],
        "summary_auto_apply": summary_auto_apply,
        "summary_requests_export": summary_requests_export,
        "worker_passes": worker_passes,
        "feishu_sync": sync_result,
        "enrichment_completion": completion,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
    result["quality_summary"] = account_job_quality_summary(
        run_status=run_status,
        discover_coverage=result["discover_coverage"],
        completion=completion,
        sync_result=sync_result,
        thresholds=quality_thresholds_from_args(args),
        worker_retry=retry_summary,
        worker_failure=failure_summary,
    )
    if not result["quality_summary"]["quality_thresholds"]["ok"]:
        result["complete"] = False
        result["quality_threshold_failed"] = True
        result["quality_threshold_failures"] = result["quality_summary"]["quality_thresholds"]["failures"]
        if run_status == "complete":
            result["run_status"] = "quality_threshold_failed"
            result["quality_summary"]["run_status"] = "quality_threshold_failed"
            result["quality_summary"]["complete"] = False
        result["quality_summary"]["completion_blockers"] = completion_blockers_for_summary(result["quality_summary"])
    result["next_commands"] = next_commands_for_status(
        args=args,
        target_dates=target_dates,
        run_status=result["run_status"],
        completion=completion,
        discover_coverage=result["discover_coverage"],
    )
    for action in result["quality_summary"]["quality_thresholds"].get("next_actions") or []:
        if action and all(action != item.get("description") for item in result["next_commands"]):
            result["next_commands"].append({"reason": "quality_threshold_failed", "description": action, "command": result["next_commands"][0]["command"] if result["next_commands"] else ""})
    result["next_commands"] = result["next_commands"][:4]
    strict_completion_exit = not args.allow_incomplete_success
    if strict_completion_exit and result["run_status"] != "complete" and sync_result.get("ok", True):
        result["exit_status_reason"] = "incomplete_run_status"
    if result.get("quality_threshold_failed") and sync_result.get("ok", True):
        result["exit_status_reason"] = "quality_threshold_failed"
    emit_result(result)
    if result.get("quality_threshold_failed"):
        return 2
    if strict_completion_exit and result["run_status"] != "complete":
        return 2
    return 0 if sync_result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
