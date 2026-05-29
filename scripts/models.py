#!/usr/bin/env python3
"""Shared data normalization helpers."""

from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import parse_qs, urlencode, unquote, urlparse, urlunparse
from typing import Any


POST_URL_KEYS = ("post_url", "fb_post_url", "Facebook帖子链接", "帖子链接")
ARTICLE_URL_KEYS = ("article_url", "landing_url", "comment_article_url", "文章链接")
SUMMARY_KEYS = ("article_summary", "story_summary", "topic_content", "简述", "故事概要")
POSTED_AT_KEYS = ("posted_at", "发帖时间精确值")
FACEBOOK_INTERNAL_HOSTS = {
    "facebook.com",
    "m.facebook.com",
    "mbasic.facebook.com",
    "www.facebook.com",
    "fb.watch",
    "meta.com",
    "www.meta.com",
    "messenger.com",
    "www.messenger.com",
}
ESTIMATED_TIME_SOURCES = {"relative_hour", "relative_estimated", "relative_label"}
COMMENT_LEAD_SOURCES = {"comment", "comment_reply"}
RELATIVE_TIME_RE = re.compile(
    r"^(?:just now|yesterday|\d+\s*(?:m|min|h|hr|d|day|w|wk)|刚刚|\d+\s*分钟|\d+\s*小时|昨天|\d+\s*天|\d+\s*周)$",
    re.I,
)
TRACKING_QUERY_PREFIXES = ("utm_", "__")
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "comment_id",
    "reply_comment_id",
    "notif_id",
    "notif_t",
    "ref",
    "refid",
    "mibextid",
    "rdid",
    "share_url",
}

EN_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def first_value(raw: dict[str, Any], keys: tuple[str, ...], default: Any = "") -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return default


def append_note(note: str, item: str) -> str:
    parts = [part for part in str(note or "").split("；") if part]
    if item not in parts:
        parts.append(item)
    return "；".join(parts)


def parse_count(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    match = re.search(r"([\d.]+)\s*([kKmMwW万]?)", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).lower()
    if unit == "k":
        number *= 1_000
    elif unit in {"m"}:
        number *= 1_000_000
    elif unit in {"w", "万"}:
        number *= 10_000
    return int(number)


def canonicalize_post_url(value: Any) -> str:
    """Normalize common Facebook post/reel URL variants for dedupe."""

    if value in (None, ""):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if "l.facebook.com" in parsed.netloc:
        qs = parse_qs(parsed.query)
        if qs.get("u"):
            return canonicalize_post_url(unquote(qs["u"][0]))
    netloc = parsed.netloc.lower()
    if netloc.startswith("m."):
        netloc = netloc[2:]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    qs = parse_qs(parsed.query)

    story_fbid = (qs.get("story_fbid") or qs.get("fbid") or [""])[0]
    account_id = (qs.get("id") or [""])[0]
    if story_fbid and account_id:
        return f"https://facebook.com/{account_id}/posts/{story_fbid}"

    # Keep the stable prefix through the post/reel identifier and discard tracking.
    parts = [part for part in path.split("/") if part]
    if "posts" in parts:
        idx = parts.index("posts")
        if idx > 0 and idx + 1 < len(parts):
            return f"https://facebook.com/{parts[idx - 1]}/posts/{parts[idx + 1]}"
    if "permalink.php" in path and story_fbid:
        return f"https://facebook.com/permalink/{story_fbid}"
    if "reel" in parts:
        idx = parts.index("reel")
        if idx + 1 < len(parts):
            return f"https://facebook.com/reel/{parts[idx + 1]}"
    if "watch" in parts and qs.get("v"):
        return f"https://facebook.com/watch/{qs['v'][0]}"
    if "photo.php" in path and story_fbid:
        return f"https://facebook.com/photo/{story_fbid}"

    return urlunparse(("https", netloc or "facebook.com", path, "", "", ""))


def facebook_link_kind(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        parsed = urlparse(str(value).strip())
    except Exception:
        return ""
    netloc = parsed.netloc.lower()
    path = parsed.path.lower()
    qs = parse_qs(parsed.query)
    if "facebook.com" not in netloc and "fb.watch" not in netloc:
        return "external"
    if "/posts/" in path or "story.php" in path or "permalink.php" in path or qs.get("story_fbid"):
        return "parent_post"
    if "/reel/" in path:
        return "reel"
    if "photo.php" in path or "/photo/" in path or qs.get("fbid"):
        return "photo"
    if "/watch/" in path or "/videos/" in path or qs.get("v"):
        return "video"
    return "facebook"


def is_external_landing_url(value: Any) -> bool:
    if value in (None, ""):
        return False
    try:
        parsed = urlparse(str(value).strip())
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host_without_www = host[4:]
    else:
        host_without_www = host
    return host_without_www not in FACEBOOK_INTERNAL_HOSTS and not host_without_www.endswith(".facebook.com")


def _drop_tracking_query(query: str, *, keep_keys: set[str] | None = None) -> str:
    keep_keys = keep_keys or set()
    kept: list[tuple[str, str]] = []
    for key, values in parse_qs(query, keep_blank_values=True).items():
        if key in keep_keys:
            kept.extend((key, value) for value in values)
            continue
        if key in TRACKING_QUERY_KEYS or any(key.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES):
            continue
        kept.extend((key, value) for value in values)
    return urlencode(kept, doseq=True)


def clean_post_url(value: Any) -> str:
    """Return a readable Facebook post URL for business output."""

    if value in (None, ""):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if "l.facebook.com" in parsed.netloc:
        qs = parse_qs(parsed.query)
        if qs.get("u"):
            return clean_post_url(unquote(qs["u"][0]))
    netloc = parsed.netloc.lower()
    if netloc.startswith("m."):
        netloc = netloc[2:]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    cleaned_query = _drop_tracking_query(parsed.query, keep_keys={"story_fbid", "id", "fbid", "v", "set", "type"})
    return urlunparse((parsed.scheme or "https", netloc or "facebook.com", parsed.path, "", cleaned_query, ""))


def clean_article_url(value: Any) -> str:
    """Resolve l.facebook.com redirects and remove common tracking params."""

    if value in (None, ""):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if "l.facebook.com" in parsed.netloc:
        qs = parse_qs(parsed.query)
        if qs.get("u"):
            return clean_article_url(unquote(qs["u"][0]))
    cleaned_query = _drop_tracking_query(parsed.query)
    netloc = parsed.netloc.lower()
    return urlunparse((parsed.scheme or "https", netloc, parsed.path, "", cleaned_query, ""))


def comment_lead_landing_url(lead_url_raw: Any, lead_link_source: Any) -> str:
    """Return the comment/reply lead URL when it is a real external landing page.

    Facebook detail pages can contain unrelated right-column or feed ads. A link
    found in the account's own comment/reply is more authoritative than generic
    external links discovered elsewhere on the page.
    """

    source = str(lead_link_source or "").strip()
    if source not in COMMENT_LEAD_SOURCES:
        return ""
    cleaned = clean_article_url(lead_url_raw)
    return cleaned if is_external_landing_url(cleaned) else ""


def normalize_date(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip()
    if re.fullmatch(r"\d{6}", text):
        return text
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y年%m月%d日 %H:%M", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text, fmt).strftime("%y%m%d")
        except ValueError:
            pass
    return text


def normalize_posted_at(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    english_match = re.fullmatch(
        r"(?:[A-Za-z]+,\s+)?([A-Za-z]+)\s+(\d{1,2}),\s+(20\d\d)\s+at\s+(\d{1,2}):(\d{2})\s+([AP]M)",
        text,
        flags=re.I,
    )
    if english_match:
        month_name, day, year, hour, minute, ampm = english_match.groups()
        month = EN_MONTHS.get(month_name.lower())
        if month:
            hour_num = int(hour)
            if ampm.upper() == "PM" and hour_num != 12:
                hour_num += 12
            if ampm.upper() == "AM" and hour_num == 12:
                hour_num = 0
            return f"{year}年{month}月{int(day)}日 {hour_num:02d}:{minute}"
    english_without_year_match = re.fullmatch(
        r"(?:[A-Za-z]+,\s+)?([A-Za-z]+)\s+(\d{1,2})\s+at\s+(\d{1,2}):(\d{2})\s+([AP]M)",
        text,
        flags=re.I,
    )
    if english_without_year_match:
        month_name, day, hour, minute, ampm = english_without_year_match.groups()
        month = EN_MONTHS.get(month_name.lower())
        if month:
            hour_num = int(hour)
            if ampm.upper() == "PM" and hour_num != 12:
                hour_num += 12
            if ampm.upper() == "AM" and hour_num == 12:
                hour_num = 0
            return f"{datetime.now().year}年{month}月{int(day)}日 {hour_num:02d}:{minute}"
    chinese_ampm_match = re.fullmatch(
        r"(?:星期[一二三四五六日天]\s*)?(20\d\d)[年/-](\d{1,2})[月/-](\d{1,2})日?\s*(上午|下午|中午|凌晨|晚上)\s*(\d{1,2}):(\d{2})",
        text,
    )
    if chinese_ampm_match:
        year, month, day, marker, hour, minute = chinese_ampm_match.groups()
        hour_num = int(hour)
        if marker in {"下午", "晚上"} and hour_num != 12:
            hour_num += 12
        if marker == "凌晨" and hour_num == 12:
            hour_num = 0
        return f"{year}年{int(month)}月{int(day)}日 {hour_num:02d}:{minute}"
    for fmt in ("%Y-%m-%d %H:%M", "%Y年%m月%d日 %H:%M"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y年%-m月%-d日 %H:%M")
        except ValueError:
            pass
    match = re.fullmatch(r"(20\d\d)[年/-](\d{1,2})[月/-](\d{1,2})日?\s+(\d{1,2}):(\d{2})", text)
    if match:
        year, month, day, hour, minute = match.groups()
        return f"{year}年{int(month)}月{int(day)}日 {int(hour):02d}:{minute}"
    return text if re.fullmatch(r"20\d\d年\d{1,2}月\d{1,2}日 \d{2}:\d{2}", text) else ""


def is_relative_time_label(value: Any) -> bool:
    if value in (None, ""):
        return False
    return bool(RELATIVE_TIME_RE.fullmatch(str(value).strip()))


def normalize_post_time(value: Any, now: datetime | None = None) -> str:
    """Convert absolute post dates to YYMMDD.

    Relative Facebook labels such as 1h or 1d are intentionally not converted
    here. They are crawl-time clues, not proof of a calendar date.
    """

    if value in (None, ""):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if is_relative_time_label(text):
        return ""
    direct = normalize_date(text)
    if re.fullmatch(r"\d{6}", direct):
        return direct
    return ""


def normalize_post(raw: dict[str, Any], defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    defaults = defaults or {}
    raw_post_url = first_value(raw, POST_URL_KEYS)
    parent_post_url = clean_post_url(raw.get("parent_post_url") or "")
    post_url = parent_post_url or clean_post_url(raw_post_url)
    raw_fb_url = clean_post_url(raw.get("raw_fb_url") or raw_post_url)
    canonical_post_url = raw.get("canonical_post_url") or canonicalize_post_url(parent_post_url or raw_fb_url or post_url)
    lead_url_raw = clean_article_url(raw.get("lead_url_raw") or raw.get("comment_article_url") or "")
    lead_link_source = raw.get("lead_link_source") or ""
    lead_link_status = raw.get("lead_link_status") or ""
    article_url = clean_article_url(first_value(raw, ARTICLE_URL_KEYS))
    lead_landing_url = comment_lead_landing_url(lead_url_raw, lead_link_source)
    landing_url = lead_landing_url or clean_article_url(raw.get("landing_url") or article_url)
    if lead_landing_url:
        article_url = lead_landing_url
    if lead_link_status != "qualified" and lead_landing_url:
        lead_link_status = "qualified"
    story_summary = first_value(raw, SUMMARY_KEYS)
    posted_at = normalize_posted_at(first_value(raw, POSTED_AT_KEYS))
    posted_date_source = raw.get("posted_date") or posted_at or raw.get("post_time") or raw.get("发帖时间") or ""
    posted_date = normalize_post_time(posted_date_source)
    engagement_raw = raw.get("engagement_data") or raw.get("互动数据") or ""
    views = parse_count(raw.get("views") or raw.get("播放量") or raw.get("浏览量"))
    likes = parse_count(raw.get("likes") or raw.get("点赞量") or raw.get("reactions"))
    comments = parse_count(raw.get("comments") or raw.get("评论数"))
    shares = parse_count(raw.get("shares") or raw.get("分享数"))
    note = raw.get("note") or raw.get("备注") or ""
    if views is None and likes is None and comments is None and shares is None:
        note = append_note(note, "互动数据未确认")

    time_source = raw.get("time_source") or defaults.get("time_source", "")
    post = {
        "account_name": raw.get("account_name") or raw.get("账号名") or defaults.get("account_name", ""),
        "account_url": raw.get("account_url") or raw.get("账号主页链接") or defaults.get("account_url", ""),
        "account_type": raw.get("account_type") or raw.get("账号类型") or defaults.get("account_type", "competitor"),
        "post_url": post_url,
        "canonical_post_url": canonical_post_url,
        "raw_fb_url": raw_fb_url,
        "parent_post_url": parent_post_url,
        "fb_link_kind": raw.get("fb_link_kind") or facebook_link_kind(raw_fb_url or post_url),
        "post_type": raw.get("post_type") or raw.get("帖子类型") or defaults.get("post_type", ""),
        "posted_date": posted_date,
        "posted_at": posted_at,
        "relative_time_text": raw.get("relative_time_text") or raw.get("post_time_text") or "",
        "article_url": article_url,
        "lead_url_raw": lead_url_raw,
        "landing_url": landing_url,
        "lead_link_status": lead_link_status,
        "lead_link_source": lead_link_source,
        "story_summary": story_summary,
        "views": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "crawled_at": raw.get("crawled_at") or datetime.now().isoformat(timespec="seconds"),
        "source_skill": raw.get("source_skill") or defaults.get("source_skill", "manual-import"),
        "note": note,
        "engagement_raw": engagement_raw,
        "crawl_status": raw.get("crawl_status") or defaults.get("crawl_status", "imported"),
        "output_status": raw.get("output_status") or defaults.get("output_status", ""),
        "time_confirmed": bool(raw.get("time_confirmed")) if "time_confirmed" in raw else bool(posted_at),
        "time_source": time_source,
        "summary_source": raw.get("summary_source") or ("article" if raw.get("article_summary") else ""),
        "coverage_note": raw.get("coverage_note") or defaults.get("coverage_note", ""),
        "first_seen_at": raw.get("first_seen_at") or datetime.now().isoformat(timespec="seconds"),
        "last_seen_at": raw.get("last_seen_at") or datetime.now().isoformat(timespec="seconds"),
        "raw_payload": json.dumps(raw, ensure_ascii=False),
    }
    if not post["output_status"]:
        required_ok = all(
            [
                post.get("post_url"),
                post.get("posted_at"),
                post.get("time_confirmed"),
                post.get("story_summary"),
                post.get("summary_source") == "article",
                post.get("lead_link_status") == "qualified",
                post.get("landing_url") or post.get("article_url"),
            ]
        )
        post["output_status"] = "ready_for_output" if required_ok else "needs_enrichment"
    if post["crawl_status"] in {"", "imported", "captured"}:
        post["crawl_status"] = post["output_status"] if post["output_status"] == "ready_for_output" else "needs_enrichment"
    return post


def feishu_row(post: dict[str, Any], extra: dict[str, Any] | None = None) -> list[Any]:
    extra = extra or {}
    row = [
        post.get("account_name", ""),
        post.get("account_url", ""),
        post.get("account_type", ""),
        post.get("post_url", ""),
        post.get("post_type", ""),
        post.get("posted_at") or post.get("posted_date", ""),
        post.get("article_url", ""),
        post.get("story_summary", ""),
        post.get("views") if post.get("views") is not None else "",
        post.get("likes") if post.get("likes") is not None else "",
        post.get("crawled_at", ""),
        post.get("source_skill", ""),
        post.get("note", ""),
    ]
    if extra:
        row.extend(extra.values())
    return row


POST_HEADERS = [
    "账号",
    "账户类型",
    "帖子链接",
    "帖子类型",
    "发帖时间",
    "文章链接",
    "故事概要",
    "互动数据（点赞量）",
    "浏览量",
    "是否采用",
    "对应站内链接",
]


def output_account_type(value: Any) -> str:
    text = str(value or "").strip()
    if text == "competitor":
        return "竞品"
    if text == "internal":
        return "内部"
    return text


def output_row(post: dict[str, Any]) -> list[Any]:
    parts = []
    if post.get("likes") is not None:
        parts.append(f"点赞量：{post.get('likes')}")
    if post.get("comments") is not None:
        parts.append(f"评论数：{post.get('comments')}")
    if post.get("shares") is not None:
        parts.append(f"分享数：{post.get('shares')}")
    engagement = "；".join(parts) or post.get("engagement_raw") or ""
    account = post.get("account_name") or post.get("account_url") or ""
    return [
        account,
        output_account_type(post.get("account_type")),
        post.get("post_url") or post.get("raw_fb_url", ""),
        post.get("post_type", ""),
        post.get("posted_at") or post.get("posted_date", ""),
        post.get("landing_url") or post.get("article_url", ""),
        post.get("story_summary", ""),
        engagement,
        post.get("views") if post.get("views") is not None else "",
        "",
        "",
    ]
