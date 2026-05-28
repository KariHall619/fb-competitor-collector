#!/usr/bin/env python3
"""Attach article material to prepared posts for Codex Chinese summarization."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fetch_article_material import extract_material


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    posts = payload.get("posts", [])
    material_attached = 0
    errors = []
    for index, post in enumerate(posts):
        if args.limit and index >= args.limit:
            break
        if post.get("article_material"):
            continue
        article_url = post.get("landing_url") or post.get("article_url") or ""
        if not article_url:
            continue
        material = extract_material(article_url)
        post["article_material"] = material
        if not material.get("ok"):
            errors.append({"post_url": post.get("post_url"), "article_url": article_url, "error": material.get("error")})
            continue
        material_attached += 1

    payload["article_material_attached"] = material_attached
    payload["article_summary_errors"] = errors
    payload["ready"] = sum(1 for item in posts if item.get("crawl_status") == "ready")
    payload["needs_enrichment"] = sum(1 for item in posts if item.get("crawl_status") == "needs_enrichment")
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "material_attached": material_attached, "errors": len(errors), "output": args.output}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
