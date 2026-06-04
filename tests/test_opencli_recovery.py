#!/usr/bin/env python3
"""Focused tests for OpenCLI Browser Bridge recovery behavior."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_check_env_launches_chrome_and_waits_for_bridge() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_env

    original_check = check_env.check_invocation
    original_read = check_env.read_opencli_daemon_status
    original_run = check_env.run_opencli_command
    original_open_chrome = check_env.open_chrome_for_bridge
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

    assert result["ok"] is False
    assert result["status"] == "browser_command_failed"
    assert result["browser_probe"]["ok"] is False
    assert result["blocking_issue"] == "browser_command_failed"


def test_run_accounts_retries_tab_open_after_opencli_recovery() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_accounts_job

    original_prepare = run_accounts_job.prepare_account_tab
    original_check = run_accounts_job.check_opencli
    calls: list[str] = []
    try:
        def fake_prepare(_config: dict, account: dict, *, enabled: bool = True) -> dict:
            calls.append(str(account["account_url"]))
            if len(calls) == 1:
                return {"ok": False, "returncode": 1, "error": "Browser Bridge extension not connected"}
            return {"ok": True, "tab": {"page": "page-1", "url": account["account_url"]}}

        run_accounts_job.prepare_account_tab = fake_prepare
        run_accounts_job.check_opencli = lambda *args, **kwargs: {"ok": True, "status": "ready"}
        result = run_accounts_job.prepare_account_tab_with_recovery(
            {"opencli_session": "fb-competitor"},
            {"account_url": "https://www.facebook.com/example"},
            enabled=True,
        )
    finally:
        run_accounts_job.prepare_account_tab = original_prepare
        run_accounts_job.check_opencli = original_check

    assert result["ok"] is True
    assert result["recovered_after_opencli_fix"] is True
    assert result["initial_open_account_tab"]["ok"] is False
    assert len(calls) == 2


def test_opencli_runtime_times_out_hung_commands() -> None:
    script = """
import { runOpencli } from './scripts/opencli_runtime.mjs';
const result = await runOpencli(['-e', 'setTimeout(() => {}, 10000)'], {
  command: [process.execPath],
  timeoutMs: 50,
});
console.log(JSON.stringify({ ok: result.ok, code: result.code, timeout: result.timeout, stderr: result.stderr }));
"""
    result = subprocess.run(
        ["node", "--input-type=module"],
        cwd=ROOT,
        input=script,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["code"] == 124
    assert payload["timeout"] is True
    assert "timed out" in payload["stderr"]


if __name__ == "__main__":
    test_check_env_launches_chrome_and_waits_for_bridge()
    test_check_env_requires_configured_opencli_browser_command()
    test_run_accounts_retries_tab_open_after_opencli_recovery()
    test_opencli_runtime_times_out_hung_commands()
