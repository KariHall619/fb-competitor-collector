#!/usr/bin/env python3
"""Focused tests for structured diagnostics and offline lead POC."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    bundled_python = "/Users/a1/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
    if Path(bundled_python).exists():
        env.setdefault("SCRAPLING_PYTHON", bundled_python)
    return subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, check=False)


def assert_structured_stage_diagnostics_and_output_audit(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from models import normalize_post
    from store import (
        connect,
        enqueue_enrichment_tasks_for_posts,
        mark_task_failed,
        pending_enrichment_tasks,
        record_output_audit,
        stage_attempts_for_posts,
        upsert_post,
    )

    conn = connect(tmp_path / "structured-diagnostics.sqlite")
    post = normalize_post(
        {
            "account_name": "Diagnostics Page",
            "account_url": "https://www.facebook.com/diagnosticspage",
            "account_type": "competitor",
            "post_url": "https://www.facebook.com/diagnosticspage/posts/one",
            "relative_time_text": "1h",
            "article_url": "https://story.example/diag",
            "crawled_at": "2026-06-03T12:00:00",
        },
        {"source_skill": "test"},
    )
    upsert_post(conn, post)
    enqueue_enrichment_tasks_for_posts(conn, [post])
    task = next(task for task in pending_enrichment_tasks(conn, stages=["lead_link"], limit=10))
    mark_task_failed(
        conn,
        task["id"],
        "parser_no_candidate_after_actions",
        retry_seconds=0,
        reason_code="L_LINK_NOT_IN_DOM",
        progress_signature='{"candidate_count":0}',
        payload={"candidate_count": 0},
    )

    attempts = stage_attempts_for_posts(conn, [post])
    assert attempts[-1]["reason_code"] == "L_LINK_NOT_IN_DOM"
    assert attempts[-1]["status"] == "failed"
    summary = record_output_audit(conn, [post], run_id="test-run", account_name="Diagnostics Page")
    assert summary["discovered"] == 1
    assert summary["lead_qualified"] == 0
    assert summary["blocked_by"]["L_LINK_NOT_IN_DOM"] >= 1
    row = conn.execute("SELECT blocked_by, top_missing_field FROM output_audit WHERE run_id = 'test-run'").fetchone()
    assert row is not None
    assert "L_LINK_NOT_IN_DOM" in row["blocked_by"]
    assert row["top_missing_field"]


def assert_offline_lead_poc_reports_recall_and_evidence(tmp_path: Path) -> None:
    html_path = tmp_path / "sample.html"
    html_path.write_text(
        """
        <html><body>
          <div role="article">
            <span>Author</span>
            <span>Soul Lines</span>
            <div>Read the ending at https://kaylestore.net/ending/</div>
          </div>
        </body></html>
        """,
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "id": "visible-author-link",
                    "html": str(html_path),
                    "url": "https://www.facebook.com/soullines/posts/one",
                    "account_name": "Soul Lines",
                    "expected_links": ["https://kaylestore.net/ending/"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report_path = tmp_path / "report.json"
    evidence_dir = tmp_path / "evidence"
    result = run(
        [
            PYTHON,
            "scripts/offline_lead_poc.py",
            "--manifest",
            str(manifest),
            "--output",
            str(report_path),
            "--evidence-dir",
            str(evidence_dir),
        ]
    )
    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["sample_count"] == 1
    assert report["summary"]["recall"] == 1.0
    sample = report["samples"][0]
    assert sample["hit_links"] == ["https://kaylestore.net/ending/"]
    assert Path(sample["evidence_ref"], "outerHTML.html").exists()
    assert Path(sample["evidence_ref"], "parse_candidates.json").exists()


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        tmp_path = Path(temp_dir)
        assert_structured_stage_diagnostics_and_output_audit(tmp_path)
        assert_offline_lead_poc_reports_recall_and_evidence(tmp_path)
    print("diagnostics and offline POC tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
