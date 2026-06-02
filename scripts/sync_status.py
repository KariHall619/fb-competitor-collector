#!/usr/bin/env python3
"""Completion status helpers for ledger sync and resumable account jobs."""

from __future__ import annotations

from typing import Any

from field_audit import REASON_LABELS, REASON_STAGES, audit_post_fields, parse_reasons
from pipeline_status import missing_enrichment_stages
from story_summary_policy import article_material_for_post, has_valid_story_summary
from store import enrichment_tasks_for_posts


OPEN_TASK_STATUSES = {"pending", "failed", "running"}
FINAL_OUTPUT_STATUSES = {"ready_for_output", "output_synced"}
NON_LEDGER_STATUSES = {"blocked"}
AUTO_ENRICHMENT_STAGES = {"detail_time", "lead_link", "engagement", "post_type", "article_material"}


def blocked_auth_result(message: str, error: str) -> dict[str, Any]:
    """Return a consistent pre-write auth blocker payload for CLI callers."""

    return {
        "ok": False,
        "stage": "feishu_auth_preflight",
        "run_status": "blocked_auth",
        "complete": False,
        "message": message,
        "error": error,
        "next_actions": [
            "完成飞书用户授权或等待自动刷新恢复后，重新运行同一命令；本次未执行 Facebook 采集、导入或飞书写入。"
        ],
        "completion_blockers": [
            {
                "code": "blocked_auth",
                "label": "飞书授权阻塞",
                "severity": "hard_blocker",
                "priority": 0,
                "recoverable": True,
                "message": message,
                "next_action": "完成飞书用户授权或等待自动刷新恢复后，重新运行同一命令。",
                "metrics": {"stage": "feishu_auth_preflight"},
            }
        ],
    }


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


def _requires_codex_summary(post: dict[str, Any]) -> bool:
    return bool(not has_valid_story_summary(post) and article_material_for_post(post))


def _field_gap_reasons(post: dict[str, Any]) -> list[str]:
    stored_reasons = parse_reasons(post.get("field_audit_reasons"))
    if stored_reasons:
        return stored_reasons
    return audit_post_fields(post).get("field_audit_reasons", [])


def _field_gap_counts(posts: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for post in posts:
        if post.get("output_status") in FINAL_OUTPUT_STATUSES:
            continue
        for reason in _field_gap_reasons(post):
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _top_field_gaps(field_gap_counts: dict[str, int]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for reason, count in field_gap_counts.items():
        gaps.append(
            {
                "reason": reason,
                "label": REASON_LABELS.get(reason, reason),
                "count": count,
                "stage": REASON_STAGES.get(reason, "coverage" if reason == "coverage" else ""),
            }
        )
    return gaps[:8]


def _field_gap_notes(field_gap_counts: dict[str, int]) -> list[str]:
    notes: list[str] = []
    for gap in _top_field_gaps(field_gap_counts)[:5]:
        notes.append(f"{gap['label']}：{gap['count']} 条")
    return notes


def has_auto_enrichment_work(completion: dict[str, Any]) -> bool:
    """Return True when a machine-runnable refetch/enrichment stage remains."""

    return bool(
        completion.get("auto_open_task_count")
        or completion.get("coverage_incomplete_count")
        or completion.get("has_auto_enrichment_work")
    )


def completion_run_status(completion: dict[str, Any], *, ledger_mode: bool = False) -> str:
    """Map a completion summary to the next operational state."""

    if has_auto_enrichment_work(completion):
        return "synced_ledger_incomplete" if ledger_mode else "incomplete_pending_tasks"
    if completion.get("has_summary_only_work") or completion.get("requires_codex_summary_count"):
        return "needs_codex_summary"
    if completion.get("has_incomplete_enrichment"):
        return "synced_ledger_incomplete" if ledger_mode else "incomplete_pending_tasks"
    return "complete"


def sync_completion_blockers(
    sync_result: dict[str, Any],
    completion: dict[str, Any],
    *,
    ledger_mode: bool,
) -> list[dict[str, Any]]:
    """Return ordered blockers for direct import/filter/sync callers."""

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

    if not sync_result.get("ok"):
        stage = str(sync_result.get("stage") or sync_result.get("run_status") or "sync_failed")
        add(
            stage,
            label={
                "quality_gate": "严格质量门未通过",
                "audit_output_gate": "无台账候选",
                "partial_gate": "无预览候选",
                "blocked_auth": "飞书授权阻塞",
                "feishu_auth_preflight": "飞书授权阻塞",
            }.get(stage, "飞书同步未完成"),
            severity="sync",
            priority=0,
            message=str(sync_result.get("message") or "同步或输出门未完成，本地 SQLite 结果仍可续跑。"),
            next_action=(sync_result.get("next_actions") or _failed_sync_next_actions(sync_result))[0],
            metrics={
                key: sync_result[key]
                for key in ("stage", "run_status", "ready_for_output", "output_candidates", "partial_review", "skipped")
                if key in sync_result
            },
        )

    if completion.get("coverage_health") == "incomplete" or completion.get("coverage_incomplete_count"):
        add(
            "coverage_incomplete",
            label="覆盖不足",
            severity="coverage",
            priority=10,
            message="本地记录标记了覆盖不足；台账可见不代表本次账号窗口已抓全。",
            next_action="从账号主页顶部重跑采集，必要时提高 --max-snapshots 并带上 expected count/labels。",
            metrics={
                "coverage_incomplete_count": completion.get("coverage_incomplete_count", 0),
                "coverage_incomplete_urls": completion.get("coverage_incomplete_urls", []),
            },
        )

    for stage, count in (completion.get("open_task_stage_counts") or {}).items():
        if stage not in AUTO_ENRICHMENT_STAGES or int(count or 0) <= 0:
            continue
        add(
            f"stage_{stage}",
            label=f"{stage} 待补抓",
            severity="auto_enrichment",
            priority=20,
            message=f"{stage} 仍有待跑补抓任务，阻止最终完整可用。",
            next_action="继续运行同账号补抓队列，优先处理 detail_time, lead_link, engagement, post_type, article_material。",
            metrics={"stage": stage, "open_task_count": int(count or 0)},
        )

    for stage, count in (completion.get("missing_stage_counts") or {}).items():
        if stage not in AUTO_ENRICHMENT_STAGES or int(count or 0) <= 0:
            continue
        code = f"stage_{stage}"
        if any(item["code"] == code for item in blockers):
            continue
        add(
            code,
            label=f"{stage} 缺失",
            severity="auto_enrichment",
            priority=21,
            message=f"{stage} 字段仍缺失，最终完整可用率未达标。",
            next_action="重新入队并运行同账号补抓队列。",
            metrics={"stage": stage, "missing_post_count": int(count or 0)},
        )

    if completion.get("requires_codex_summary_count") or (completion.get("missing_stage_counts") or {}).get("summary"):
        add(
            "codex_summary_required",
            label="需要 Codex 中文概要",
            severity="codex_required",
            priority=40,
            message="仍缺基于文章材料的中文概要。",
            next_action="导出 scoped summary requests，写入中文概要后运行 apply_article_summaries。",
            metrics={"requires_codex_summary_count": completion.get("requires_codex_summary_count", 0)},
        )

    top_field_gaps = completion.get("top_field_gaps") if isinstance(completion.get("top_field_gaps"), list) else []
    if top_field_gaps and completion.get("final_usable_rate", 0.0) < 1.0:
        add(
            "field_gaps",
            label="最终可用字段缺口",
            severity="quality_gap",
            priority=50,
            message="存在最终输出字段缺口，台账写入不等于最终可用。",
            next_action="按 top_field_gaps 补齐精确时间、引流链接、文章概要、互动数据或帖子类型。",
            metrics={"top_field_gaps": top_field_gaps[:5]},
        )

    if ledger_mode and completion.get("ledger_candidate_count") and completion.get("final_usable_rate", 0.0) < 1.0:
        add(
            "ledger_not_final",
            label="台账已写但未最终可用",
            severity="ledger_state",
            priority=60,
            message="普通同步允许台账行先入表，但最终完整可用率仍未达标。",
            next_action="继续补抓 completion_blockers 中列出的字段，后续按帖子链接 upsert 更新同一行。",
            metrics={
                "ledger_candidate_count": completion.get("ledger_candidate_count", 0),
                "ledger_usable_rate": completion.get("ledger_usable_rate", 0.0),
                "final_usable_rate": completion.get("final_usable_rate", 0.0),
            },
        )

    blockers.sort(key=lambda item: (item["priority"], item["code"]))
    return blockers[:10]


def _next_actions(
    *,
    post_count: int,
    coverage_incomplete_count: int,
    missing_stage_counts: dict[str, int],
    field_gap_counts: dict[str, int],
    requires_codex_summary_count: int,
    open_task_count: int,
) -> list[str]:
    actions: list[str] = []
    if post_count == 0:
        return ["先从账号主页顶部运行采集；没有候选时不要同步空结果。"]
    if coverage_incomplete_count:
        actions.append("覆盖未完成：从账号主页顶部重跑采集，必要时提高 --max-snapshots 后继续补抓。")
    if open_task_count or AUTO_ENRICHMENT_STAGES.intersection(missing_stage_counts):
        actions.append(
            "继续运行 enrichment_worker 的 detail_time,lead_link,engagement,post_type,article_material 阶段。"
        )
    if field_gap_counts:
        notes = "、".join(_field_gap_notes(field_gap_counts)[:3])
        actions.append(f"优先处理最终输出字段缺口：{notes}。")
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
    auto_open_tasks = [task for task in open_tasks if task.get("stage") in AUTO_ENRICHMENT_STAGES]
    summary_open_tasks = [task for task in open_tasks if task.get("stage") == "summary"]
    summary_task_blockers = [
        task
        for task in open_tasks
        if task.get("stage") == "summary"
        and "requires_codex_chinese_summary" in str(task.get("last_error") or "")
    ]
    summary_required_urls = {
        _post_key(post)
        for post in posts
        if _post_key(post) and _requires_codex_summary(post)
    }
    summary_required_urls.update(
        _task_key(task)
        for task in summary_task_blockers
        if _task_key(task)
    )
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
    field_gap_counts = _field_gap_counts(posts)

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
        "auto_open_task_count": len(auto_open_tasks),
        "summary_open_task_count": len(summary_open_tasks),
        "task_counts": task_counts,
        "open_task_stage_counts": open_stage_counts,
        "missing_stages_by_post": dict(list(missing_by_post.items())[:10]),
        "missing_stage_counts": missing_stage_counts,
        "field_gap_counts": field_gap_counts,
        "top_field_gaps": _top_field_gaps(field_gap_counts),
        "field_gap_notes": _field_gap_notes(field_gap_counts),
        "requires_codex_summary_count": len(summary_required_urls),
        "requires_codex_summary_urls": sorted(summary_required_urls)[:10],
        "coverage_complete": coverage_incomplete_count == 0,
        "coverage_health": "complete" if coverage_incomplete_count == 0 else "incomplete",
        "coverage_incomplete_count": coverage_incomplete_count,
        "coverage_incomplete_urls": sorted(set(coverage_incomplete_urls))[:10],
        "has_incomplete_enrichment": bool(incomplete_keys or open_tasks),
        "has_auto_enrichment_work": bool(auto_open_tasks or AUTO_ENRICHMENT_STAGES.intersection(missing_stage_counts)),
        "has_summary_only_work": bool(summary_required_urls)
        and not (auto_open_tasks or AUTO_ENRICHMENT_STAGES.intersection(missing_stage_counts)),
        "next_actions": _next_actions(
            post_count=post_count,
            coverage_incomplete_count=coverage_incomplete_count,
            missing_stage_counts=missing_stage_counts,
            field_gap_counts=field_gap_counts,
            requires_codex_summary_count=len(summary_required_urls),
            open_task_count=len(auto_open_tasks),
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
        next_result.setdefault("next_actions", completion.get("next_actions") or _failed_sync_next_actions(next_result))
        next_result["completion_blockers"] = sync_completion_blockers(next_result, completion, ledger_mode=ledger_mode)
        return next_result
    if incomplete:
        next_result["run_status"] = completion_run_status(completion, ledger_mode=ledger_mode)
        next_result["message"] = (
            "已写入可审计台账行，但本次采集作业未完成；仍有补抓任务或缺失字段，"
            "需要继续运行可恢复作业入口。"
        )
        next_result["next_actions"] = completion.get("next_actions", [])
    else:
        next_result["run_status"] = "complete"
        next_result.setdefault("next_actions", completion.get("next_actions", []))
    next_result["completion_blockers"] = sync_completion_blockers(next_result, completion, ledger_mode=ledger_mode)
    return next_result


def annotate_sync_failure(sync_result: dict[str, Any]) -> dict[str, Any]:
    """Attach run_status/next_actions to sync failures without a DB completion scope."""

    next_result = dict(sync_result)
    if next_result.get("ok"):
        next_result.setdefault("complete", True)
        next_result.setdefault("run_status", "complete")
        next_result.setdefault("next_actions", [])
        return next_result
    next_result["complete"] = False
    stage = str(next_result.get("stage") or "")
    next_result["run_status"] = stage if stage in {"quality_gate", "audit_output_gate", "partial_gate"} else "sync_failed"
    next_result.setdefault("next_actions", _failed_sync_next_actions(next_result))
    next_result["completion_blockers"] = sync_completion_blockers(next_result, {}, ledger_mode=False)
    return next_result


def _failed_sync_next_actions(sync_result: dict[str, Any]) -> list[str]:
    stage = str(sync_result.get("stage") or sync_result.get("run_status") or "")
    if stage == "quality_gate":
        return ["严格完整行同步未执行：继续补齐精确时间、评论/回复引流链接、外部落地页和文章来源中文概要后重试。"]
    if stage == "audit_output_gate":
        return ["当前没有可写入正式台账的候选；先确认已从主页顶部采集并导入了有效 Facebook 帖子链接和账号信息。"]
    if stage == "partial_gate":
        return ["当前没有 partial_review 预览候选；先确认候选已导入本地库，或改用普通 --sync 台账模式。"]
    if stage in {"auth_status", "feishu_auth_preflight"}:
        return ["完成飞书用户授权或等待自动刷新恢复后，重新运行同一同步命令。"]
    if stage == "upsert_headers":
        return ["检查飞书输出表头配置；upsert 写入必须提供帖子链接等输出表头。"]
    return ["飞书同步失败：保留本地 SQLite 结果，修复同步错误后重新运行同一命令。"]
