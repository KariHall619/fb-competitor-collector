#!/usr/bin/env python3
"""Structured diagnostics for capture/enrichment/sync stages."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from field_audit import audit_post_fields
from models import has_qualified_comment_lead_link
from pipeline_status import has_article_summary, has_confirmed_time
from value_utils import parse_bool


DISCOVERY_REASONS = {
    "D_LOGIN",
    "D_VISITOR",
    "D_CAPTCHA",
    "D_EMPTY",
    "D_WRONG_PROFILE",
    "D_COVERAGE_UNPROVEN",
}
TIME_REASONS = {"T_NO_ANCHOR", "T_ONLY_RELATIVE", "T_COMMENT_TIME_LEAK", "T_PAGE_NOT_DETAIL"}
LEAD_REASONS = {
    "L_NO_COMMENTS_IN_DOM",
    "L_AUTHOR_BLOCK_UNMATCHED",
    "L_LINK_NOT_IN_DOM",
    "L_REDIRECT_UNWRAP_FAIL",
    "L_DOMAIN_FILTERED",
    "L_NO_LINK_ON_PAGE",
}
ARTICLE_REASONS = {"A_FETCH_FAIL", "A_NON_ARTICLE", "A_BLOCKED"}
SUMMARY_REASONS = {"S_NO_MATERIAL", "S_APPLY_FAIL", "S_NOT_CHINESE"}
SYNC_REASONS = {"G_GATE_BLOCKED", "F_AUTH", "F_API"}
QUEUE_REASONS = {"Q_POST_NOT_FOUND", "Q_POST_KEY_NOT_FOUND", "Q_NO_PROGRESS", "Q_MAX_ATTEMPTS"}
UNKNOWN_REASONS = {"U_LEAD_UNOBSERVED"}

KNOWN_REASON_CODES = (
    DISCOVERY_REASONS
    | TIME_REASONS
    | LEAD_REASONS
    | ARTICLE_REASONS
    | SUMMARY_REASONS
    | SYNC_REASONS
    | QUEUE_REASONS
    | UNKNOWN_REASONS
)


def normalize_reason_code(value: Any, *, default: str = "UNKNOWN") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    code = text.split(":", 1)[0].strip()
    return code if code in KNOWN_REASON_CODES else default


def reason_for_detail_stage(
    stage: str,
    post: dict[str, Any] | None,
    *,
    message: str = "",
    evidence: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> str:
    post = post or {}
    text = str(message or "")
    evidence = evidence or {}
    explicit = normalize_reason_code(evidence.get("reason_code") or evidence.get("stop_reason"), default="")
    if explicit:
        return explicit
    if "post not found" in text:
        return "Q_POST_NOT_FOUND"
    if "post key not found" in text:
        return "Q_POST_KEY_NOT_FOUND"
    if "requires_codex_chinese_summary" in text:
        return "S_NOT_CHINESE"
    if stage == "detail_time":
        if post.get("posted_at") and not parse_bool(post.get("time_confirmed")):
            return "T_ONLY_RELATIVE"
        if post.get("posted_at") and str(post.get("time_source") or "").startswith("relative"):
            return "T_ONLY_RELATIVE"
        return "T_NO_ANCHOR"
    if stage == "lead_link":
        return reason_for_lead_evidence(evidence, message=text)
    if stage == "article_material":
        if not (post.get("article_url") or post.get("landing_url")):
            return "S_NO_MATERIAL"
        if "blocked" in text.lower():
            return "A_BLOCKED"
        if "non_article" in text.lower():
            return "A_NON_ARTICLE"
        return "A_FETCH_FAIL"
    if stage == "summary":
        if not has_article_summary(post):
            return "S_NO_MATERIAL"
        return "S_APPLY_FAIL"
    if stage in {"engagement", "post_type"}:
        audit = audit_post_fields(post, config)
        if audit.get("field_audit_status") == "needs_refetch":
            return "G_GATE_BLOCKED"
    return "UNKNOWN"


def reason_for_lead_evidence(evidence: dict[str, Any] | None, *, message: str = "") -> str:
    evidence = evidence or {}
    text = str(message or "")
    if "parser_no_candidate" in text:
        return "L_LINK_NOT_IN_DOM"
    if "external_link_not_in_html" in text:
        return "L_NO_LINK_ON_PAGE"
    if "comment_controls_not_found" in text:
        return "L_NO_COMMENTS_IN_DOM"
    if "lead_link_not_resolved" in text:
        return "L_REDIRECT_UNWRAP_FAIL"
    has_observation = bool(evidence.get("observed"))
    if int(evidence.get("candidate_count") or 0) > 0:
        return "L_REDIRECT_UNWRAP_FAIL"
    if int(evidence.get("external_link_count_in_dom") or 0) > 0:
        return "L_LINK_NOT_IN_DOM"
    if has_observation and evidence.get("author_block_matched") is False and evidence.get("comments_region_found") is True:
        return "L_AUTHOR_BLOCK_UNMATCHED"
    if has_observation and int(evidence.get("expand_clicks_count") or 0) == 0 and evidence.get("comments_region_found") is False:
        return "L_NO_COMMENTS_IN_DOM"
    if has_observation:
        return "L_NO_LINK_ON_PAGE"
    return "U_LEAD_UNOBSERVED"


def detail_evidence_from_payload(payload: dict[str, Any] | None, stage: str) -> dict[str, Any]:
    payload = payload or {}
    if stage != "lead_link":
        return {}
    lead = payload.get("lead_link") if isinstance(payload.get("lead_link"), dict) else {}
    attempts = lead.get("attempts") if isinstance(lead.get("attempts"), list) else []
    non_cta = [item for item in attempts if item.get("mode") != "post_cta"]
    action_summaries = [item.get("action_summary") or {} for item in non_cta]
    candidate_count = max([int(item.get("candidate_count") or 0) for item in attempts] or [0])
    scrapling_count = max([int((item.get("scrapling") or {}).get("candidate_count") or 0) for item in non_cta] or [0])
    external_count = max([int(summary.get("external_count") or summary.get("external_anchor_count") or 0) for summary in action_summaries] or [0])
    text_count = max([int(summary.get("external_text_url_count") or 0) for summary in action_summaries] or [0])
    clicked_count = sum(int(summary.get("clicked_count") or 0) for summary in action_summaries)
    author_matched = any(bool((item.get("selected") or {}).get("owner_matched") or (item.get("selected") or {}).get("author_marked")) for item in attempts)
    return {
        "reason_code": normalize_reason_code(lead.get("reason_code"), default=""),
        "observed": bool(attempts),
        "stop_reason": lead.get("reason") or "",
        "expand_clicks_count": clicked_count,
        "comments_region_found": bool(action_summaries),
        "author_block_matched": author_matched if candidate_count or scrapling_count else None,
        "external_link_count_in_dom": external_count + text_count,
        "anchor_link_count": external_count,
        "plaintext_link_count": text_count,
        "candidate_count": max(candidate_count, scrapling_count),
    }


def evidence_bundle_from_detail_payload(
    payload: dict[str, Any] | None,
    *,
    base_dir: str | Path,
    key: str,
    reason_code: str,
) -> str:
    payload = payload or {}
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    lead = payload.get("lead_link") if isinstance(payload.get("lead_link"), dict) else {}
    attempts = lead.get("attempts") if isinstance(lead.get("attempts"), list) else []
    expand_actions = [
        {
            "mode": item.get("mode"),
            "action_summary": item.get("action_summary") or {},
            "action_trace": item.get("action_trace") or {},
        }
        for item in attempts
    ]
    link_stats: dict[str, Any] = {}
    parse_candidates: list[dict[str, Any]] = []
    for item in attempts:
        scrapling = item.get("scrapling") if isinstance(item.get("scrapling"), dict) else {}
        diagnostics = scrapling.get("lead_diagnostics") if isinstance(scrapling.get("lead_diagnostics"), dict) else {}
        if diagnostics:
            link_stats.update(diagnostics)
        candidates = scrapling.get("parse_candidates") if isinstance(scrapling.get("parse_candidates"), list) else []
        parse_candidates.extend(candidate for candidate in candidates if isinstance(candidate, dict))
    if not link_stats:
        link_stats = detail_evidence_from_payload(payload, "lead_link")
    if not parse_candidates and isinstance(lead.get("candidates"), list):
        parse_candidates = [item for item in lead.get("candidates") if isinstance(item, dict)]
    if not snapshot.get("html") and not snapshot.get("inner_text") and not link_stats and not parse_candidates:
        return ""
    return write_evidence_bundle(
        base_dir,
        key=key,
        html=str(snapshot.get("html") or ""),
        inner_text=str(snapshot.get("inner_text") or ""),
        expand_actions=expand_actions,
        link_stats=link_stats,
        parse_candidates=parse_candidates[:100],
        stop_reason=reason_code,
    )


def write_evidence_bundle(
    base_dir: str | Path,
    *,
    key: str,
    html: str = "",
    inner_text: str = "",
    screenshot_path: str = "",
    expand_actions: dict[str, Any] | list[Any] | None = None,
    link_stats: dict[str, Any] | None = None,
    parse_candidates: list[dict[str, Any]] | None = None,
    stop_reason: str = "",
) -> str:
    safe_key = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in key)[:120] or "evidence"
    bundle_dir = Path(base_dir) / safe_key
    bundle_dir.mkdir(parents=True, exist_ok=True)
    if html:
        (bundle_dir / "outerHTML.html").write_text(html, encoding="utf-8")
    if inner_text:
        (bundle_dir / "innerText.txt").write_text(inner_text, encoding="utf-8")
    if screenshot_path:
        (bundle_dir / "screenshot_path.txt").write_text(str(screenshot_path), encoding="utf-8")
    (bundle_dir / "expand_actions.json").write_text(json.dumps(expand_actions or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    (bundle_dir / "link_stats.json").write_text(json.dumps(link_stats or {}, ensure_ascii=False, indent=2), encoding="utf-8")
    (bundle_dir / "parse_candidates.json").write_text(json.dumps(parse_candidates or [], ensure_ascii=False, indent=2), encoding="utf-8")
    (bundle_dir / "stop_reason.txt").write_text(str(stop_reason or ""), encoding="utf-8")
    return str(bundle_dir)


def output_audit_summary(
    posts: list[dict[str, Any]],
    tasks: list[dict[str, Any]] | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tasks = tasks or []
    discovered = len(posts)
    time_confirmed = sum(1 for post in posts if has_confirmed_time(post))
    lead_qualified = sum(1 for post in posts if has_qualified_comment_lead_link(post))
    summary_ready = sum(1 for post in posts if has_article_summary(post))
    gate_passed = sum(1 for post in posts if post.get("output_status") == "ready_for_output")
    synced = sum(1 for post in posts if post.get("output_status") == "output_synced")
    blocked_by: Counter[str] = Counter()
    task_reason_by_key: dict[tuple[str, str], str] = {}
    for task in tasks:
        code = normalize_reason_code(task.get("reason_code"), default="")
        if not code:
            continue
        canonical = str(task.get("canonical_post_url") or task.get("post_url") or "")
        stage = str(task.get("stage") or "")
        if canonical and stage:
            task_reason_by_key[(canonical, stage)] = code
    for post in posts:
        if not has_confirmed_time(post):
            blocked_by[reason_for_detail_stage("detail_time", post, config=config)] += 1
        if not has_qualified_comment_lead_link(post):
            key = str(post.get("canonical_post_url") or post.get("post_url") or "")
            blocked_by[task_reason_by_key.get((key, "lead_link"), "U_LEAD_UNOBSERVED")] += 1
        if not has_article_summary(post):
            blocked_by["S_NO_MATERIAL"] += 1
        audit = audit_post_fields(post, config)
        for reason in audit.get("field_audit_reasons", []):
            blocked_by[f"field_audit_{reason}"] += 1
    top_missing_field = ""
    if blocked_by:
        top_missing_field = blocked_by.most_common(1)[0][0]
    return {
        "discovered": discovered,
        "time_confirmed": time_confirmed,
        "lead_qualified": lead_qualified,
        "summary_ready": summary_ready,
        "gate_passed": gate_passed,
        "synced": synced,
        "blocked_by": dict(sorted(blocked_by.items())),
        "top_missing_field": top_missing_field,
    }
