#!/usr/bin/env python3
"""Focused tests for OpenCLI Browser Bridge recovery behavior."""

from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_check_env_launches_chrome_and_waits_for_bridge() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_env

    original_check = check_env.check_invocation
    original_read = check_env.read_opencli_daemon_status
    original_run = check_env.run_opencli_command
    original_open_chrome = check_env.open_chrome_for_bridge
    original_adapter = check_env.opencli_adapter_status
    try:
        statuses = [
            {"ok": True, "status": {"ok": True, "extensionConnected": False}},
            {"ok": True, "status": {"ok": True, "extensionConnected": False}},
            {"ok": True, "status": {"ok": True, "extensionConnected": True}},
        ]

        def fake_read(_port: int) -> dict:
            return statuses.pop(0) if statuses else {"ok": True, "status": {"ok": True, "extensionConnected": True}}

        check_env.check_invocation = lambda command: {
            "command": command,
            "path": command[0],
            "resolved_path": command[0],
            "exists": True,
            "ok": True,
            "stdout": "1.8.2",
            "stderr": "",
        }
        check_env.read_opencli_daemon_status = fake_read
        def fake_run(command, args, timeout=20):
            if args[:3] == ["browser", "fb-competitor", "tab"]:
                return {"ok": True, "returncode": 0, "stdout": "[]", "stderr": ""}
            return {"ok": True, "returncode": 0, "stdout": "doctor ok", "stderr": ""}

        check_env.run_opencli_command = fake_run
        check_env.open_chrome_for_bridge = lambda: {"ok": True, "returncode": 0, "command": ["open"]}
        check_env.opencli_adapter_status = lambda: {"ok": True, "exists": True, "path": "/tmp/adapter.js"}

        result = check_env.check_opencli(
            ["opencli"],
            daemon_port=19825,
            auto_fix=True,
            wait_seconds=0.01,
        )
    finally:
        check_env.check_invocation = original_check
        check_env.read_opencli_daemon_status = original_read
        check_env.run_opencli_command = original_run
        check_env.open_chrome_for_bridge = original_open_chrome
        check_env.opencli_adapter_status = original_adapter

    assert result["ok"] is True
    assert result["status"] == "ready"
    assert [step["step"] for step in result["auto_fix_steps"]] == [
        "opencli_daemon_restart",
        "opencli_doctor",
        "open_chrome_for_bridge",
        "wait_for_browser_bridge",
    ]
    assert result["browser_probe"]["ok"] is True


def test_check_env_requires_configured_opencli_browser_command() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_env

    original_check = check_env.check_invocation
    original_read = check_env.read_opencli_daemon_status
    original_run = check_env.run_opencli_command
    original_adapter = check_env.opencli_adapter_status
    try:
        check_env.check_invocation = lambda command: {
            "command": command,
            "path": command[0],
            "resolved_path": command[0],
            "exists": True,
            "ok": True,
            "stdout": "1.8.2",
            "stderr": "",
        }
        check_env.read_opencli_daemon_status = lambda _port: {
            "ok": True,
            "status": {"ok": True, "extensionConnected": True},
        }
        check_env.run_opencli_command = lambda command, args, timeout=20: {
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": "configured opencli cannot run browser commands",
        }
        check_env.opencli_adapter_status = lambda: {"ok": True, "exists": True, "path": "/tmp/adapter.js"}
        result = check_env.check_opencli(
            ["fake-opencli"],
            daemon_port=19825,
            auto_fix=False,
            session="fb-competitor",
        )
    finally:
        check_env.check_invocation = original_check
        check_env.read_opencli_daemon_status = original_read
        check_env.run_opencli_command = original_run
        check_env.opencli_adapter_status = original_adapter

    assert result["ok"] is False
    assert result["status"] == "browser_command_failed"
    assert result["browser_probe"]["ok"] is False
    assert result["blocking_issue"] == "browser_command_failed"


def test_check_env_reports_missing_opencli_adapter() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_env

    original_check = check_env.check_invocation
    original_read = check_env.read_opencli_daemon_status
    original_run = check_env.run_opencli_command
    original_adapter = check_env.opencli_adapter_status
    try:
        check_env.check_invocation = lambda command: {
            "command": command,
            "path": command[0],
            "resolved_path": command[0],
            "exists": True,
            "ok": True,
            "stdout": "1.8.2",
            "stderr": "",
        }
        check_env.read_opencli_daemon_status = lambda _port: {
            "ok": True,
            "status": {"ok": True, "extensionConnected": True},
        }
        check_env.run_opencli_command = lambda command, args, timeout=20: {
            "ok": True,
            "returncode": 0,
            "stdout": "[]",
            "stderr": "",
        }
        check_env.opencli_adapter_status = lambda: {"ok": False, "exists": False, "path": "/tmp/missing.js"}
        result = check_env.check_opencli(
            ["fake-opencli"],
            daemon_port=19825,
            auto_fix=False,
            session="fb-competitor",
        )
    finally:
        check_env.check_invocation = original_check
        check_env.read_opencli_daemon_status = original_read
        check_env.run_opencli_command = original_run
        check_env.opencli_adapter_status = original_adapter

    assert result["ok"] is False
    assert result["status"] == "adapter_missing"
    assert result["blocking_issue"] == "adapter_missing"


def test_run_accounts_does_not_preopen_account_tabs() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_accounts_job

    assert not hasattr(run_accounts_job, "prepare_account_tab")
    assert not hasattr(run_accounts_job, "prepare_account_tab_with_recovery")
    assert not hasattr(run_accounts_job, "close_account_tab")

    args = Namespace(
        config="config/settings.yaml",
        max_snapshots=32,
        min_snapshots=6,
        max_resume_passes=8,
        enrichment_limit=50,
        resume_stale_running_seconds=1800,
        target_date="260604",
        last_hours=24,
        resume_only=False,
        force_recover_running=False,
        status_only=False,
        expected_post_count=0,
        expected_labels="",
        sync=True,
        sync_audit=False,
        dry_run=False,
        allow_incomplete_success=False,
        require_coverage_complete=False,
        min_ledger_usable_rate=0,
        min_final_usable_rate=0,
        min_completion_rate=0,
        min_expected_post_coverage_rate=0,
        min_expected_label_coverage_rate=0,
    )
    command = run_accounts_job.account_job_command(
        args,
        {
            "account_url": "https://www.facebook.com/example",
            "account_name": "Example",
            "account_type": "competitor",
            "tab_page": "legacy-page",
        },
    )
    assert "--tab-page" not in command


if __name__ == "__main__":
    test_check_env_launches_chrome_and_waits_for_bridge()
    test_check_env_requires_configured_opencli_browser_command()
    test_check_env_reports_missing_opencli_adapter()
    test_run_accounts_does_not_preopen_account_tabs()
