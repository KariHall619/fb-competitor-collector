#!/usr/bin/env python3
"""Apply Codex-written Chinese article summaries to prepared posts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def output_status_for(post: dict) -> str:
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
    return "ready_for_output" if required_ok else "needs_enrichment"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--summaries", required=True, help="JSON object keyed by post_url/canonical_post_url/article_url")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    summaries = json.loads(Path(args.summaries).read_text(encoding="utf-8"))
    posts = payload.get("posts", [])
    applied = 0
    missing = []
    for post in posts:
        keys = [post.get("post_url"), post.get("canonical_post_url"), post.get("article_url")]
        summary = next((summaries.get(key) for key in keys if key and summaries.get(key)), "")
        if not summary:
            missing.append(post.get("post_url"))
            continue
        post["story_summary"] = summary.strip()
        post["summary_source"] = "article"
        note = post.get("note") or ""
        post["note"] = "；".join(part for part in note.split("；") if part and part != "文章概要待生成")
        post["output_status"] = output_status_for(post)
        post["crawl_status"] = post["output_status"] if post["output_status"] == "ready_for_output" else "needs_enrichment"
        applied += 1

    payload["article_summary_applied"] = applied
    payload["article_summary_missing"] = missing
    payload["ready"] = sum(1 for item in posts if item.get("crawl_status") == "ready")
    payload["ready_for_output"] = sum(1 for item in posts if item.get("output_status") == "ready_for_output")
    payload["needs_enrichment"] = sum(1 for item in posts if item.get("crawl_status") == "needs_enrichment")
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "applied": applied, "missing": len(missing), "output": args.output}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
