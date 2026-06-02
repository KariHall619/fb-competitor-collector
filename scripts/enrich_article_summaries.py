#!/usr/bin/env python3
"""Attach article material to prepared posts for Codex Chinese summarization."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path

from fetch_article_material import extract_material
from config_loader import deep_get, load_config


def article_source_url(post: dict) -> str:
    return post.get("article_url") or post.get("landing_url") or ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=0)
    args = parser.parse_args()

    config = load_config(args.config)
    concurrency = args.concurrency or int(deep_get(config, "performance.article_concurrency", 5))
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    posts = payload.get("posts", [])
    material_attached = 0
    errors = []
    pending: list[tuple[int, str]] = []
    cache: dict[str, dict] = {}
    for index, post in enumerate(posts):
        if args.limit and index >= args.limit:
            break
        if post.get("article_material"):
            continue
        article_url = article_source_url(post)
        if not article_url:
            continue
        if article_url in cache:
            post["article_material"] = cache[article_url]
            material_attached += int(bool(cache[article_url].get("ok")))
            continue
        pending.append((index, article_url))

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        futures = {executor.submit(extract_material, article_url): (index, article_url) for index, article_url in pending}
        for future in as_completed(futures):
            index, article_url = futures[future]
            try:
                material = future.result()
            except Exception as exc:
                material = {"ok": False, "article_url": article_url, "error": str(exc)}
            cache[article_url] = material
            post = posts[index]
            post["article_material"] = material
            if not material.get("ok"):
                errors.append({"post_url": post.get("post_url"), "article_url": article_url, "error": material.get("error")})
                continue
            material_attached += 1

    for index, post in enumerate(posts):
        article_url = article_source_url(post)
        if post.get("article_material") or not article_url or article_url not in cache:
            continue
        material = cache[article_url]
        post["article_material"] = material
        if not material.get("ok"):
            errors.append({"post_url": post.get("post_url"), "article_url": article_url, "error": material.get("error")})
            continue
        material_attached += 1

    payload["article_material_attached"] = material_attached
    payload["article_material_cache_entries"] = len(cache)
    payload["article_summary_errors"] = errors
    payload["ready"] = sum(1 for item in posts if item.get("crawl_status") == "ready")
    payload["partial_review"] = sum(1 for item in posts if item.get("output_status") == "partial_review")
    payload["needs_enrichment"] = sum(1 for item in posts if item.get("crawl_status") == "needs_enrichment")
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "material_attached": material_attached, "errors": len(errors), "output": args.output}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
