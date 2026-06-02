#!/usr/bin/env python3
"""Fast capture pipeline: discover visible candidates, import them, and queue enrichment."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path

from check_env import check_opencli
from config_loader import load_config
from coverage_expectations import apply_expected_coverage, split_expected_labels
from lark_io import ensure_user_identity
from models import normalize_date
from store import connect, query_posts
from sync_status import blocked_auth_result, completion_run_status, enrichment_completion_summary


ROOT = Path(__file__).resolve().parents[1]


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
    return list(completion.get("next_actions") or [])


def run_command(command: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False, timeout=timeout)


def parse_stdout_json(result: subprocess.CompletedProcess[str]) -> dict:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "stdout": result.stdout, "stderr": result.stderr}


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
    parser.add_argument("--expected-post-count", type=int, default=0)
    parser.add_argument("--expected-labels", default="", help="Comma-separated visible relative-time labels from the operator checklist.")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.sync_partial and not args.dry_run:
        try:
            ensure_user_identity(config)
        except RuntimeError as exc:
            print(
                json.dumps(
                    {
                        **blocked_auth_result(
                            "飞书真实写入前置检查失败；已在 Facebook 采集前停止。",
                            str(exc),
                        ),
                        "next_actions": capture_pipeline_next_actions("blocked_auth", {}),
                    },
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
                {
                    "ok": False,
                    "stage": "opencli_preflight",
                    "run_status": "blocked_opencli",
                    "complete": False,
                    "message": "OpenCLI Browser Bridge 未就绪；已在 Facebook 采集前停止。",
                    "opencli_browser_bridge": opencli_preflight,
                    "next_actions": capture_pipeline_next_actions("blocked_opencli", {}),
                },
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
        discover = run_command(
            [
                "node",
                "scripts/opencli_extract_current_tab.mjs",
                "--config",
                args.config,
                "--account-url",
                args.account_url,
                "--max-text",
                args.max_text,
            ]
        )
        discover_payload = parse_stdout_json(discover)
        if discover.returncode != 0 or not discover_payload.get("ok"):
            print(
                json.dumps(
                    {
                        "ok": False,
                        "stage": "discover",
                        "run_status": "discover_failed",
                        "complete": False,
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                        "discover_elapsed_ms": int((time.monotonic() - discover_started) * 1000),
                        "result": discover_payload,
                        "next_actions": capture_pipeline_next_actions("discover_failed", {}),
                    },
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
        prepare = run_command(
            [
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
        )
        if prepare.returncode != 0:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "stage": "prepare",
                        "stdout": prepare.stdout,
                        "stderr": prepare.stderr,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return prepare.returncode

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
        if imported.returncode != 0:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "stage": "import",
                        "stdout": imported.stdout,
                        "stderr": imported.stderr,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return imported.returncode

        prepared_payload = json.loads(prepared_path.read_text(encoding="utf-8"))
        conn = connect(config.get("database_path", "data/posts.sqlite"))
        scoped_posts = query_posts(
            conn,
            date=normalize_date(args.target_date) if args.target_date else "",
            account_name=args.account_name,
            account_url=args.account_url,
            account_type=args.account_type,
        )
        completion = enrichment_completion_summary(conn, scoped_posts)
        run_status = capture_pipeline_run_status(discover_payload, completion)
        result = {
            "ok": True,
            "mode": "partial" if args.partial else "standard",
            "target_date": args.target_date,
            "raw_candidate_count": discover_payload.get("raw_candidate_count", 0),
            "post_count": discover_payload.get("post_count", 0),
            "capture_complete": discover_payload.get("capture_complete", True),
            "coverage": discover_payload.get("coverage", {}),
            "expected_coverage": (discover_payload.get("coverage") or {}).get("expected", {}),
            "prepared": prepared_payload.get("prepared", 0),
            "coverage_note": prepared_payload.get("coverage_note", ""),
            "ready_for_output": prepared_payload.get("ready_for_output", 0),
            "partial_review": prepared_payload.get("partial_review", 0),
            "needs_enrichment": prepared_payload.get("needs_enrichment", 0),
            "enrichment_completion": completion,
            "complete": run_status == "complete",
            "run_status": run_status,
            "next_actions": capture_pipeline_next_actions(run_status, completion),
            "timing_ms": {
                "discover": int((time.monotonic() - discover_started) * 1000),
                "prepare": int((time.monotonic() - prepare_started) * 1000),
                "import": int((time.monotonic() - import_started) * 1000),
                "total": int((time.monotonic() - started) * 1000),
            },
            "import_stdout": imported.stdout,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
