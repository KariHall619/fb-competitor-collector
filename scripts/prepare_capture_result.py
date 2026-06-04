#!/usr/bin/env python3
"""Prepare raw OpenCLI Browser Bridge capture output before import or Feishu sync."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from coverage_status import coverage_note_from_payload
from field_schema import normalize_header
from models import (
    clean_article_url,
    clean_post_url,
    canonicalize_post_url,
    comment_lead_landing_url,
    estimate_posted_at_from_relative,
    facebook_link_kind,
    has_qualified_comment_lead_link,
    is_external_landing_url,
    is_estimated_time_source,
    normalize_posted_at,
    parse_count,
    QUALIFIED_LEAD_SOURCES,
)
from pipeline_status import crawl_status_for, output_status_for


MEDIA_LINK_RE = re.compile(
    r"(?:facebook\.com/(?:photo(?:\.php|/)|photos/|reel/|watch/|video/|[^/]+/videos/|videos/|share/)|fb\.watch/)",
    re.I,
)
ARTICLE_SUMMARY_KEYS = ("article_summary", "文章摘要", "内容摘要", "故事概要", "摘要", "简述")
POST_TYPE_KEYS = ("post_type", "帖子类型", "内容类型")
VIEW_KEYS = ("views", "播放量", "浏览量")
LIKE_KEYS = ("likes", "reactions", "点赞量", "点赞数", "反应数")
COMMENT_KEYS = ("comments", "评论数", "评论量")
SHARE_KEYS = ("shares", "分享数", "分享量")
ENGAGEMENT_KEYS = ("engagement_data", "engagement_raw", "互动数据", "互动数据（点赞量）", "互动数据(点赞量)", "互动数据汇总")


def first_raw_value(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    normalized_keys = {normalize_header(key) for key in keys}
    for raw_key, value in raw.items():
        if value in (None, ""):
            continue
        if normalize_header(raw_key) in normalized_keys:
            return value
    return ""


def prepare_failed_result(
    *,
    stage: str,
    message: str,
    error: str,
    input_path: str | Path | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "stage": stage,
        "run_status": "prepare_failed",
        "complete": False,
        "message": message,
        "error": error,
        "next_actions": [
            "修复 OpenCLI 原始抓取结果结构后，从账号主页顶部重新运行 run_account_job.py；本次未生成可入库候选。"
        ],
    }
    if input_path is not None:
        payload["input_path"] = str(input_path)
    if output_path is not None:
        payload["output_path"] = str(output_path)
    return payload


def load_raw_posts(path: str | Path) -> tuple[dict[str, Any] | list[dict[str, Any]], list[dict[str, Any]]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Input file not found: {p}")
    payload = json.loads(p.read_text(encoding="utf-8"))
    raw_posts = payload.get("posts") if isinstance(payload, dict) else payload
    if not isinstance(raw_posts, list):
        raise ValueError("Raw capture input must be a list, or an object with a posts list.")
    if not all(isinstance(item, dict) for item in raw_posts):
        raise ValueError("Every raw capture post must be a JSON object.")
    return payload, raw_posts


def clean_story_placeholder(raw: dict[str, Any]) -> str:
    article_summary = first_raw_value(raw, ARTICLE_SUMMARY_KEYS)
    if article_summary:
        return str(article_summary).strip()
    if raw.get("summary_source") == "article" and raw.get("story_summary"):
        return str(raw.get("story_summary") or "").strip()
    return ""


def summary_source_for_story(raw: dict[str, Any], story_summary: str) -> str:
    if raw.get("summary_source"):
        return str(raw.get("summary_source") or "")
    if first_raw_value(raw, ARTICLE_SUMMARY_KEYS):
        return "article"
    return "pending_article_summary"


def parse_engagement(raw: dict[str, Any]) -> tuple[int | None, int | None, str]:
    engagement = str(first_raw_value(raw, ENGAGEMENT_KEYS) or "")
    views = parse_count(first_raw_value(raw, VIEW_KEYS))
    likes = parse_count(first_raw_value(raw, LIKE_KEYS))
    if views is None:
        match = re.search(r"([\d.,]+)\s*([kKmM万]?)\s*(views|plays|次播放|播放)", engagement, re.I)
        if match:
            views = parse_count("".join(match.group(1, 2)))
    if likes is None:
        match = re.search(r"([\d.,]+)\s*([kKmM万]?)\s*(likes|reactions|赞)", engagement, re.I)
        if match:
            likes = parse_count("".join(match.group(1, 2)))
    return views, likes, engagement


def is_media_link(raw: dict[str, Any]) -> bool:
    post_url = str(raw.get("post_url") or "")
    return bool(MEDIA_LINK_RE.search(post_url))


def media_suspect_payload(raw: dict[str, Any]) -> dict[str, Any]:
    relative_time = str(raw.get("relative_time_text") or raw.get("post_time_text") or "").strip()
    return {
        "reason": "media_link_requires_parent_post",
        "post_url": raw.get("post_url"),
        "post_time_text": raw.get("post_time_text"),
        "relative_time_text": relative_time,
        "posted_at": normalize_posted_at(raw.get("posted_at") or raw.get("posted_at_raw") or ""),
        "posted_date": raw.get("posted_date") or "",
        "article_url": clean_article_url(raw.get("article_url")),
        "raw_text": str(raw.get("raw_text") or raw.get("story_summary") or "")[:500],
        "message": "发现图片/视频/媒体页链接，正式结果已排除；这通常表示对应父帖子没有被抓到，需要回到FB页面补抓 /posts/ 帖子链接。",
    }


def media_is_covered_by_post(media: dict[str, Any], post: dict[str, Any]) -> bool:
    """Return True when a rejected media link is already represented by a real post.

    Facebook pages often expose both the parent post link and inner photo/video
    links for the same story. The media link should never be written as output,
    but it should only trigger manual intervention when no matching parent post
    was captured.
    """

    media_article = media.get("article_url")
    post_article = post.get("article_url")
    if not media_article or media_article != post_article:
        return False

    media_time = media.get("posted_at")
    media_relative = media.get("relative_time_text") or media.get("post_time_text")
    media_date = media.get("posted_date")
    return bool(
        (media_time and media_time == post.get("posted_at"))
        or (media_relative and media_relative == post.get("relative_time_text"))
        or (media_date and media_date == post.get("posted_date"))
    )


def split_media_suspects(
    media_candidates: list[dict[str, Any]], prepared: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    unresolved: list[dict[str, Any]] = []
    covered: list[dict[str, Any]] = []
    for media in media_candidates:
        covering_post = next((post for post in prepared if media_is_covered_by_post(media, post)), None)
        if covering_post:
            covered.append(
                {
                    **media,
                    "status": "covered_by_post",
                    "covered_by_post_url": covering_post.get("post_url"),
                    "message": "发现媒体页链接，但同一文章和时间附近已抓到真实帖子链接；正式结果只保留帖子链接。",
                }
            )
        else:
            unresolved.append({**media, "status": "unresolved_parent_post_missing"})
    return unresolved, covered


def is_profile_or_noise(raw: dict[str, Any]) -> bool:
    story = str(raw.get("story_summary") or raw.get("raw_text") or "")
    if story.count("Facebook") >= 8:
        return True
    return False


def prepare_record(raw: dict[str, Any], defaults: dict[str, str], target_date: str) -> tuple[dict[str, Any] | None, str]:
    if is_profile_or_noise(raw):
        return None, "profile_or_noise"
    raw_fb_url = clean_post_url(raw.get("raw_fb_url") or raw.get("post_url"))
    parent_post_url = clean_post_url(raw.get("parent_post_url") or "")
    post_url = parent_post_url or raw_fb_url
    canonical = canonicalize_post_url(parent_post_url or raw_fb_url)
    if not post_url or not canonical:
        return None, "missing_post_url"

    lead_url_raw = clean_article_url(raw.get("lead_url_raw") or raw.get("comment_article_url") or "")
    lead_link_source = raw.get("lead_link_source") or ""
    lead_link_status = raw.get("lead_link_status") or ""
    comment_landing_url = comment_lead_landing_url(lead_url_raw, lead_link_source)
    landing_url = comment_landing_url or clean_article_url(raw.get("landing_url") or raw.get("article_url"))
    article_url = landing_url
    if comment_landing_url:
        lead_link_status = "qualified"
    elif lead_link_status != "qualified" and lead_url_raw and lead_link_source in QUALIFIED_LEAD_SOURCES and is_external_landing_url(landing_url):
        lead_link_status = "qualified"
    relative_time = str(raw.get("relative_time_text") or raw.get("post_time_text") or "").strip()
    posted_at = normalize_posted_at(raw.get("posted_at") or raw.get("posted_at_raw") or "")
    time_source = raw.get("time_source") or ("exact" if posted_at else "")
    crawled_at = raw.get("crawled_at") or datetime.now().isoformat(timespec="seconds")
    if not posted_at and relative_time:
        estimated_at = estimate_posted_at_from_relative(relative_time, crawled_at)
        if estimated_at:
            posted_at = estimated_at
            time_source = "relative_estimated"
    time_confirmed = bool(posted_at and not is_estimated_time_source(time_source))
    candidate_date = raw.get("posted_date") or ""
    if not candidate_date and posted_at:
        candidate_date = datetime.strptime(posted_at, "%Y年%m月%d日 %H:%M").strftime("%y%m%d")
    if target_date and candidate_date and candidate_date != target_date:
        return None, f"outside_target_date:{candidate_date or 'unknown'}"

    views, likes, engagement = parse_engagement(raw)
    note_parts = []
    if not posted_at:
        note_parts.append("发帖时间待确认，需通过FB时间悬停提示获取精确时间")
    elif is_estimated_time_source(time_source):
        note_parts.append(f"发帖时间为相对时间估算（{relative_time}），非Facebook精确时间")
    if target_date and not candidate_date:
        note_parts.append("目标日期待确认")
    if not article_url:
        note_parts.append("评论/回复或主帖CTA引流落地链接待确认")
    story_summary = clean_story_placeholder(raw)
    summary_source = summary_source_for_story(raw, story_summary)
    post_type = str(first_raw_value(raw, POST_TYPE_KEYS)).strip()
    if not story_summary or summary_source != "article":
        note_parts.append("文章概要待生成")
    if not post_type:
        note_parts.append("帖子类型待确认")
    if lead_link_status != "qualified":
        note_parts.append("评论区、评论回复或主帖CTA引流链接待确认")
    if views is None and likes is None and not engagement:
        note_parts.append("互动数据未确认")
    if parse_count(first_raw_value(raw, SHARE_KEYS)) is None:
        note_parts.append("分享数未确认")

    record = {
        **defaults,
        "post_url": post_url,
        "canonical_post_url": canonical,
        "raw_fb_url": raw_fb_url,
        "parent_post_url": parent_post_url,
        "fb_link_kind": raw.get("fb_link_kind") or facebook_link_kind(raw_fb_url),
        "article_url": article_url,
        "lead_url_raw": lead_url_raw,
        "landing_url": landing_url,
        "lead_link_status": lead_link_status,
        "lead_link_source": lead_link_source,
        "post_type": post_type,
        "story_summary": story_summary,
        "summary_source": summary_source,
        "posted_date": candidate_date,
        "posted_at": posted_at,
        "relative_time_text": relative_time,
        "time_confirmed": time_confirmed,
        "time_source": time_source,
        "views": views,
        "likes": likes,
        "comments": parse_count(first_raw_value(raw, COMMENT_KEYS)),
        "shares": parse_count(first_raw_value(raw, SHARE_KEYS)),
        "engagement_data": engagement,
        "crawl_status": "captured",
        "coverage_note": raw.get("coverage_note") or defaults.get("coverage_note", ""),
        "crawled_at": crawled_at,
        "note": "；".join(note_parts),
        "raw_payload": raw,
    }
    record["output_status"] = output_status_for(record)
    record["crawl_status"] = crawl_status_for(record)
    return record, ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--target-date", required=True, help="YYMMDD")
    parser.add_argument("--account-name", default="The meaning of life")
    parser.add_argument("--account-url", default="https://www.facebook.com/themeaningoflife88")
    parser.add_argument("--account-type", default="competitor")
    args = parser.parse_args()

    try:
        payload, raw_posts = load_raw_posts(args.input)
    except (FileNotFoundError, JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        print(
            json.dumps(
                prepare_failed_result(
                    stage="input_load",
                    message="OpenCLI 原始抓取结果读取或解析失败；已在标准化和入库前停止。",
                    error=str(exc),
                    input_path=args.input,
                    output_path=args.output,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    coverage_note = coverage_note_from_payload(payload if isinstance(payload, dict) else {})
    defaults = {
        "account_name": args.account_name,
        "account_url": args.account_url,
        "account_type": args.account_type,
        "source_skill": "fb-competitor-collector",
        "coverage_note": coverage_note,
    }
    prepared: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    media_candidates: list[dict[str, Any]] = []
    coverage_warnings: list[dict[str, Any]] = []
    for raw in raw_posts:
        try:
            record, reason = prepare_record(raw, defaults, args.target_date)
        except Exception as exc:
            rejected.append(
                {
                    "reason": "prepare_record_error",
                    "post_url": raw.get("post_url"),
                    "post_time_text": raw.get("post_time_text"),
                    "error": str(exc),
                    "message": "单条候选标准化失败；已跳过该候选并继续处理其它已发现帖子。",
                }
            )
            continue
        if record:
            prepared.append(record)
            if is_media_link(raw):
                media_candidates.append({**media_suspect_payload(raw), "status": "captured_as_candidate"})
            if record.get("shares") is None:
                coverage_warnings.append(
                    {
                        "warning": "missing_share_count",
                        "post_url": record.get("post_url"),
                        "relative_time_text": record.get("relative_time_text"),
                        "message": "分享数缺失，请复核是否抓到了帖子本体而不是评论/媒体片段。",
                    }
                )
        else:
            rejected_item = {"reason": reason, "post_url": raw.get("post_url"), "post_time_text": raw.get("post_time_text")}
            rejected.append(rejected_item)
            if reason == "media_link_requires_parent_post":
                media_candidates.append(media_suspect_payload(raw))

    unresolved_media = [item for item in media_candidates if item.get("status") != "captured_as_candidate"]
    media_suspects, covered_media_suspects = split_media_suspects(unresolved_media, prepared)

    output = {
        "ok": True,
        "target_date": args.target_date,
        "input": len(raw_posts),
        "prepared": len(prepared),
        "ready": sum(1 for item in prepared if item.get("crawl_status") == "ready"),
        "ready_for_output": sum(1 for item in prepared if item.get("output_status") == "ready_for_output"),
        "partial_review": sum(1 for item in prepared if item.get("output_status") == "partial_review"),
        "needs_enrichment": sum(1 for item in prepared if item.get("crawl_status") == "needs_enrichment"),
        "media_candidate_count": len(media_candidates),
        "media_suspect_count": len(media_suspects),
        "covered_media_suspect_count": len(covered_media_suspects),
        "coverage_warning_count": len(coverage_warnings),
        "coverage_note": coverage_note,
        "coverage": payload.get("coverage", {}) if isinstance(payload, dict) else {},
        "media_suspects": media_suspects,
        "covered_media_suspects": covered_media_suspects,
        "coverage_warnings": coverage_warnings,
        "rejected": rejected,
        "posts": prepared,
    }
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: output[k] for k in ("ok", "target_date", "input", "prepared", "ready", "needs_enrichment")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
