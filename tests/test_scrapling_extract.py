#!/usr/bin/env python3
"""Focused tests for Scrapling-based Facebook DOM extraction."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_extract(payload: dict[str, object]) -> dict[str, object]:
    python = os.environ.get("SCRAPLING_PYTHON") or sys.executable
    result = subprocess.run(
        [python, str(ROOT / "scripts" / "fb_scrapling_extract.py")],
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
    assert data["lead_diagnostics"]["candidate_count"] >= 1
    assert data["lead_diagnostics"]["plaintext_link_count"] >= 1


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


def test_author_badge_without_reply_context_still_extracts() -> None:
    data = run_extract(
        {
            "url": "https://www.facebook.com/soullines/posts/pfbid",
            "account_name": "Soul Lines",
            "mode": "detail",
            "html": """
            <html><body>
              <div role="article">
                <span>Author</span>
                <span>Soul Lines</span>
                <div>Full ending here: https://kaylestore.net/final-ending/</div>
              </div>
            </body></html>
            """,
        }
    )
    assert data["lead_candidate_count"] == 1
    assert data["lead_candidates"][0]["href"] == "https://kaylestore.net/final-ending/"
    assert data["lead_candidates"][0]["author_marked"] is True
    assert data["lead_diagnostics"]["author_block_matched"] is True


if __name__ == "__main__":
    test_author_reply_plain_text_link()
    test_homepage_candidate_and_external_link()
    test_author_badge_without_reply_context_still_extracts()
    print("scrapling extraction tests passed")
