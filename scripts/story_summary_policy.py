#!/usr/bin/env python3
"""Policy helpers for final story-summary output."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any


MIN_CHINESE_CHARS = 8
MIN_SUMMARY_CHARS = 12


def text_value(value: Any) -> str:
    return str(value or "").strip()


def compact_text(value: Any) -> str:
    text = text_value(value).lower()
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)
    return text


def chinese_char_count(value: Any) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text_value(value)))


def contains_chinese(value: Any, *, min_chars: int = MIN_CHINESE_CHARS) -> bool:
    return chinese_char_count(value) >= min_chars


def _raw_payload(post: dict[str, Any]) -> dict[str, Any]:
    payload = post.get("raw_payload")
    if isinstance(payload, dict):
        return payload
    if not payload:
        return {}
    try:
        parsed = json.loads(str(payload))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def article_material_for_post(post: dict[str, Any]) -> dict[str, Any]:
    material = post.get("article_material")
    if isinstance(material, dict):
        return material
    payload = _raw_payload(post)
    material = payload.get("article_material")
    return material if isinstance(material, dict) else {}


def article_source_texts(post: dict[str, Any]) -> list[str]:
    material = article_material_for_post(post)
    values: list[str] = []
    for key in ("title", "meta_description", "text_excerpt"):
        value = material.get(key)
        if value:
            values.append(text_value(value))
    paragraphs = material.get("paragraphs")
    if isinstance(paragraphs, list):
        values.extend(text_value(item) for item in paragraphs[:5] if text_value(item))
    return [item for item in values if item]


def is_copied_article_material(summary: Any, material_texts: list[str]) -> bool:
    summary_text = text_value(summary)
    if not summary_text:
        return False
    summary_compact = compact_text(summary_text)
    if not summary_compact:
        return False
    for source in material_texts:
        source_text = text_value(source)
        source_compact = compact_text(source_text)
        if not source_compact:
            continue
        if summary_compact == source_compact:
            return True
        if len(summary_compact) >= 24 and summary_compact in source_compact:
            return True
        ratio = SequenceMatcher(None, summary_compact, source_compact).ratio()
        if len(summary_compact) >= 24 and ratio >= 0.92:
            return True
    return False


def story_summary_errors(post: dict[str, Any], *, summary: Any | None = None) -> list[str]:
    summary_text = text_value(post.get("story_summary") if summary is None else summary)
    errors: list[str] = []
    if not summary_text:
        return ["missing_article_summary"]
    if len(summary_text) < MIN_SUMMARY_CHARS:
        errors.append("story_summary_too_short")
    if not contains_chinese(summary_text):
        errors.append("story_summary_not_chinese")
    if is_copied_article_material(summary_text, article_source_texts(post)):
        errors.append("story_summary_copied_article_material")
    return errors


def has_valid_story_summary(post: dict[str, Any]) -> bool:
    return post.get("summary_source") == "article" and not story_summary_errors(post)

