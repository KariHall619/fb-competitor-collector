#!/usr/bin/env python3
"""Fast capture pipeline: discover visible candidates, import them, and queue enrichment."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path

from config_loader import load_config


ROOT = Path(__file__).resolve().parents[1]


def run_command(command: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False, timeout=timeout)


def parse_stdout_json(result: subprocess.CompletedProcess[str]) -> dict:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "stdout": result.stdout, "stderr": result.stderr}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--account-url", required=True)
    parser.add_argument("--account-name", default="")
    parser.add_argument("--account-type", default="competitor")
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--partial", action="store_true", help="Import candidates and allow partial_review preview.")
    parser.add_argument("--sync-partial", action="store_true", help="Dry-run/write partial preview through import script.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-text", default="1500")
    args = parser.parse_args()

    load_config(args.config)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="fb-capture-pipeline-") as temp_dir:
        temp = Path(temp_dir)
        raw_path = temp / "raw.json"
        prepared_path = temp / "prepared.json"

        discover_started = time.monotonic()
        discover = run_command(
            [
                "node",
                "scripts/opencli_extract_current_tab.mjs",
                "--config",
                args.config,
                "--account-url",
                args.account_url,
                "--max-text",
                args.max_text,
            ]
        )
        discover_payload = parse_stdout_json(discover)
        if discover.returncode != 0 or not discover_payload.get("ok"):
            print(
                json.dumps(
                    {
                        "ok": False,
                        "stage": "discover",
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                        "discover_elapsed_ms": int((time.monotonic() - discover_started) * 1000),
                        "result": discover_payload,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return discover.returncode or 1
        raw_path.write_text(json.dumps(discover_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        prepare_started = time.monotonic()
        prepare = run_command(
            [
                "python3",
                "scripts/prepare_capture_result.py",
                "--input",
                str(raw_path),
                "--output",
                str(prepared_path),
                "--target-date",
                args.target_date,
                "--account-url",
                args.account_url,
                "--account-name",
                args.account_name,
                "--account-type",
                args.account_type,
            ]
        )
        if prepare.returncode != 0:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "stage": "prepare",
                        "stdout": prepare.stdout,
                        "stderr": prepare.stderr,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return prepare.returncode

        import_started = time.monotonic()
        import_command = [
            "python3",
            "scripts/import_existing_result.py",
            "--config",
            args.config,
            "--input",
            str(prepared_path),
            "--account-url",
            args.account_url,
            "--account-name",
            args.account_name,
            "--account-type",
            args.account_type,
        ]
        if args.sync_partial:
            import_command.append("--sync-partial")
            if args.dry_run:
                import_command.append("--dry-run")
        else:
            import_command.append("--no-sync")
        imported = run_command(import_command)
        if imported.returncode != 0:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "stage": "import",
                        "stdout": imported.stdout,
                        "stderr": imported.stderr,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return imported.returncode

        prepared_payload = json.loads(prepared_path.read_text(encoding="utf-8"))
        result = {
            "ok": True,
            "mode": "partial" if args.partial else "standard",
            "target_date": args.target_date,
            "raw_candidate_count": discover_payload.get("raw_candidate_count", 0),
            "post_count": discover_payload.get("post_count", 0),
            "capture_complete": discover_payload.get("capture_complete", True),
            "coverage": discover_payload.get("coverage", {}),
            "prepared": prepared_payload.get("prepared", 0),
            "coverage_note": prepared_payload.get("coverage_note", ""),
            "ready_for_output": prepared_payload.get("ready_for_output", 0),
            "partial_review": prepared_payload.get("partial_review", 0),
            "needs_enrichment": prepared_payload.get("needs_enrichment", 0),
            "timing_ms": {
                "discover": int((time.monotonic() - discover_started) * 1000),
                "prepare": int((time.monotonic() - prepare_started) * 1000),
                "import": int((time.monotonic() - import_started) * 1000),
                "total": int((time.monotonic() - started) * 1000),
            },
            "import_stdout": imported.stdout,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
