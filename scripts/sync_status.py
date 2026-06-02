#!/usr/bin/env python3
"""Completion status helpers for ledger sync and resumable account jobs."""

from __future__ import annotations

from typing import Any

from pipeline_status import missing_enrichment_stages
from store import enrichment_tasks_for_posts


OPEN_TASK_STATUSES = {"pending", "failed", "running"}
FINAL_OUTPUT_STATUSES = {"ready_for_output", "output_synced"}
NON_LEDGER_STATUSES = {"blocked"}


def _post_key(post: dict[str, Any]) -> str:
    return str(post.get("canonical_post_url") or post.get("post_url") or "").strip()


def _task_key(task: dict[str, Any]) -> str:
    return str(task.get("canonical_post_url") or task.get("post_url") or "").strip()


def _rate(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(part / total, 4)


def _has_account_context(post: dict[str, Any]) -> bool:
    return bool(str(post.get("account_name") or "").strip() or str(post.get("account_url") or "").strip())


def _is_ledger_candidate(post: dict[str, Any]) -> bool:
    return bool(
        str(post.get("post_url") or "").strip()
        and _has_account_context(post)
        and str(post.get("output_status") or "") not in NON_LEDGER_STATUSES
    )


def _coverage_incomplete(post: dict[str, Any]) -> bool:
    return bool(
        str(post.get("coverage_note") or "").strip()
        or str(post.get("coverage_status") or "").strip() in {"coverage_blocked", "coverage_incomplete"}
        or str(post.get("coverage_reason") or "").strip() in {"coverage_blocked", "coverage_incomplete"}
    )


def _stage_counts(missing_by_post: dict[str, list[str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for stages in missing_by_post.values():
        for stage in stages:
            counts[stage] = counts.get(stage, 0) + 1
    return dict(sorted(counts.items()))


def _open_task_stage_counts(open_tasks: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for task in open_tasks:
        stage = str(task.get("stage") or "")
        if not stage:
            continue
        counts[stage] = counts.get(stage, 0) + 1
    return dict(sorted(counts.items()))


def _next_actions(
    *,
    post_count: int,
    coverage_incomplete_count: int,
    missing_stage_counts: dict[str, int],
    requires_codex_summary_count: int,
    open_task_count: int,
) -> list[str]:
    actions: list[str] = []
    if post_count == 0:
        return ["先从账号主页顶部运行采集；没有候选时不要同步空结果。"]
    if coverage_incomplete_count:
        actions.append("覆盖未完成：从账号主页顶部重跑采集，必要时提高 --max-snapshots 后继续补抓。")
    detail_stages = {"detail_time", "lead_link", "engagement", "post_type", "article_material"}
    if open_task_count or detail_stages.intersection(missing_stage_counts):
        actions.append(
            "继续运行 enrichment_worker 的 detail_time,lead_link,engagement,post_type,article_material 阶段。"
        )
    if requires_codex_summary_count or missing_stage_counts.get("summary"):
        actions.append("导出 summary requests，写入 Codex 中文概要后再 apply_article_summaries。")
    if not actions:
        actions.append("当前范围没有未完成补抓项，可执行正式同步或保持现有台账结果。")
    return actions[:4]


def enrichment_completion_summary(conn: Any, posts: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a business-facing completion summary for a scoped set of posts."""

    tasks = enrichment_tasks_for_posts(conn, posts)
    task_counts: dict[str, int] = {}
    open_tasks: list[dict[str, Any]] = []
    for task in tasks:
        key = f"{task.get('stage')}:{task.get('status')}"
        task_counts[key] = task_counts.get(key, 0) + 1
        if task.get("status") in OPEN_TASK_STATUSES:
            open_tasks.append(task)

    missing_by_post: dict[str, list[str]] = {}
    coverage_incomplete_urls: list[str] = []
    for post in posts:
        key = _post_key(post)
        missing = missing_enrichment_stages(post)
        if _coverage_incomplete(post) and "coverage" not in missing:
            missing.append("coverage")
        if key and missing:
            missing_by_post[key] = missing
        if key and _coverage_incomplete(post):
            coverage_incomplete_urls.append(key)

    incomplete_keys = set(missing_by_post)
    incomplete_keys.update(_task_key(task) for task in open_tasks if _task_key(task))
    incomplete_keys.update(coverage_incomplete_urls)
    summary_blockers = [
        task
        for task in open_tasks
        if task.get("stage") == "summary"
        and "requires_codex_chinese_summary" in str(task.get("last_error") or "")
    ]
    post_count = len(posts)
    ready_or_synced_posts = [
        post for post in posts if post.get("output_status") in FINAL_OUTPUT_STATUSES
    ]
    final_usable_posts = [
        post
        for post in ready_or_synced_posts
        if _post_key(post) not in incomplete_keys and not _coverage_incomplete(post)
    ]
    ledger_candidate_count = sum(1 for post in posts if _is_ledger_candidate(post))
    missing_stage_counts = _stage_counts(missing_by_post)
    open_stage_counts = _open_task_stage_counts(open_tasks)
    coverage_incomplete_count = len(set(coverage_incomplete_urls))

    return {
        "post_count": post_count,
        "ledger_candidate_count": ledger_candidate_count,
        "ledger_usable_rate": _rate(ledger_candidate_count, post_count),
        "ready_or_synced_posts": len(ready_or_synced_posts),
        "final_usable_count": len(final_usable_posts),
        "final_usable_rate": _rate(len(final_usable_posts), post_count),
        "incomplete_post_count": len(incomplete_keys),
        "completion_rate": _rate(max(0, post_count - len(incomplete_keys)), post_count),
        "incomplete_post_urls": sorted(incomplete_keys)[:10],
        "open_task_count": len(open_tasks),
        "task_counts": task_counts,
        "open_task_stage_counts": open_stage_counts,
        "missing_stages_by_post": dict(list(missing_by_post.items())[:10]),
        "missing_stage_counts": missing_stage_counts,
        "requires_codex_summary_count": len(summary_blockers),
        "requires_codex_summary_urls": sorted({_task_key(task) for task in summary_blockers if _task_key(task)})[:10],
        "coverage_complete": coverage_incomplete_count == 0,
        "coverage_health": "complete" if coverage_incomplete_count == 0 else "incomplete",
        "coverage_incomplete_count": coverage_incomplete_count,
        "coverage_incomplete_urls": sorted(set(coverage_incomplete_urls))[:10],
        "has_incomplete_enrichment": bool(incomplete_keys or open_tasks),
        "next_actions": _next_actions(
            post_count=post_count,
            coverage_incomplete_count=coverage_incomplete_count,
            missing_stage_counts=missing_stage_counts,
            requires_codex_summary_count=len(summary_blockers),
            open_task_count=len(open_tasks),
        ),
    }


def annotate_sync_result(
    sync_result: dict[str, Any],
    completion: dict[str, Any],
    *,
    ledger_mode: bool,
) -> dict[str, Any]:
    """Attach completion semantics so ledger writes cannot look like final completion."""

    next_result = dict(sync_result)
    next_result["enrichment_completion"] = completion
    incomplete = bool(completion.get("has_incomplete_enrichment"))
    next_result["complete"] = bool(sync_result.get("ok")) and not incomplete
    if not sync_result.get("ok"):
        next_result.setdefault("run_status", sync_result.get("stage") or "sync_failed")
        return next_result
    if incomplete:
        if completion.get("requires_codex_summary_count"):
            next_result["run_status"] = "needs_codex_summary"
        elif ledger_mode:
            next_result["run_status"] = "synced_ledger_incomplete"
        else:
            next_result["run_status"] = "incomplete_pending_tasks"
        next_result["message"] = (
            "已写入可审计台账行，但本次采集作业未完成；仍有补抓任务或缺失字段，"
            "需要继续运行可恢复作业入口。"
        )
    else:
        next_result["run_status"] = "complete"
    return next_result
