#!/usr/bin/env python3
"""Run queued enrichment stages with local concurrency limits."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import subprocess
import tempfile
import time
from pathlib import Path
import sqlite3
from typing import Any

from config_loader import deep_get, load_config
from fetch_article_material import extract_material
from models import has_qualified_comment_lead_link
from pipeline_status import crawl_status_for, output_status_for
from story_summary_policy import has_valid_story_summary, story_summary_errors
from store import (
    cached_article_material,
    connect,
    enqueue_enrichment_tasks,
    mark_task_done,
    mark_task_failed,
    mark_task_running,
    pending_enrichment_tasks,
    pending_enrichment_tasks_for_posts,
    post_for_task,
    query_posts,
    row_for_post,
    task_counts,
    task_counts_for_posts,
    update_post_fields,
    upsert_article_material,
    upsert_post,
)


ROOT = Path(__file__).resolve().parents[1]
DETAIL_STAGES = {"detail_time", "lead_link", "engagement", "post_type"}


def split_stages(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def detail_args_for_stages(stages: set[str]) -> list[str]:
    args: list[str] = []
    if "detail_time" not in stages:
        args.append("--skip-time")
    if "lead_link" not in stages:
        args.append("--skip-lead-link")
    if "engagement" not in stages:
        args.append("--skip-engagement")
    if "post_type" not in stages:
        args.append("--skip-post-type")
    return args


def run_detail_batch(
    config_path: str,
    config: dict[str, Any],
    posts: list[dict[str, Any]],
    stages: set[str],
    target_date: str,
) -> dict[str, Any]:
    if not posts:
        return {"ok": True, "posts": []}
    timeout = int(deep_get(config, "performance.detail_timeout_seconds", 45)) * max(1, len(posts))
    with tempfile.TemporaryDirectory(prefix="fb-detail-enrich-") as temp_dir:
        input_path = Path(temp_dir) / "input.json"
        output_path = Path(temp_dir) / "output.json"
        input_path.write_text(json.dumps({"posts": posts}, ensure_ascii=False, indent=2), encoding="utf-8")
        command = [
            "node",
            str(ROOT / "scripts" / "opencli_enrich_post_details.mjs"),
            "--config",
            config_path,
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            *detail_args_for_stages(stages),
        ]
        if target_date:
            command.extend(["--target-date", target_date])
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        if result.returncode != 0:
            payload: dict[str, Any] = {}
            if output_path.exists():
                try:
                    payload = json.loads(output_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    payload = {}
            if not payload and result.stdout:
                try:
                    payload = json.loads(result.stdout)
                except json.JSONDecodeError:
                    payload = {}
            if payload.get("human_intervention_required") or payload.get("action_required") == "human_intervention_required":
                return {
                    "ok": False,
                    "human_intervention_required": True,
                    "status": "human_intervention_required",
                    "reason": payload.get("blocked_reason") or payload.get("reason") or payload.get("status") or "facebook_login_blocked",
                    "payload": payload,
                    "error": result.stderr or result.stdout or f"exit={result.returncode}",
                }
            return {"ok": False, "error": result.stderr or result.stdout or f"exit={result.returncode}"}
        if not output_path.exists():
            return {"ok": False, "error": "detail enrichment did not write output"}
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        return {"ok": True, "posts": payload.get("posts", []), "payload": payload, "stdout": result.stdout}


def apply_detail_results(conn: sqlite3.Connection, original_posts: list[dict[str, Any]], enriched_posts: list[dict[str, Any]]) -> None:
    by_key = {
        (post.get("canonical_post_url") or post.get("post_url")): post
        for post in enriched_posts
        if post.get("post_url")
    }
    for post in original_posts:
        key = post.get("canonical_post_url") or post.get("post_url")
        enriched = by_key.get(key)
        if not enriched:
            continue
        enriched["output_status"] = output_status_for(enriched)
        enriched["crawl_status"] = crawl_status_for(enriched)
        upsert_post(conn, enriched)
        stored = row_for_post(conn, enriched) or enriched
        enqueue_enrichment_tasks(conn, stored)


def article_material_fields(post: dict[str, Any], material: dict[str, Any]) -> dict[str, Any]:
    raw_payload = post.get("raw_payload") or "{}"
    try:
        payload = json.loads(raw_payload)
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    payload["article_material"] = material
    next_post = {**post, "raw_payload": json.dumps(payload, ensure_ascii=False)}
    next_post["output_status"] = output_status_for(next_post)
    next_post["crawl_status"] = crawl_status_for(next_post)
    return {
        "raw_payload": next_post["raw_payload"],
        "output_status": next_post["output_status"],
        "crawl_status": next_post["crawl_status"],
    }


def run_article_task(config: dict[str, Any], post: dict[str, Any], conn_path: str) -> tuple[str, dict[str, Any]]:
    url = post.get("article_url") or post.get("landing_url") or ""
    if not url:
        raise RuntimeError("missing article_url")
    conn = connect(conn_path)
    cached = cached_article_material(conn, url)
    if cached is not None:
        return "cache", cached
    material = extract_material(url, timeout=int(deep_get(config, "performance.article_timeout_seconds", 12)))
    upsert_article_material(conn, url, material)
    return "fetch", material


def run_summary_task(post: dict[str, Any]) -> dict[str, Any]:
    if has_valid_story_summary(post):
        next_post = {**post}
    else:
        errors = story_summary_errors(post)
        if not errors and post.get("summary_source") != "article":
            errors = ["missing_article_summary"]
        raise RuntimeError("requires_codex_chinese_summary:" + ",".join(errors or ["missing_article_summary"]))
    next_post["output_status"] = output_status_for(next_post)
    next_post["crawl_status"] = crawl_status_for(next_post)
    return next_post


def detail_stage_satisfied(post: dict[str, Any], stage: str) -> bool:
    if stage == "detail_time":
        return bool(post.get("posted_at") and post.get("time_confirmed"))
    if stage == "lead_link":
        return has_qualified_comment_lead_link(post)
    if stage == "engagement":
        return all(post.get(field) is not None for field in ("likes", "comments", "shares"))
    if stage == "post_type":
        return post.get("post_type") in {"图文", "视频", "仅图片", "仅文字"}
    return True


def post_task_key(post: dict[str, Any], task: dict[str, Any] | None = None) -> str:
    return str(
        post.get("canonical_post_url")
        or (task or {}).get("canonical_post_url")
        or post.get("post_url")
        or (task or {}).get("post_url")
        or ""
    )


def detail_units_for_tasks(
    conn: sqlite3.Connection, detail_tasks: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    units_by_key: dict[str, dict[str, Any]] = {}
    missing_posts = 0
    for task in detail_tasks:
        post = post_for_task(conn, task)
        if not post:
            mark_task_failed(conn, task["id"], "post not found")
            missing_posts += 1
            continue
        key = post_task_key(post, task)
        if not key:
            mark_task_failed(conn, task["id"], "post key not found")
            missing_posts += 1
            continue
        unit = units_by_key.setdefault(key, {"key": key, "post": post, "tasks": [], "stages": set()})
        unit["tasks"].append(task)
        unit["stages"].add(task["stage"])
    return list(units_by_key.values()), missing_posts


def batches_for_detail_units(units: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for unit in units:
        stage_key = tuple(sorted(unit["stages"]))
        grouped.setdefault(stage_key, []).append(unit)
    batches: list[list[dict[str, Any]]] = []
    for stage_key in sorted(grouped):
        group = grouped[stage_key]
        for index in range(0, len(group), batch_size):
            batches.append(group[index : index + batch_size])
    return batches


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--stages", default="detail_time,lead_link,engagement,post_type,article_material")
    parser.add_argument("--target-date", default="")
    parser.add_argument("--date", default="")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--account-name", default="")
    parser.add_argument("--account-url", default="")
    parser.add_argument("--account-type", default="")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--detail-concurrency", type=int, default=0)
    parser.add_argument("--article-concurrency", type=int, default=0)
    args = parser.parse_args()

    config = load_config(args.config)
    db_path = config.get("database_path", "data/posts.sqlite")
    conn = connect(db_path)
    stages = split_stages(args.stages)
    scope_enabled = any(
        [
            args.date,
            args.start_date,
            args.end_date,
            args.account_name,
            args.account_url,
            args.account_type,
        ]
    )
    scoped_posts = (
        query_posts(
            conn,
            date=args.date,
            start_date=args.start_date,
            end_date=args.end_date,
            include_unknown_date=bool(args.date or args.start_date or args.end_date),
            account_name=args.account_name,
            account_url=args.account_url,
            account_type=args.account_type,
        )
        if scope_enabled
        else []
    )
    tasks = (
        pending_enrichment_tasks_for_posts(conn, scoped_posts, stages=stages, limit=args.limit)
        if scope_enabled
        else pending_enrichment_tasks(conn, stages=stages, limit=args.limit)
    )
    started = time.monotonic()
    completed = 0
    failed = 0
    human_intervention_required = False
    human_intervention_reasons: list[str] = []

    detail_tasks = [task for task in tasks if task["stage"] in DETAIL_STAGES]
    if detail_tasks:
        detail_concurrency = args.detail_concurrency or int(deep_get(config, "performance.detail_concurrency", 2))
        for task in detail_tasks:
            mark_task_running(conn, task["id"])
        detail_units, missing_posts = detail_units_for_tasks(conn, detail_tasks)
        failed += missing_posts
        batches = batches_for_detail_units(detail_units, max(1, detail_concurrency))
        for batch_units in batches:
            batch = [unit["post"] for unit in batch_units]
            stage_set = set().union(*(unit["stages"] for unit in batch_units))
            batch_start = time.monotonic()
            batch_succeeded = False
            try:
                result = run_detail_batch(args.config, config, batch, stage_set, args.target_date)
                if not result.get("ok"):
                    if result.get("human_intervention_required"):
                        human_intervention_required = True
                        reason = str(result.get("reason") or "facebook_login_blocked")
                        if reason not in human_intervention_reasons:
                            human_intervention_reasons.append(reason)
                    raise RuntimeError(result.get("error") or "detail enrichment failed")
                apply_detail_results(conn, batch, result.get("posts", []))
                batch_succeeded = True
            except Exception as exc:
                batch_error = str(exc)
            else:
                batch_error = ""
            duration_ms = int((time.monotonic() - batch_start) * 1000)
            for unit in batch_units:
                post = unit["post"]
                stored = row_for_post(conn, post) or post
                for task in unit["tasks"]:
                    if batch_succeeded and detail_stage_satisfied(stored, task["stage"]):
                        mark_task_done(conn, task["id"], duration_ms=duration_ms)
                        completed += 1
                    else:
                        mark_task_failed(conn, task["id"], batch_error or f"{task['stage']} still missing", duration_ms=duration_ms)
                        failed += 1

    article_tasks = [task for task in tasks if task["stage"] == "article_material"]
    if article_tasks:
        article_concurrency = args.article_concurrency or int(deep_get(config, "performance.article_concurrency", 5))
        for task in article_tasks:
            mark_task_running(conn, task["id"])
        task_posts = [(task, post_for_task(conn, task)) for task in article_tasks]
        task_posts = [(task, post) for task, post in task_posts if post]
        with ThreadPoolExecutor(max_workers=max(1, article_concurrency)) as executor:
            futures = {}
            for task, post in task_posts:
                futures[executor.submit(run_article_task, config, post, db_path)] = (task, post, time.monotonic())
            for future in as_completed(futures):
                task, post, task_start = futures[future]
                duration_ms = int((time.monotonic() - task_start) * 1000)
                try:
                    _source, material = future.result()
                    fields = article_material_fields(post, material)
                    update_post_fields(conn, post, fields)
                    stored = row_for_post(conn, post) or post
                    enqueue_enrichment_tasks(conn, stored)
                    mark_task_done(conn, task["id"], duration_ms=duration_ms)
                    completed += 1
                except Exception as exc:
                    mark_task_failed(conn, task["id"], str(exc), duration_ms=duration_ms)
                    failed += 1

    summary_tasks = [task for task in tasks if task["stage"] == "summary"]
    for task in summary_tasks:
        post = post_for_task(conn, task)
        if not post:
            mark_task_failed(conn, task["id"], "post not found")
            failed += 1
            continue
        task_start = time.monotonic()
        mark_task_running(conn, task["id"])
        try:
            next_post = run_summary_task(post)
            upsert_post(conn, next_post)
            mark_task_done(conn, task["id"], duration_ms=int((time.monotonic() - task_start) * 1000))
            completed += 1
        except Exception as exc:
            mark_task_failed(conn, task["id"], str(exc), duration_ms=int((time.monotonic() - task_start) * 1000))
            failed += 1

    result = {
        "ok": failed == 0,
        "run_status": "human_intervention_required" if human_intervention_required else ("complete" if failed == 0 else "failed"),
        "human_intervention_required": human_intervention_required,
        "human_intervention_reasons": human_intervention_reasons,
        "input_tasks": len(tasks),
        "completed": completed,
        "failed": failed,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "scope": {
            "enabled": scope_enabled,
            "date": args.date,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "account_name": args.account_name,
            "account_url": args.account_url,
            "account_type": args.account_type,
            "post_count": len(scoped_posts) if scope_enabled else None,
        },
        "task_counts": task_counts_for_posts(conn, scoped_posts) if scope_enabled else task_counts(conn),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
