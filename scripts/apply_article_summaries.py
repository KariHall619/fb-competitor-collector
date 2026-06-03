#!/usr/bin/env python3
"""Apply Codex-written Chinese article summaries to prepared posts."""

from __future__ import annotations

import argparse
import json
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from pipeline_status import crawl_status_for, output_status_for
from story_summary_policy import story_summary_errors
from config_loader import load_config
from store import all_posts, connect, mark_stage_done, query_posts, update_post_fields_with_audit


def summary_apply_failed_result(
    *,
    stage: str,
    message: str,
    error: str,
    summaries_path: str | Path | None = None,
    input_path: str | Path | None = None,
    output_path: str | Path | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "stage": stage,
        "run_status": "summary_apply_failed",
        "complete": False,
        "message": message,
        "error": error,
        "next_actions": [
            "修复 Codex 中文概要 JSON 或输入配置后重新运行同一命令；本次未更新本地库或输出文件。"
        ],
    }
    if summaries_path is not None:
        payload["summaries_path"] = str(summaries_path)
    if input_path is not None:
        payload["input_path"] = str(input_path)
    if output_path is not None:
        payload["output_path"] = str(output_path)
    if config_path is not None:
        payload["config_path"] = str(config_path)
    return payload


def load_summary_map(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Summaries file not found: {p}")
    summaries = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(summaries, dict):
        raise ValueError("Summaries input must be a JSON object keyed by post/canonical/article URL.")
    return summaries


def load_input_posts(path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Input file not found: {p}")
    payload = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Apply-summary file input must be a JSON object with a posts list.")
    posts = payload.get("posts", [])
    if not isinstance(posts, list):
        raise ValueError("Apply-summary input field posts must be a list.")
    if not all(isinstance(item, dict) for item in posts):
        raise ValueError("Every apply-summary post must be a JSON object.")
    return payload, posts


def summary_for_post(post: dict[str, Any], summaries: dict[str, Any]) -> str:
    keys = [
        post.get("post_url"),
        post.get("canonical_post_url"),
        post.get("landing_url"),
        post.get("article_url"),
    ]
    return next((summaries.get(key) for key in keys if key and summaries.get(key)), "")


def applied_fields(post: dict[str, Any], summary: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    next_post = {**post, "story_summary": summary.strip(), "summary_source": "article"}
    next_post["output_status"] = output_status_for(next_post, config)
    next_post["crawl_status"] = crawl_status_for(next_post, config)
    note = next_post.get("note") or ""
    next_post["note"] = "；".join(
        part
        for part in note.split("；")
        if part and part not in {"文章概要待生成", "故事概要需重新生成中文摘要"}
    )
    return {
        "story_summary": next_post["story_summary"],
        "summary_source": "article",
        "note": next_post["note"],
        "output_status": next_post["output_status"],
        "crawl_status": next_post["crawl_status"],
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


def scope_enabled(args: argparse.Namespace) -> bool:
    return any([args.date, args.start_date, args.end_date, args.account_name, args.account_url, args.account_type])


def scoped_posts(conn: Any, args: argparse.Namespace) -> list[dict[str, Any]]:
    if not scope_enabled(args):
        return all_posts(conn)
    return query_posts(
        conn,
        date=args.date,
        start_date=args.start_date,
        end_date=args.end_date,
        include_unknown_date=bool(args.date or args.start_date or args.end_date),
        account_name=args.account_name,
        account_url=args.account_url,
        account_type=args.account_type,
    )


def apply_scope_payload(args: argparse.Namespace, source_post_count: int) -> dict[str, Any]:
    return {
        "enabled": scope_enabled(args),
        "date": args.date,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "account_name": args.account_name,
        "account_url": args.account_url,
        "account_type": args.account_type,
        "source_post_count": source_post_count,
    }


def next_commands_after_sqlite_apply(args: argparse.Namespace) -> list[dict[str, str]]:
    if not args.config or not args.account_url:
        return []
    command: list[Any] = [
        "python3",
        "scripts/run_account_job.py",
        "--config",
        args.config,
    ]
    command.extend(["--account-url", args.account_url])
    if args.account_name:
        command.extend(["--account-name", args.account_name])
    if args.account_type:
        command.extend(["--account-type", args.account_type])
    if args.date:
        command.extend(["--target-date", args.date])
    command.extend(["--resume-only", "--force-recover-running", "--sync"])
    if args.dry_run:
        command.append("--dry-run")
    return [
        {
            "reason": "resume_account_job_after_summary_apply",
            "description": "中文概要已应用；继续同账号补抓/同步，让最终可用率和飞书台账更新到最新状态。",
            "command": command_text(command),
        }
    ]


def apply_to_posts(
    posts: list[dict[str, Any]],
    summaries: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    applied = 0
    missing = []
    rejected = []
    fields_by_key: dict[str, dict[str, Any]] = {}
    for post in posts:
        summary = summary_for_post(post, summaries)
        if not summary:
            missing.append(post.get("post_url"))
            continue
        candidate = {**post, "story_summary": str(summary).strip(), "summary_source": "article"}
        errors = story_summary_errors(candidate)
        if errors:
            rejected.append({"post_url": post.get("post_url"), "errors": errors})
            post["output_status"] = output_status_for(post, config)
            post["crawl_status"] = crawl_status_for(post, config)
            continue
        fields = applied_fields(post, str(summary), config)
        post.update(fields)
        key = str(post.get("canonical_post_url") or post.get("post_url") or "")
        if key:
            fields_by_key[key] = fields
        applied += 1
    return {"applied": applied, "missing": missing, "rejected": rejected, "fields_by_key": fields_by_key}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="")
    parser.add_argument("--input", default="")
    parser.add_argument("--summaries", required=True, help="JSON object keyed by post_url/canonical_post_url/article_url")
    parser.add_argument("--output", default="")
    parser.add_argument("--date", default="")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--account-name", default="")
    parser.add_argument("--account-url", default="")
    parser.add_argument("--account-type", default="")
    parser.add_argument("--dry-run", action="store_true", help="Only affects emitted next_commands; summary application still updates SQLite.")
    args = parser.parse_args()

    try:
        summaries = load_summary_map(args.summaries)
    except (FileNotFoundError, JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        print(
            json.dumps(
                summary_apply_failed_result(
                    stage="summaries_load",
                    message="Codex 中文概要 JSON 读取或解析失败；已在更新本地库或输出文件前停止。",
                    error=str(exc),
                    summaries_path=args.summaries,
                    input_path=args.input or None,
                    output_path=args.output or None,
                    config_path=args.config or None,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    if args.config and not args.input:
        try:
            config = load_config(args.config)
        except (FileNotFoundError, JSONDecodeError, ValueError) as exc:
            print(
                json.dumps(
                    summary_apply_failed_result(
                        stage="config_load",
                        message="摘要应用配置读取失败；已在更新本地库前停止。",
                        error=str(exc),
                        summaries_path=args.summaries,
                        config_path=args.config,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        conn = connect(config.get("database_path", "data/posts.sqlite"))
        posts = scoped_posts(conn, args)
        result = apply_to_posts(posts, summaries, config)
        for post in posts:
            key = str(post.get("canonical_post_url") or post.get("post_url") or "")
            fields = result["fields_by_key"].get(key)
            if not fields:
                continue
            update_post_fields_with_audit(conn, post, fields, config=config)
            mark_stage_done(conn, post, "summary")
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": "sqlite",
                    "applied": result["applied"],
                    "missing": len(result["missing"]),
                    "rejected": len(result["rejected"]),
                    "article_summary_missing": result["missing"],
                    "article_summary_rejected": result["rejected"],
                    "scope": apply_scope_payload(args, len(posts)),
                    "next_commands": next_commands_after_sqlite_apply(args) if result["applied"] else [],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if not args.input or not args.output:
        parser.error("--input and --output are required unless --config is used for SQLite mode")

    try:
        payload, posts = load_input_posts(args.input)
    except (FileNotFoundError, JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        print(
            json.dumps(
                summary_apply_failed_result(
                    stage="input_load",
                    message="摘要应用输入读取或解析失败；已在写出结果前停止。",
                    error=str(exc),
                    summaries_path=args.summaries,
                    input_path=args.input,
                    output_path=args.output,
                    config_path=args.config or None,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    file_config = None
    if args.config:
        try:
            file_config = load_config(args.config)
        except (FileNotFoundError, JSONDecodeError, ValueError) as exc:
            print(
                json.dumps(
                    summary_apply_failed_result(
                        stage="config_load",
                        message="摘要应用配置读取失败；已在写出结果前停止。",
                        error=str(exc),
                        summaries_path=args.summaries,
                        input_path=args.input,
                        output_path=args.output,
                        config_path=args.config,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
    result = apply_to_posts(posts, summaries, file_config)

    payload["article_summary_applied"] = result["applied"]
    payload["article_summary_missing"] = result["missing"]
    payload["article_summary_rejected"] = result["rejected"]
    payload["ready"] = sum(1 for item in posts if item.get("crawl_status") == "ready")
    payload["ready_for_output"] = sum(1 for item in posts if item.get("output_status") == "ready_for_output")
    payload["partial_review"] = sum(1 for item in posts if item.get("output_status") == "partial_review")
    payload["needs_enrichment"] = sum(1 for item in posts if item.get("crawl_status") == "needs_enrichment")
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "mode": "file",
                "applied": result["applied"],
                "missing": len(result["missing"]),
                "rejected": len(result["rejected"]),
                "output": args.output,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
