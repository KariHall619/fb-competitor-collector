#!/usr/bin/env python3
"""Focused tests for Scrapling-based Facebook DOM extraction."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_extract(payload: dict[str, object]) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "fb_scrapling_extract.py")],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def test_author_reply_plain_text_link() -> None:
    data = run_extract(
        {
            "url": "https://www.facebook.com/glasstory/posts/pfbid",
            "account_name": "GLAS Story",
            "mode": "default",
            "html": """
            <html><body>
              <div role="article">
                <span>GLAS Story</span>
                <span>PART 2: The furious pounding at the door did not stop.</span>
                <span>1h</span>
                <span>Like</span><span>Reply</span>
                <div>
                  <span>Author</span>
                  <span>GLAS Story</span>
                  <span>PART 3 - END: https://kaylestore.net/i-canceled-my-ex-mother-in-laws/</span>
                  <span>See more</span>
                  <span>1h</span>
                  <span>Like</span><span>Reply</span>
                </div>
              </div>
            </body></html>
            """,
        }
    )
    assert data["lead_candidate_count"] >= 1
    first = data["lead_candidates"][0]
    assert first["href"] == "https://kaylestore.net/i-canceled-my-ex-mother-in-laws/"
    assert first["source"] == "comment_reply"
    assert first["extractor"] == "scrapling"


def test_homepage_candidate_and_external_link() -> None:
    data = run_extract(
        {
            "url": "https://www.facebook.com/glasstory",
            "account_name": "GLAS Story",
            "html": """
            <html><body>
              <div role="article">
                <span>GLAS Story</span>
                <a href="/glasstory/posts/123456789">1h</a>
                <span>I Canceled My Ex-Mother-in-Law's Luxury Credit Card</span>
                <a href="https://kaylestore.net/story">Read more</a>
                <span>10 comments</span>
              </div>
            </body></html>
            """,
        }
    )
    assert data["real_post_count"] == 1
    post = data["candidates"][0]
    assert post["post_url"] == "https://facebook.com/glasstory/posts/123456789"
    assert post["lead_link_status"] == "qualified"
    assert post["landing_url"] == "https://kaylestore.net/story"


if __name__ == "__main__":
    test_author_reply_plain_text_link()
    test_homepage_candidate_and_external_link()
    print("scrapling extraction tests passed")
