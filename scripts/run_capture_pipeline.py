#!/usr/bin/env python3
"""Fast capture pipeline: discover visible candidates, import them, and queue enrichment."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import tempfile
import time
from json import JSONDecodeError
from pathlib import Path

from check_env import check_opencli
from config_loader import load_config
from coverage_expectations import apply_expected_coverage, split_expected_labels
from lark_io import ensure_user_identity
from models import normalize_date
from run_account_job import (
    account_job_quality_summary,
    completion_blockers_for_summary,
    discover_coverage_summary,
    discover_homepage_with_retry,
    next_commands_for_status,
    quality_thresholds_from_args,
)
from store import connect, query_posts
from sync_status import blocked_auth_result, completion_run_status, enrichment_completion_summary


ROOT = Path(__file__).resolve().parents[1]
HUMAN_INTERVENTION_STATUSES = {"human_intervention_required", "login_required", "visitor_preview", "facebook_tab_missing"}


def capture_pipeline_run_status(discover_payload: dict, completion: dict) -> str:
    coverage = discover_payload.get("coverage") if isinstance(discover_payload.get("coverage"), dict) else {}
    if discover_payload.get("coverage_blocked") or coverage.get("coverage_blocked"):
        return "coverage_incomplete"
    if discover_payload.get("coverage_incomplete") or coverage.get("coverage_incomplete"):
        return "coverage_incomplete"
    if discover_payload.get("capture_complete") is False or coverage.get("capture_complete") is False:
        return "coverage_incomplete"
    if not completion.get("post_count"):
        return "no_candidates"
    return completion_run_status(completion, ledger_mode=False)


def needs_human_intervention(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("human_intervention_required") or payload.get("action_required") == "human_intervention_required":
        return True
    if str(payload.get("status") or payload.get("run_status") or "") in HUMAN_INTERVENTION_STATUSES:
        return True
    nested = payload.get("result")
    if isinstance(nested, dict) and needs_human_intervention(nested):
        return True
    return False


def capture_pipeline_next_actions(run_status: str, completion: dict) -> list[str]:
    if run_status == "coverage_incomplete":
        return ["覆盖未完成：从账号主页顶部使用 run_account_job.py 重跑，必要时提高 --max-snapshots。"]
    if run_status == "no_candidates":
        return ["当前没有可入库候选；确认目标账号主页顶部帖子已加载后再运行 run_account_job.py。"]
    if run_status == "blocked_opencli":
        return ["先运行 python3 scripts/check_env.py --config config/settings.yaml --fix-opencli；Browser Bridge 恢复后从账号主页顶部重跑 run_account_job.py。"]
    if run_status == "blocked_auth":
        return ["完成飞书用户授权或等待自动刷新恢复后，重新运行同一命令；本次未开始 Facebook 采集。"]
    if run_status == "discover_failed":
        return ["Facebook 主页发现阶段失败；确认目标账号主页已在正常 Chrome 打开并已登录，再用 run_account_job.py 从主页顶部重跑。"]
    if run_status == "human_intervention_required":
        return ["Facebook 登录态、Chrome Profile、访客预览或页面可见性异常；先在正常 Chrome 打开目标主页并确认真实帖子可见，再从主页顶部重跑 run_account_job.py。"]
    if run_status == "prepare_failed":
        return ["主页候选已发现但标准化失败；保留错误输出，修复 prepare_capture_result.py 或输入结构后从账号主页顶部重跑 run_account_job.py。"]
    if run_status == "import_failed":
        return ["标准化候选已生成但本地入库失败；修复导入错误后重新运行同一账号采集命令，已发现候选不得手动当作完成。"]
    return list(completion.get("next_actions") or [])


def capture_pipeline_command_args(args: argparse.Namespace) -> argparse.Namespace:
    values = vars(args).copy()
    values["sync"] = bool(args.sync_partial)
    values["strict_ready_only"] = False
    values["resume_only"] = False
    return argparse.Namespace(**values)


def capture_pipeline_next_commands(
    args: argparse.Namespace,
    *,
    run_status: str,
    completion: dict,
    discover_coverage: dict | None = None,
) -> list[dict]:
    if run_status == "complete":
        return []
    command_status = "no_work" if run_status in {"discover_failed", "no_candidates"} else run_status
    return next_commands_for_status(
        args=capture_pipeline_command_args(args),
        target_dates=[normalize_date(args.target_date) if args.target_date else ""],
        run_status=command_status,
        completion=completion,
        discover_coverage=discover_coverage or {},
    )


def sync_result_from_import_payload(import_payload: dict, *, dry_run: bool) -> dict:
    if isinstance(import_payload.get("feishu_sync"), dict):
        return import_payload["feishu_sync"]
    return {
        "ok": True,
        "skipped": True,
        "stage": "sync_disabled",
        "run_status": "not_synced",
        "dry_run": dry_run,
    }


def discover_report_for_quality(discover_payload: dict) -> dict:
    return {
        "ok": True,
        "stage": "discover_import",
        "discover": {
            "raw_candidate_count": discover_payload.get("raw_candidate_count", 0),
            "post_count": discover_payload.get("post_count", 0),
            "capture_complete": discover_payload.get("capture_complete", True),
            "coverage": discover_payload.get("coverage", {}),
            "coverage_blocked": discover_payload.get("coverage_blocked", False),
            "coverage_incomplete": discover_payload.get("coverage_incomplete", False),
            "expected_coverage": (discover_payload.get("coverage") or {}).get("expected", {}),
        },
    }


def run_command(command: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False, timeout=timeout)


def parse_stdout_json(result: subprocess.CompletedProcess[str]) -> dict:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "stdout": result.stdout, "stderr": result.stderr}


def capture_pipeline_failed_result(
    *,
    stage: str,
    run_status: str,
    message: str,
    error: str = "",
    next_actions: list[str] | None = None,
    next_commands: list[dict] | None = None,
    **extra: object,
) -> dict:
    payload = {
        "ok": False,
        "stage": stage,
        "run_status": run_status,
        "complete": False,
        "message": message,
        "next_actions": next_actions if next_actions is not None else capture_pipeline_next_actions(run_status, {}),
    }
    if error:
        payload["error"] = error
    if next_commands is not None:
        payload["next_commands"] = next_commands
    payload.update(extra)
    payload["completion_blockers"] = [
        {
            "code": run_status,
            "label": {
                "blocked_auth": "飞书授权阻塞",
                "blocked_opencli": "OpenCLI 未就绪",
                "human_intervention_required": "需要人工恢复登录/Profile",
                "discover_failed": "主页发现失败",
                "prepare_failed": "候选标准化失败",
                "import_failed": "本地入库失败",
                "coverage_incomplete": "覆盖不足",
            }.get(run_status, run_status),
            "severity": "hard_blocker" if run_status in {"blocked_auth", "blocked_opencli", "human_intervention_required"} else "operational",
            "priority": 0,
            "recoverable": True,
            "message": message,
            "next_action": payload["next_actions"][0] if payload.get("next_actions") else "",
            "metrics": {
                key: payload[key]
                for key in ("stage", "returncode", "prepared", "database_path", "target_date")
                if key in payload
            },
        }
    ]
    return payload


def emit_result(result: dict) -> None:
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
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--partial", action="store_true", help="Import candidates and allow partial_review preview.")
    parser.add_argument("--sync-partial", action="store_true", help="Dry-run/write partial preview through import script.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-text", default="1500")
    parser.add_argument("--max-snapshots", type=int, default=32)
    parser.add_argument("--min-snapshots", type=int, default=6)
    parser.add_argument("--scroll-pixels", type=int, default=520)
    parser.add_argument("--posted-after", default="")
    parser.add_argument("--posted-before", default="")
    parser.add_argument("--max-resume-passes", type=int, default=8)
    parser.add_argument("--enrichment-limit", type=int, default=50)
    parser.add_argument("--resume-stale-running-seconds", type=int, default=1800)
    parser.add_argument("--expected-post-count", type=int, default=0)
    parser.add_argument("--expected-labels", default="", help="Comma-separated visible relative-time labels from the operator checklist.")
    parser.add_argument(
        "--fail-on-incomplete",
        action="store_true",
        help="Return nonzero when run_status is not complete, even if partial import succeeded.",
    )
    parser.add_argument(
        "--require-coverage-complete",
        action="store_true",
        help="Fail the pipeline when homepage/expected coverage is not complete.",
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

    try:
        config = load_config(args.config)
    except (FileNotFoundError, JSONDecodeError, ValueError, TypeError) as exc:
        print(
            json.dumps(
                capture_pipeline_failed_result(
                    stage="config_load",
                    run_status="import_failed",
                    message="配置文件读取失败；已在 Facebook 采集、导入和飞书写入前停止。",
                    error=str(exc),
                    config_path=args.config,
                    account_url=args.account_url,
                    target_date=args.target_date,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    if args.sync_partial and not args.dry_run:
        try:
            ensure_user_identity(config)
        except RuntimeError as exc:
            auth_blocker = blocked_auth_result(
                "飞书真实写入前置检查失败；已在 Facebook 采集前停止。",
                str(exc),
            )
            print(
                json.dumps(
                    capture_pipeline_failed_result(
                        stage="feishu_auth_preflight",
                        run_status="blocked_auth",
                        message=auth_blocker["message"],
                        error=auth_blocker.get("error", ""),
                        next_actions=capture_pipeline_next_actions("blocked_auth", {}),
                        next_commands=capture_pipeline_next_commands(
                            args,
                            run_status="blocked_auth",
                            completion={},
                            discover_coverage={"source": "not_run", "complete": False, "incomplete": True, "reasons": []},
                        ),
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
    opencli_preflight = check_opencli(
        config.get("opencli_command") or [config.get("opencli_path", "opencli")],
        daemon_port=int(config.get("opencli_daemon_port", 19825) or 19825),
        auto_fix=True,
    )
    if not opencli_preflight.get("ok"):
        print(
            json.dumps(
                capture_pipeline_failed_result(
                    stage="opencli_preflight",
                    run_status="blocked_opencli",
                    message="OpenCLI Browser Bridge 未就绪；已在 Facebook 采集前停止。",
                    next_commands=capture_pipeline_next_commands(
                        args,
                        run_status="blocked_opencli",
                        completion={},
                        discover_coverage={"source": "not_run", "complete": False, "incomplete": True, "reasons": []},
                    ),
                    opencli_browser_bridge=opencli_preflight,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="fb-capture-pipeline-") as temp_dir:
        temp = Path(temp_dir)
        raw_path = temp / "raw.json"
        prepared_path = temp / "prepared.json"

        discover_started = time.monotonic()
        discover, discover_payload, discover_retry = discover_homepage_with_retry(args)
        if discover.returncode != 0 or not discover_payload.get("ok"):
            run_status = "human_intervention_required" if needs_human_intervention(discover_payload) else "discover_failed"
            discover_coverage = {
                "source": "discover",
                "complete": False,
                "incomplete": True,
                "reasons": ["human_intervention_required" if run_status == "human_intervention_required" else "discover_failed_before_import"],
                "raw_candidate_count": discover_payload.get("raw_candidate_count", 0),
                "post_count": discover_payload.get("post_count", 0),
            }
            print(
                json.dumps(
                    capture_pipeline_failed_result(
                        stage=run_status if run_status == "human_intervention_required" else "discover",
                        run_status=run_status,
                        message="Facebook 页面需要人工处理登录态或可见页面后再续跑。"
                        if run_status == "human_intervention_required"
                        else "Facebook 主页发现阶段失败；本次未导入本地库，也未写入飞书。",
                        human_intervention_required=run_status == "human_intervention_required",
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                        discover_elapsed_ms=int((time.monotonic() - discover_started) * 1000),
                        result=discover_payload,
                        discover_retry=discover_retry,
                        next_commands=capture_pipeline_next_commands(
                            args,
                            run_status=run_status,
                            completion={},
                            discover_coverage=discover_coverage,
                        ),
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return discover.returncode or 1
        discover_payload = apply_expected_coverage(
            discover_payload,
            expected_post_count=int(args.expected_post_count or 0),
            expected_labels=split_expected_labels(args.expected_labels),
        )
        raw_path.write_text(json.dumps(discover_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        prepare_started = time.monotonic()
        prepare_command = [
            "python3",
            "scripts/prepare_capture_result.py",
            "--input",
            str(raw_path),
            "--output",
            str(prepared_path),
            "--target-date",
            args.target_date,
            "--account-url",
            args.account_url,
            "--account-name",
            args.account_name,
            "--account-type",
            args.account_type,
        ]
        if args.posted_after:
            prepare_command.extend(["--posted-after", args.posted_after])
        if args.posted_before:
            prepare_command.extend(["--posted-before", args.posted_before])
        prepare = run_command(prepare_command)
        if prepare.returncode != 0:
            discover_coverage = discover_coverage_summary(discover_report_for_quality(discover_payload))
            print(
                json.dumps(
                    capture_pipeline_failed_result(
                        stage="prepare",
                        run_status="prepare_failed",
                        message="主页候选发现后标准化失败；本次未导入本地库，也未写入飞书。",
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                        discover={
                            "raw_candidate_count": discover_payload.get("raw_candidate_count", 0),
                            "post_count": discover_payload.get("post_count", 0),
                            "capture_complete": discover_payload.get("capture_complete", True),
                            "coverage": discover_payload.get("coverage", {}),
                            "coverage_blocked": discover_payload.get("coverage_blocked", False),
                            "coverage_incomplete": discover_payload.get("coverage_incomplete", False),
                            "auto_retry": (discover_payload.get("coverage") or {}).get("auto_retry", {}),
                        },
                        stdout=prepare.stdout,
                        stderr=prepare.stderr,
                        returncode=prepare.returncode,
                        next_commands=capture_pipeline_next_commands(
                            args,
                            run_status="prepare_failed",
                            completion={},
                            discover_coverage=discover_coverage,
                        ),
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return prepare.returncode
        try:
            prepared_payload = json.loads(prepared_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, JSONDecodeError, UnicodeDecodeError) as exc:
            discover_coverage = discover_coverage_summary(discover_report_for_quality(discover_payload))
            print(
                json.dumps(
                    capture_pipeline_failed_result(
                        stage="prepare",
                        run_status="prepare_failed",
                        message="候选标准化命令返回成功，但输出文件不可读取；本次未导入本地库，也未写入飞书。",
                        error=str(exc),
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                        discover={
                            "raw_candidate_count": discover_payload.get("raw_candidate_count", 0),
                            "post_count": discover_payload.get("post_count", 0),
                            "capture_complete": discover_payload.get("capture_complete", True),
                            "coverage": discover_payload.get("coverage", {}),
                            "coverage_blocked": discover_payload.get("coverage_blocked", False),
                            "coverage_incomplete": discover_payload.get("coverage_incomplete", False),
                            "auto_retry": (discover_payload.get("coverage") or {}).get("auto_retry", {}),
                        },
                        output_path=str(prepared_path),
                        next_commands=capture_pipeline_next_commands(
                            args,
                            run_status="prepare_failed",
                            completion={},
                            discover_coverage=discover_coverage,
                        ),
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1

        import_started = time.monotonic()
        import_command = [
            "python3",
            "scripts/import_existing_result.py",
            "--config",
            args.config,
            "--input",
            str(prepared_path),
            "--account-url",
            args.account_url,
            "--account-name",
            args.account_name,
            "--account-type",
            args.account_type,
        ]
        if args.sync_partial:
            import_command.append("--sync-partial")
            if args.dry_run:
                import_command.append("--dry-run")
        else:
            import_command.append("--no-sync")
        imported = run_command(import_command)
        import_payload = parse_stdout_json(imported)
        if imported.returncode != 0:
            discover_coverage = discover_coverage_summary(discover_report_for_quality(discover_payload))
            print(
                json.dumps(
                    capture_pipeline_failed_result(
                        stage="import",
                        run_status="import_failed",
                        message="候选标准化后本地入库失败；本次未完成采集作业，不能把已有输出视为最终结果。",
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                        prepared=prepared_payload.get("prepared", 0),
                        stdout=imported.stdout,
                        stderr=imported.stderr,
                        returncode=imported.returncode,
                        next_commands=capture_pipeline_next_commands(
                            args,
                            run_status="import_failed",
                            completion={},
                            discover_coverage=discover_coverage,
                        ),
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return imported.returncode

        database_path = config.get("database_path", "data/posts.sqlite")
        try:
            conn = connect(database_path)
        except sqlite3.Error as exc:
            discover_coverage = discover_coverage_summary(discover_report_for_quality(discover_payload))
            print(
                json.dumps(
                    capture_pipeline_failed_result(
                        stage="sqlite_connect",
                        run_status="import_failed",
                        message="本地内容库不可打开；候选导入结果无法确认，本次采集作业未完成。",
                        error=str(exc),
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                        database_path=str(database_path),
                        prepared=prepared_payload.get("prepared", 0),
                        next_commands=capture_pipeline_next_commands(
                            args,
                            run_status="import_failed",
                            completion={},
                            discover_coverage=discover_coverage,
                        ),
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        scoped_posts = query_posts(
            conn,
            date=normalize_date(args.target_date) if args.target_date else "",
            account_name=args.account_name,
            account_url=args.account_url,
            account_type=args.account_type,
        )
        completion = enrichment_completion_summary(conn, scoped_posts, config)
        run_status = capture_pipeline_run_status(discover_payload, completion)
        sync_result = sync_result_from_import_payload(import_payload, dry_run=args.dry_run)
        discover_coverage = discover_coverage_summary(discover_report_for_quality(discover_payload))
        quality_summary = account_job_quality_summary(
            run_status=run_status,
            discover_coverage=discover_coverage,
            completion=completion,
            sync_result=sync_result,
            thresholds=quality_thresholds_from_args(args),
        )
        next_actions = capture_pipeline_next_actions(run_status, completion)
        for action in quality_summary["quality_thresholds"].get("next_actions") or []:
            if action and action not in next_actions:
                next_actions.append(action)
        result = {
            "ok": True,
            "mode": "partial" if args.partial else "standard",
            "target_date": args.target_date,
            "raw_candidate_count": discover_payload.get("raw_candidate_count", 0),
            "post_count": discover_payload.get("post_count", 0),
            "capture_complete": discover_payload.get("capture_complete", True),
            "coverage": discover_payload.get("coverage", {}),
            "expected_coverage": (discover_payload.get("coverage") or {}).get("expected", {}),
            "snapshot_budget": discover_payload.get("snapshot_budget", {}),
            "discover_retry": discover_retry,
            "prepared": prepared_payload.get("prepared", 0),
            "coverage_note": prepared_payload.get("coverage_note", ""),
            "ready_for_output": prepared_payload.get("ready_for_output", 0),
            "partial_review": prepared_payload.get("partial_review", 0),
            "needs_enrichment": prepared_payload.get("needs_enrichment", 0),
            "enrichment_completion": completion,
            "enrichment_tasks": import_payload.get("enrichment_tasks") if isinstance(import_payload, dict) else {},
            "feishu_sync": sync_result,
            "quality_summary": quality_summary,
            "complete": run_status == "complete",
            "run_status": run_status,
            "next_actions": next_actions[:4],
            "next_commands": capture_pipeline_next_commands(
                args,
                run_status=run_status,
                completion=completion,
                discover_coverage=discover_coverage,
            ),
            "timing_ms": {
                "discover": int((time.monotonic() - discover_started) * 1000),
                "prepare": int((time.monotonic() - prepare_started) * 1000),
                "import": int((time.monotonic() - import_started) * 1000),
                "total": int((time.monotonic() - started) * 1000),
            },
            "import_result": import_payload,
            "import_stdout": imported.stdout,
        }
        if not quality_summary["quality_thresholds"]["ok"]:
            result["complete"] = False
            result["quality_threshold_failed"] = True
            result["quality_threshold_failures"] = quality_summary["quality_thresholds"]["failures"]
            if run_status == "complete":
                result["run_status"] = "quality_threshold_failed"
                result["quality_summary"]["run_status"] = "quality_threshold_failed"
                result["quality_summary"]["complete"] = False
            result["quality_summary"]["completion_blockers"] = completion_blockers_for_summary(result["quality_summary"])
            result["next_commands"] = capture_pipeline_next_commands(
                args,
                run_status=result["run_status"],
                completion=completion,
                discover_coverage=discover_coverage,
            )
        if args.fail_on_incomplete and result["run_status"] != "complete":
            result["exit_status_reason"] = "incomplete_run_status"
        if result.get("quality_threshold_failed"):
            result["exit_status_reason"] = "quality_threshold_failed"
        emit_result(result)
        if result.get("quality_threshold_failed"):
            return 2
        if args.fail_on_incomplete and result["run_status"] != "complete":
            return 2
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
