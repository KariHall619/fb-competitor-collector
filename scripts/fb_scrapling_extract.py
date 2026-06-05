#!/usr/bin/env python3
"""Extract Facebook fields from already-loaded Facebook HTML with Scrapling."""

from __future__ import annotations

import json
import re
import sys
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

try:
    from scrapling import Selector
except Exception:  # pragma: no cover - reported as structured JSON.
    Selector = None  # type: ignore[assignment]


FACEBOOK_HOSTS = {"facebook.com", "fb.watch", "meta.com"}
MEDIA_HOST_PATTERNS = (
    "giphy.com",
    "tenor.com",
    "fbcdn.net",
    "cdninstagram.com",
)
URL_PATTERN = re.compile(r"(?:https?://|www\.)[^\s<>\"'，。；、)）\]]+", re.I)


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_href(value: str, base_url: str) -> str:
    if not value:
        return ""
    try:
        href = urljoin(base_url, value)
        parsed = urlparse(href)
        host = parsed.netloc.lower().removeprefix("www.")
        if (host.endswith("l.facebook.com") or host in {"m.facebook.com", "mobile.facebook.com"}) and parsed.query:
            target = parse_qs(parsed.query).get("u", [""])[0]
            if target:
                return urljoin(base_url, unquote(target))
        return href
    except Exception:
        return ""


def is_story_landing_url(href: str) -> bool:
    try:
        parsed = urlparse(href)
    except Exception:
        return False
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.lower()
    if parsed.scheme not in {"http", "https"}:
        return False
    if host in FACEBOOK_HOSTS or any(host.endswith("." + item) for item in FACEBOOK_HOSTS):
        return False
    if any(host == item or host.endswith("." + item) for item in MEDIA_HOST_PATTERNS):
        return False
    if re.search(r"\.(?:gif|jpe?g|png|webp|svg|mp4|mov|webm|m3u8|mp3|wav)(?:$|[?#])", path):
        return False
    if re.search(r"\b(?:image|img|media|static|cdn|assets?)\b", host) and not re.search(r"[a-z0-9-]{12,}", path, re.I):
        return False
    return True


def plain_text_links(text: str, base_url: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for match in URL_PATTERN.finditer(text):
        raw = match.group(0).rstrip(".,;!?")
        href = raw if raw.lower().startswith("http") else "https://" + raw
        normalized = normalize_href(href, base_url)
        if normalized and is_story_landing_url(normalized):
            links.append({"href": normalized, "text": raw, "source_kind": "plain_text"})
    return links


def element_text(element: Any) -> str:
    try:
        return clean(element.get_all_text(separator="\n", strip=True))
    except Exception:
        return clean(getattr(element, "text", ""))


def element_lines(element: Any) -> list[str]:
    try:
        raw = str(element.get_all_text(separator="\n", strip=True) or "")
    except Exception:
        raw = str(getattr(element, "text", "") or "")
    return [clean(line) for line in raw.splitlines() if clean(line)]


def element_attr(element: Any, name: str) -> str:
    try:
        return str(element.attrib.get(name, "") or "")
    except Exception:
        return ""


def anchor_links(block: Any, base_url: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    try:
        anchors = block.css("a[href]")
    except Exception:
        anchors = []
    for anchor in anchors:
        href = normalize_href(element_attr(anchor, "href"), base_url)
        if href and is_story_landing_url(href):
            links.append(
                {
                    "href": href,
                    "text": clean(element_text(anchor) or element_attr(anchor, "aria-label")),
                    "source_kind": "anchor",
                }
            )
    return links


def external_link_stats(block: Any, base_url: str) -> dict[str, Any]:
    anchor_count = 0
    plaintext_count = 0
    domain_filtered: list[str] = []
    redirect_unwrapped: list[dict[str, str]] = []
    try:
        anchors = block.css("a[href]")
    except Exception:
        anchors = []
    for anchor in anchors:
        raw = element_attr(anchor, "href")
        normalized = normalize_href(raw, base_url)
        if not normalized:
            continue
        if normalized != urljoin(base_url, raw):
            redirect_unwrapped.append({"raw": raw, "normalized": normalized})
        try:
            parsed = urlparse(normalized)
        except Exception:
            continue
        host = parsed.netloc.lower().removeprefix("www.")
        if parsed.scheme in {"http", "https"} and host and not (host == "facebook.com" or host.endswith(".facebook.com")):
            anchor_count += 1
            if not is_story_landing_url(normalized):
                domain_filtered.append(normalized)
    text = element_text(block)
    for match in URL_PATTERN.finditer(text):
        raw = match.group(0).rstrip(".,;!?")
        href = raw if raw.lower().startswith("http") else "https://" + raw
        normalized = normalize_href(href, base_url)
        if normalized:
            plaintext_count += 1
            if not is_story_landing_url(normalized):
                domain_filtered.append(normalized)
    return {
        "anchor_link_count": anchor_count,
        "plaintext_link_count": plaintext_count,
        "external_link_count_in_dom": anchor_count + plaintext_count,
        "domain_filtered": sorted(set(domain_filtered))[:50],
        "redirect_unwrapped": redirect_unwrapped[:50],
    }


def unique_links(links: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    output: list[dict[str, str]] = []
    for link in links:
        href = link.get("href", "")
        if not href or href in seen:
            continue
        seen.add(href)
        output.append(link)
    return output


def has_comment_context(text: str) -> bool:
    return bool(
        re.search(r"\bReply\b|\breplied\b|\bresponded\b|回复|\bLike\b", text, re.I)
        or re.search(r"\bjust now\b|\b\d+\s*(?:m|min|h|hr|d|day|w|wk)\b|刚刚|\d+\s*(?:分钟|小时|天|周)", text, re.I)
        or re.search(r"\bAuthor\b|作者", text, re.I)
    )


def owner_matched(text: str, account_name: str) -> bool:
    account = clean(account_name).lower()
    if not account:
        return True
    lines = [clean(line).lower() for line in str(text or "").splitlines() if clean(line)]
    head = lines[:24]
    return any(
        line == account
        or line.startswith(account + " replied")
        or line.startswith(account + " responded")
        or account in line[: max(len(account) + 40, 80)]
        for line in head
    )


def owner_matched_lines(lines: list[str], account_name: str) -> bool:
    account = clean(account_name).lower()
    if not account:
        return True
    head = [line.lower() for line in lines[:32]]
    return any(
        line == account
        or line.startswith(account + " replied")
        or line.startswith(account + " responded")
        or account in line[: max(len(account) + 40, 80)]
        for line in head
    ) or author_marked_lines(lines)


def author_marked_lines(lines: list[str]) -> bool:
    return any(re.fullmatch(r"author|作者", line.strip(), re.I) for line in lines[:40])


def looks_shell_or_ad(text: str) -> bool:
    return bool(
        re.search(
            r"Sponsored|Suggested for you|Create a post|What's on your mind|Privacy\s*·\s*Terms|Ads Manager|Feed posts",
            text,
            re.I,
        )
    )


def score_candidate(text: str, link: dict[str, str], account_name: str) -> int:
    score = 0
    if owner_matched(text, account_name):
        score += 80
    if re.search(r"\bAuthor\b|作者", text, re.I):
        score += 35
    if re.search(r"\bReply\b|\breplied\b|\bresponded\b|回复", text, re.I):
        score += 30
    if re.search(r"\bLike\b|\d+\s*(?:m|min|h|hr|d|day|w|wk)\b|刚刚|\d+\s*(?:分钟|小时|天|周)", text, re.I):
        score += 20
    if link.get("source_kind") == "plain_text":
        score += 10
    if looks_shell_or_ad(text):
        score -= 120
    if len(text) > 3500:
        score -= 40
    return score


def candidate_blocks(page: Any) -> list[Any]:
    selectors = [
        "[role='article']",
        "li",
        "div[aria-label]",
        "div",
    ]
    blocks: list[Any] = []
    seen: set[str] = set()
    for selector in selectors:
        try:
            matches = page.css(selector)
        except Exception:
            matches = []
        for block in matches:
            html = clean(getattr(block, "html_content", "") or str(block)[:500])
            if html in seen:
                continue
            seen.add(html)
            blocks.append(block)
    return blocks


def parse_count(value: str) -> int | None:
    text = clean(value).replace(",", "")
    match = re.search(r"([\d.]+)\s*([kKmMwW万]?)", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).lower()
    if unit == "k":
        number *= 1000
    if unit == "m":
        number *= 1000000
    if unit in {"w", "万"}:
        number *= 10000
    return round(number)


def is_facebook_content_url(href: str) -> bool:
    try:
        parsed = urlparse(href)
    except Exception:
        return False
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    query = parse_qs(parsed.query)
    if "facebook.com" not in host and "fb.watch" not in host:
        return False
    return bool(
        "/posts/" in path
        or "/groups/" in path and "/posts/" in path
        or "/reel/" in path
        or "/videos/" in path
        or "/video/" in path
        or "/watch/" in path
        or "/photo.php" in path
        or "/photo/" in path
        or "/photos/" in path
        or "/share/" in path
        or "/permalink.php" in path
        or "story_fbid" in query
        or "v" in query
        or "fbid" in query
    )


def facebook_link_kind(href: str) -> str:
    try:
        parsed = urlparse(href)
    except Exception:
        return "none"
    path = parsed.path.lower()
    query = parse_qs(parsed.query)
    if "/posts/" in path or "/permalink.php" in path or "story_fbid" in query:
        return "post"
    if any(item in path for item in ["/reel/", "/watch/", "/video", "/photo", "/share/"]) or "v" in query or "fbid" in query:
        return "media"
    return "facebook"


def clean_facebook_url(href: str, base_url: str) -> str:
    normalized = normalize_href(href, base_url)
    if not normalized:
        return ""
    try:
        parsed = urlparse(normalized)
        query = parse_qs(parsed.query)
        keep = []
        for key, values in query.items():
            if key in {"story_fbid", "id", "fbid", "v", "set", "type"}:
                keep.extend((key, value) for value in values)
        from urllib.parse import urlencode, urlunparse

        return urlunparse((parsed.scheme or "https", parsed.netloc.removeprefix("www."), parsed.path, "", urlencode(keep), ""))
    except Exception:
        return normalized


def relative_time_text(text: str) -> bool:
    return bool(re.match(r"^(just now|yesterday|\d+\s*(m|min|h|hr|d|day|w|wk)|刚刚|\d+\s*分钟|\d+\s*小时|昨天|\d+\s*天|\d+\s*周)$", clean(text), re.I))


def extract_homepage_candidates(page: Any, payload: dict[str, Any]) -> list[dict[str, Any]]:
    base_url = str(payload.get("url") or "https://www.facebook.com/")
    blocks = candidate_blocks(page)
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in blocks:
        text = element_text(block)
        if not text or len(text) < 25 or len(text) > 12000 or looks_shell_or_ad(text):
            continue
        fb_links = []
        try:
            anchors = block.css("a[href]")
        except Exception:
            anchors = []
        for anchor in anchors:
            href = clean_facebook_url(element_attr(anchor, "href"), base_url)
            if href and is_facebook_content_url(href):
                fb_links.append(
                    {
                        "href": href,
                        "text": clean(element_text(anchor) or element_attr(anchor, "aria-label")),
                        "kind": facebook_link_kind(href),
                    }
                )
        if not fb_links:
            continue
        primary = next((item for item in fb_links if item["kind"] == "post"), None) or fb_links[0]
        if primary["href"] in seen:
            continue
        seen.add(primary["href"])
        external = unique_links(anchor_links(block, base_url) + plain_text_links(text, base_url))
        lines = [clean(line) for line in text.splitlines() if clean(line)]
        time_line = next((line for line in lines[:20] if relative_time_text(line)), "")
        output.append(
            {
                "post_url": primary["href"],
                "raw_fb_url": primary["href"],
                "selected_post_link_kind": primary["kind"],
                "source_split": "scrapling",
                "post_time_text": time_line,
                "relative_time_text": time_line,
                "first_line": lines[0] if lines else "",
                "story_summary": clean(" ".join(lines[:8]))[:800],
                "raw_text": text[:1500],
                "lead_url_raw": external[0]["href"] if external else "",
                "article_url": external[0]["href"] if external else "",
                "landing_url": external[0]["href"] if external else "",
                "lead_link_source": "post_cta" if external else "",
                "lead_link_status": "qualified" if external else "missing",
                "time_texts": [time_line] if time_line else [],
                "extractor": "scrapling",
            }
        )
    return output


def extract_engagement(page: Any) -> dict[str, Any]:
    text = element_text(page)
    result: dict[str, Any] = {
        "raw": "",
        "views": None,
        "likes": None,
        "reactions": None,
        "comments": None,
        "shares": None,
        "source": "scrapling_detail_dom",
        "confidence": "scrapling_text",
    }
    patterns = [
        ("views", r"([\d.,]+\s*(?:K|M|万|w)?)\s*(?:views?|plays?|次播放|播放|浏览)"),
        ("comments", r"([\d.,]+\s*(?:K|M|万|w)?)\s*(?:comments?|评论)"),
        ("shares", r"([\d.,]+\s*(?:K|M|万|w)?)\s*(?:shares?|分享)"),
        ("reactions", r"(?:All reactions|reactions?|likes?|赞)[^0-9]{0,20}([\d.,]+\s*(?:K|M|万|w)?)"),
        ("reactions", r"([\d.,]+\s*(?:K|M|万|w)?)\s*(?:reactions?|likes?|赞)"),
    ]
    raw_parts = []
    for key, pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        value = parse_count(match.group(1))
        if value is None:
            continue
        result[key] = value
        if key == "reactions":
            result["likes"] = result.get("likes") if result.get("likes") is not None else value
        raw_parts.append(f"{key}:{value}")
    result["raw"] = "；".join(raw_parts)
    result["detail_engagement_data"] = result["raw"]
    if result["raw"]:
        result["confidence"] = "anchored"
    return result


def extract_post_type(page: Any) -> dict[str, Any]:
    html = str(getattr(page, "body", "") or "")
    text = element_text(page)
    hrefs = []
    try:
        hrefs = [element_attr(anchor, "href") for anchor in page.css("a[href]")]
    except Exception:
        hrefs = []
    combined = " ".join([html[:20000], text[:5000], " ".join(hrefs)]).lower()
    if "/reel/" in combined or "/watch/" in combined or "/videos/" in combined or re.search(r"\b(video|reel|watch)\b", combined):
        return {"post_type": "视频", "source": "scrapling_detail_dom"}
    if "/photo" in combined or re.search(r"\b(photo|image)\b", combined):
        return {"post_type": "图文", "source": "scrapling_detail_dom"}
    if text:
        return {"post_type": "文字", "source": "scrapling_detail_dom"}
    return {"post_type": "", "source": "scrapling_detail_dom"}


def extract(payload: dict[str, Any]) -> dict[str, Any]:
    if Selector is None:
        return {"ok": False, "error": "scrapling_not_available", "candidates": []}
    html = str(payload.get("html") or "")
    base_url = str(payload.get("url") or "https://www.facebook.com/")
    account_name = str(payload.get("account_name") or "")
    comment_mode = str(payload.get("mode") or "default")
    if not html:
        return {"ok": False, "error": "missing_html", "candidates": []}
    page = Selector(html, url=base_url, adaptive=True, adaptive_domain="facebook.com")
    results: list[dict[str, Any]] = []
    parse_candidates: list[dict[str, Any]] = []
    aggregate_stats = {
        "anchor_link_count": 0,
        "plaintext_link_count": 0,
        "external_link_count_in_dom": 0,
        "domain_filtered": [],
        "redirect_unwrapped": [],
    }
    for block in candidate_blocks(page):
        text = element_text(block)
        lines = element_lines(block)
        if not text or len(text) > 12000 or looks_shell_or_ad(text):
            continue
        stats = external_link_stats(block, base_url)
        aggregate_stats["anchor_link_count"] += int(stats.get("anchor_link_count") or 0)
        aggregate_stats["plaintext_link_count"] += int(stats.get("plaintext_link_count") or 0)
        aggregate_stats["external_link_count_in_dom"] += int(stats.get("external_link_count_in_dom") or 0)
        aggregate_stats["domain_filtered"].extend(stats.get("domain_filtered") or [])
        aggregate_stats["redirect_unwrapped"].extend(stats.get("redirect_unwrapped") or [])
        links = unique_links(anchor_links(block, base_url) + plain_text_links(text, base_url))
        author_marked = author_marked_lines(lines)
        owner_match = owner_matched_lines(lines, account_name)
        comment_context = has_comment_context(text)
        if not links:
            if stats.get("external_link_count_in_dom"):
                parse_candidates.append(
                    {
                        "author_block_matched": owner_match,
                        "comment_context": comment_context,
                        "score": 0,
                        "links": [],
                        "reject_reason": "domain_filtered_or_unusable_link",
                        "block_text": text[:500],
                    }
                )
            continue
        if not owner_match:
            parse_candidates.append(
                {
                    "author_block_matched": False,
                    "comment_context": comment_context,
                    "score": 0,
                    "links": [link.get("href", "") for link in links],
                    "reject_reason": "author_block_unmatched",
                    "block_text": text[:500],
                }
            )
            continue
        if not comment_context and not author_marked:
            parse_candidates.append(
                {
                    "author_block_matched": True,
                    "comment_context": False,
                    "score": 0,
                    "links": [link.get("href", "") for link in links],
                    "reject_reason": "comment_context_unproven",
                    "block_text": text[:500],
                }
            )
            continue
        for link in links:
            score = score_candidate(text, link, account_name)
            if score <= 0:
                parse_candidates.append(
                    {
                        "author_block_matched": True,
                        "comment_context": comment_context,
                        "score": score,
                        "links": [link.get("href", "")],
                        "reject_reason": "score_below_threshold",
                        "block_text": text[:500],
                    }
                )
                continue
            source = "comment_reply" if re.search(r"\bReply\b|\breplied\b|\bresponded\b|回复", text, re.I) else "comment"
            parse_candidates.append(
                {
                    "author_block_matched": True,
                    "comment_context": comment_context,
                    "score": score,
                    "links": [link.get("href", "")],
                    "reject_reason": "",
                    "block_text": text[:500],
                }
            )
            results.append(
                {
                    "href": link["href"],
                    "text": link.get("text", ""),
                    "block_text": text[:900],
                    "source": source,
                    "owner_matched": True,
                    "author_marked": author_marked,
                    "comment_context": comment_context,
                    "comment_mode": comment_mode,
                    "source_kind": link.get("source_kind", ""),
                    "extractor": "scrapling",
                    "score": score,
                }
            )
    results.sort(key=lambda item: int(item.get("score") or 0), reverse=True)
    deduped: list[dict[str, Any]] = []
    seen_hrefs: set[str] = set()
    for item in results:
        href = item.get("href", "")
        if href in seen_hrefs:
            continue
        seen_hrefs.add(href)
        deduped.append(item)
    homepage_candidates = extract_homepage_candidates(page, payload)
    engagement = extract_engagement(page)
    post_type = extract_post_type(page)
    aggregate_stats["domain_filtered"] = sorted(set(aggregate_stats["domain_filtered"]))[:50]
    aggregate_stats["redirect_unwrapped"] = aggregate_stats["redirect_unwrapped"][:50]
    return {
        "ok": True,
        "candidates": homepage_candidates,
        "real_post_count": len(homepage_candidates),
        "lead_candidates": deduped[:20],
        "lead_candidate_count": len(deduped),
        "lead_diagnostics": {
            "author_block_matched": any(item.get("author_block_matched") for item in parse_candidates),
            "candidate_count": len(deduped),
            **aggregate_stats,
        },
        "parse_candidates": parse_candidates[:100],
        "engagement": engagement,
        "post_type": post_type,
        "candidate_count": len(deduped),
    }


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        result = extract(payload if isinstance(payload, dict) else {})
    except Exception as exc:
        result = {"ok": False, "error": str(exc), "candidates": []}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
