#!/usr/bin/env python3
"""Fetch article pages and extract material for Chinese story summaries."""

from __future__ import annotations

import argparse
import html
import json
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from models import clean_article_url


class ArticleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.skip_stack: list[str] = []
        self.capture_title = False
        self.title_parts: list[str] = []
        self.paragraphs: list[str] = []
        self.current_tag = ""
        self.current_text: list[str] = []
        self.meta_description = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        if tag in {"script", "style", "noscript", "svg", "footer", "nav"}:
            self.skip_stack.append(tag)
            return
        if self.skip_stack:
            return
        if tag == "title":
            self.capture_title = True
        if tag == "meta":
            name = attrs_dict.get("name", "").lower()
            prop = attrs_dict.get("property", "").lower()
            if name == "description" or prop == "og:description":
                self.meta_description = attrs_dict.get("content", "").strip()
        if tag in {"p", "h1", "h2", "h3", "li"}:
            self.current_tag = tag
            self.current_text = []

    def handle_endtag(self, tag: str) -> None:
        if self.skip_stack:
            if self.skip_stack[-1] == tag:
                self.skip_stack.pop()
            return
        if tag == "title":
            self.capture_title = False
        if tag == self.current_tag and self.current_text:
            text = clean_text(" ".join(self.current_text))
            if len(text) >= 30:
                self.paragraphs.append(text)
            self.current_tag = ""
            self.current_text = []

    def handle_data(self, data: str) -> None:
        if self.skip_stack:
            return
        if self.capture_title:
            self.title_parts.append(data)
        if self.current_tag:
            self.current_text.append(data)


def clean_text(value: str) -> str:
    text = html.unescape(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_url(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125 Safari/537.36"
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def extract_material(url: str) -> dict[str, Any]:
    clean_url = clean_article_url(url)
    if not clean_url:
        return {"ok": False, "article_url": "", "error": "missing_article_url"}
    try:
        html_text = fetch_url(clean_url)
    except Exception as exc:
        return {"ok": False, "article_url": clean_url, "error": str(exc)}
    parser = ArticleTextParser()
    parser.feed(html_text)
    title = clean_text(" ".join(parser.title_parts))
    paragraphs = []
    seen = set()
    for para in parser.paragraphs:
        if para in seen:
            continue
        seen.add(para)
        paragraphs.append(para)
        if sum(len(item) for item in paragraphs) >= 5000:
            break
    return {
        "ok": bool(paragraphs or title or parser.meta_description),
        "article_url": clean_url,
        "title": title,
        "meta_description": clean_text(parser.meta_description),
        "paragraphs": paragraphs,
        "text_excerpt": "\n".join(paragraphs)[:5000],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="")
    parser.add_argument("--input", default="")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if args.url:
        result: Any = extract_material(args.url)
    else:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        posts = payload.get("posts") if isinstance(payload, dict) else payload
        result = [extract_material(item.get("article_url", "")) for item in posts]
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": args.output}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
