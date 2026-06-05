#!/usr/bin/env python3
"""Replay saved Facebook detail HTML snapshots against the lead parser."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from pipeline_diagnostics import reason_for_lead_evidence, write_evidence_bundle


def load_manifest(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("samples"), list):
        return [item for item in data["samples"] if isinstance(item, dict)]
    raise ValueError("manifest must be a JSON list or an object with samples")


def sample_html_path(sample: dict[str, Any], manifest_dir: Path) -> Path:
    value = sample.get("html") or sample.get("outerHTML") or sample.get("outer_html") or sample.get("path")
    if not value:
        raise ValueError(f"sample {sample.get('id') or '<unknown>'} missing html path")
    path = Path(str(value))
    return path if path.is_absolute() else manifest_dir / path


def expected_links(sample: dict[str, Any]) -> list[str]:
    value = sample.get("expected_links") or sample.get("ground_truth_links") or sample.get("lead_urls") or []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value if str(item).strip()]


def sample_id(sample: dict[str, Any], index: int) -> str:
    return str(sample.get("id") or sample.get("name") or f"sample-{index}")


def run_snapshot_parser(payload: dict[str, Any]) -> dict[str, Any]:
    python = os.environ.get("SCRAPLING_PYTHON") or sys.executable
    script = Path(__file__).resolve().parent / "fb_scrapling_extract.py"
    result = subprocess.run(
        [python, str(script)],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr or result.stdout or f"parser_exit={result.returncode}", "lead_candidates": []}
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"parser_json_failed:{exc}", "lead_candidates": []}
    return parsed if isinstance(parsed, dict) else {"ok": False, "error": "parser_returned_non_object", "lead_candidates": []}


def evaluate_sample(sample: dict[str, Any], index: int, manifest_dir: Path, evidence_dir: Path) -> dict[str, Any]:
    html_path = sample_html_path(sample, manifest_dir)
    html = html_path.read_text(encoding="utf-8")
    payload = {
        "html": html,
        "url": sample.get("url") or "https://www.facebook.com/",
        "account_name": sample.get("account_name") or "",
        "mode": sample.get("mode") or "detail",
    }
    parsed = run_snapshot_parser(payload)
    candidates = parsed.get("lead_candidates") or []
    got_links = [str(item.get("href") or "") for item in candidates if item.get("href")]
    expected = expected_links(sample)
    expected_set = set(expected)
    got_set = set(got_links)
    hits = sorted(expected_set.intersection(got_set))
    false_positives = sorted(got_set.difference(expected_set)) if expected_set else []
    diagnostics = parsed.get("lead_diagnostics") if isinstance(parsed.get("lead_diagnostics"), dict) else {}
    reason_code = ""
    if expected and not hits:
        reason_code = reason_for_lead_evidence(diagnostics)
    elif not expected and got_links:
        reason_code = "L_DOMAIN_FILTERED"
    elif not expected:
        reason_code = "L_NO_LINK_ON_PAGE"
    evidence_ref = write_evidence_bundle(
        evidence_dir,
        key=sample_id(sample, index),
        html=html,
        inner_text=str(sample.get("inner_text") or ""),
        expand_actions=sample.get("expand_actions") or {},
        link_stats=diagnostics,
        parse_candidates=parsed.get("parse_candidates") if isinstance(parsed.get("parse_candidates"), list) else [],
        stop_reason=reason_code,
    )
    return {
        "id": sample_id(sample, index),
        "html": str(html_path),
        "expected_links": expected,
        "got_links": got_links,
        "hit_links": hits,
        "missed_links": sorted(expected_set.difference(got_set)),
        "false_positive_links": false_positives,
        "reason_code": reason_code,
        "candidate_count": len(got_links),
        "lead_diagnostics": diagnostics,
        "evidence_ref": evidence_ref,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    expected_total = sum(len(item.get("expected_links") or []) for item in results)
    hit_total = sum(len(item.get("hit_links") or []) for item in results)
    false_positive_total = sum(len(item.get("false_positive_links") or []) for item in results)
    reason_counts: dict[str, int] = {}
    for item in results:
        code = str(item.get("reason_code") or "")
        if code:
            reason_counts[code] = reason_counts.get(code, 0) + 1
    recall = (hit_total / expected_total) if expected_total else 1.0
    false_positive_rate = (false_positive_total / max(1, sum(len(item.get("got_links") or []) for item in results)))
    return {
        "sample_count": len(results),
        "expected_link_count": expected_total,
        "hit_link_count": hit_total,
        "missed_link_count": expected_total - hit_total,
        "false_positive_count": false_positive_total,
        "recall": recall,
        "false_positive_rate": false_positive_rate,
        "reason_counts": dict(sorted(reason_counts.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, help="JSON manifest with HTML sample paths and expected links.")
    parser.add_argument("--output", default="", help="Optional report JSON path.")
    parser.add_argument("--evidence-dir", default="exports/evidence/offline_poc")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    samples = load_manifest(manifest_path)
    evidence_dir = Path(args.evidence_dir)
    results = [evaluate_sample(sample, index + 1, manifest_path.parent, evidence_dir) for index, sample in enumerate(samples)]
    report = {"ok": True, "summary": summarize(results), "samples": results}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
