#!/usr/bin/env python3
"""Prepare raw OpenCLI Browser Bridge capture output before import or Feishu sync."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from models import (
    clean_article_url,
    clean_post_url,
    canonicalize_post_url,
    facebook_link_kind,
    is_external_landing_url,
    normalize_posted_at,
    parse_count,
)


MEDIA_LINK_RE = re.compile(r"facebook\.com/(?:photo(?:\.php|/)|reel/|watch/|[^/]+/videos/|videos/)", re.I)


def clean_story_placeholder(raw: dict[str, Any]) -> str:
    article_summary = raw.get("article_summary") or ""
    if article_summary:
        return str(article_summary).strip()
    return ""


def parse_engagement(raw: dict[str, Any]) -> tuple[int | None, int | None, str]:
    engagement = str(raw.get("engagement_data") or raw.get("engagement_raw") or "")
    views = parse_count(raw.get("views") or raw.get("播放量") or raw.get("浏览量"))
    likes = parse_count(raw.get("likes") or raw.get("reactions") or raw.get("点赞量"))
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


def output_status_for(record: dict[str, Any]) -> str:
    required_ok = all(
        [
            record.get("post_url"),
            record.get("posted_at"),
            record.get("time_confirmed"),
            record.get("story_summary"),
            record.get("summary_source") == "article",
            record.get("lead_link_status") == "qualified",
            record.get("landing_url") or record.get("article_url"),
        ]
    )
    return "ready_for_output" if required_ok else "needs_enrichment"


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

    landing_url = clean_article_url(raw.get("landing_url") or raw.get("article_url"))
    lead_url_raw = clean_article_url(raw.get("lead_url_raw") or raw.get("comment_article_url") or "")
    article_url = landing_url
    lead_link_source = raw.get("lead_link_source") or ""
    lead_link_status = raw.get("lead_link_status") or ""
    if lead_link_status != "qualified" and lead_url_raw and lead_link_source in {"comment", "comment_reply"} and is_external_landing_url(landing_url):
        lead_link_status = "qualified"
    relative_time = str(raw.get("relative_time_text") or raw.get("post_time_text") or "").strip()
    posted_at = normalize_posted_at(raw.get("posted_at") or raw.get("posted_at_raw") or "")
    time_confirmed = bool(posted_at)
    time_source = raw.get("time_source") or ("exact" if posted_at else "")
    candidate_date = raw.get("posted_date") or ""
    if not candidate_date and posted_at:
        candidate_date = datetime.strptime(posted_at, "%Y年%m月%d日 %H:%M").strftime("%y%m%d")
    if target_date and candidate_date and candidate_date != target_date:
        return None, f"outside_target_date:{candidate_date or 'unknown'}"

    views, likes, engagement = parse_engagement(raw)
    note_parts = []
    if not posted_at:
        note_parts.append("发帖时间待确认，需通过FB时间悬停提示获取精确时间")
    if target_date and not candidate_date:
        note_parts.append("目标日期待确认")
    if not article_url:
        note_parts.append("评论/回复引流落地链接待确认")
    if not raw.get("article_summary"):
        note_parts.append("文章概要待生成")
    if lead_link_status != "qualified":
        note_parts.append("评论区或评论回复引流链接待确认")
    if views is None and likes is None and not engagement:
        note_parts.append("互动数据未确认")
    if parse_count(raw.get("shares") or raw.get("分享数")) is None:
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
        "story_summary": clean_story_placeholder(raw),
        "summary_source": "article" if raw.get("article_summary") else "pending_article_summary",
        "posted_date": candidate_date,
        "posted_at": posted_at,
        "relative_time_text": relative_time,
        "time_confirmed": time_confirmed,
        "time_source": time_source,
        "views": views,
        "likes": likes,
        "comments": parse_count(raw.get("comments") or raw.get("评论数")),
        "shares": parse_count(raw.get("shares") or raw.get("分享数")),
        "engagement_data": engagement,
        "crawl_status": "captured",
        "coverage_note": raw.get("coverage_note") or "",
        "note": "；".join(note_parts),
        "raw_payload": raw,
    }
    record["output_status"] = output_status_for(record)
    record["crawl_status"] = record["output_status"] if record["output_status"] == "ready_for_output" else "needs_enrichment"
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

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    raw_posts = payload.get("posts") if isinstance(payload, dict) else payload
    defaults = {
        "account_name": args.account_name,
        "account_url": args.account_url,
        "account_type": args.account_type,
        "source_skill": "fb-competitor-collector",
    }
    prepared: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    media_candidates: list[dict[str, Any]] = []
    coverage_warnings: list[dict[str, Any]] = []
    for raw in raw_posts:
        record, reason = prepare_record(raw, defaults, args.target_date)
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
        "needs_enrichment": sum(1 for item in prepared if item.get("crawl_status") == "needs_enrichment"),
        "media_candidate_count": len(media_candidates),
        "media_suspect_count": len(media_suspects),
        "covered_media_suspect_count": len(covered_media_suspects),
        "coverage_warning_count": len(coverage_warnings),
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
