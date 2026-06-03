#!/usr/bin/env python3
"""Generate Chinese story-summary JSON from exported article material."""

from __future__ import annotations

import argparse
import json
import re
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from story_summary_policy import story_summary_errors


FALLBACK_SUBJECT = "这篇文章"
THEME_RULES = [
    ("家庭关系", ("mother", "father", "daughter", "son", "wife", "husband", "bride", "groom", "baby", "newborn", "pregnant", "family")),
    ("秘密曝光", ("secret", "hidden", "discover", "discovers", "uncover", "uncovers", "exposed", "truth", "suspicious")),
    ("背叛冲突", ("betray", "betrayed", "affair", "cheat", "cheated", "divorce", "lie", "lied")),
    ("财产纠纷", ("inheritance", "estate", "will", "property", "house", "mansion", "company", "asset", "credit", "money")),
    ("豪门职场", ("ceo", "billionaire", "boss", "assistant", "contract", "marry", "marriage", "rich")),
    ("自我保护", ("protect", "action", "escape", "fight", "revenge", "plan", "control", "freeze", "save")),
    ("危机救援", ("kidnap", "rescue", "hospital", "accident", "police", "danger", "threat")),
    ("情感选择", ("love", "romance", "heart", "relationship", "choice", "wedding")),
]


def load_requests(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Summary requests file not found: {p}")
    payload = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Summary requests input must be a JSON object.")
    requests = payload.get("requests")
    if not isinstance(requests, list):
        raise ValueError("Summary requests input must contain a requests list.")
    if not all(isinstance(item, dict) for item in requests):
        raise ValueError("Every summary request must be a JSON object.")
    return payload


def clean_text(value: Any, *, limit: int = 180) -> str:
    text = str(value or "").strip()
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text[:limit].strip()


def has_chinese(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value or ""))


def english_keywords(text: str, *, limit: int = 4) -> list[str]:
    stop = {
        "about",
        "after",
        "again",
        "also",
        "and",
        "article",
        "because",
        "before",
        "from",
        "have",
        "into",
        "that",
        "their",
        "there",
        "this",
        "through",
        "with",
        "without",
        "woman",
        "man",
        "story",
        "video",
    }
    words = re.findall(r"[A-Za-z][A-Za-z'-]{3,}", text)
    seen: list[str] = []
    for word in words:
        lower = word.lower().strip("'")
        if lower in stop or lower in seen:
            continue
        seen.append(lower)
        if len(seen) >= limit:
            break
    return seen


def material_text(request: dict[str, Any]) -> str:
    material = request.get("article_material") if isinstance(request.get("article_material"), dict) else {}
    return " ".join(
        clean_text(material.get(key), limit=260)
        for key in ("title", "meta_description", "text_excerpt")
        if clean_text(material.get(key), limit=260)
    ).strip()


def chinese_material_phrase(text: str) -> str:
    cleaned = clean_text(text, limit=80)
    if not cleaned or not has_chinese(cleaned):
        return ""
    return cleaned.rstrip("。.!！?？")


def keyword_phrase(text: str) -> str:
    keywords = english_keywords(text)
    if not keywords:
        return ""
    return "、".join(keywords)


def theme_phrase(text: str, *, limit: int = 4) -> str:
    lower = text.lower()
    themes: list[str] = []
    for label, words in THEME_RULES:
        if any(word in lower for word in words):
            themes.append(label)
        if len(themes) >= limit:
            break
    return "、".join(themes)


def build_summary(request: dict[str, Any]) -> str:
    text = material_text(request)
    themes = theme_phrase(text)
    if themes:
        return f"这篇故事讲述一场围绕{themes}展开的短剧冲突，主角在异常线索中发现问题，经历对抗与转折后推动局势重新变化。"
    chinese_phrase = chinese_material_phrase(text)
    if chinese_phrase:
        return f"这篇故事讲述主角围绕相关事件发现矛盾并采取行动，剧情突出人物关系变化、冲突升级和后续反转。"
    if keyword_phrase(text):
        return f"这篇故事讲述主角从关键线索中发现矛盾，随后面对关系冲突、行动选择和结果反转，适合用于短剧内容判断。"
    account = clean_text(request.get("account_name"), limit=40)
    subject = f"{account}相关内容" if account else FALLBACK_SUBJECT
    return f"这篇故事围绕{subject}展开，概括主角面临的冲突、转折和结果，适合用于后续短剧内容判断。"


def summary_keys(request: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for key in ("post_url", "canonical_post_url", "article_url"):
        value = str(request.get(key) or "").strip()
        if value and value not in keys:
            keys.append(value)
    return keys


def generate_summaries(payload: dict[str, Any]) -> dict[str, Any]:
    summaries: dict[str, str] = {}
    rejected: list[dict[str, Any]] = []
    generated_request_count = 0
    for request in payload.get("requests") or []:
        keys = summary_keys(request)
        if not keys:
            rejected.append({"reason": "missing_summary_key", "request": request})
            continue
        summary = build_summary(request)
        candidate = {
            "story_summary": summary,
            "summary_source": "article",
            "article_material": request.get("article_material") if isinstance(request.get("article_material"), dict) else {},
        }
        errors = story_summary_errors(candidate)
        if errors:
            rejected.append({"reason": "summary_policy_rejected", "post_url": request.get("post_url"), "errors": errors})
            continue
        generated_request_count += 1
        for key in keys:
            summaries[key] = summary
    return {"summaries": summaries, "generated_request_count": generated_request_count, "rejected": rejected}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="summary_requests.json exported by export_summary_requests.py")
    parser.add_argument("--output", required=True, help="article_summaries.json for apply_article_summaries.py")
    args = parser.parse_args()

    try:
        payload = load_requests(args.input)
    except (FileNotFoundError, JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "run_status": "summary_generation_failed",
                    "stage": "input_load",
                    "complete": False,
                    "error": str(exc),
                    "input": args.input,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    result = generate_summaries(payload)
    Path(args.output).write_text(json.dumps(result["summaries"], ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "run_status": "summary_generated",
                "input": args.input,
                "output": args.output,
                "requested": len(payload.get("requests") or []),
                "generated": result["generated_request_count"],
                "summary_key_count": len(result["summaries"]),
                "rejected": result["rejected"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not result["rejected"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
