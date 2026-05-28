#!/usr/bin/env python3
"""Validate local MVP prerequisites on Mac or Windows."""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.error
import urllib.request
from typing import Any

from config_loader import deep_get, load_config


OPENCLI_MIN_MAJOR = 1
OPENCLI_MIN_MINOR = 8


def check_command(path: str, args: list[str] | None = None) -> dict[str, Any]:
    from pathlib import Path

    args = args or ["--version"]
    p = Path(path)
    found_on_path = False
    if not p.exists():
        from shutil import which

        found = which(path)
        if found:
            p = Path(found)
            found_on_path = True
    result = {"path": path, "resolved_path": str(p), "exists": p.exists(), "found_on_path": found_on_path}
    if p.exists():
        proc = subprocess.run([str(p), *args], text=True, capture_output=True, check=False)
        result.update({"ok": proc.returncode == 0, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()})
    return result


def check_invocation(command: list[str]) -> dict[str, Any]:
    if not command:
        return {"ok": False, "exists": False, "error": "empty command"}
    executable = check_command(command[0], command[1:] + ["--version"] if len(command) > 1 else ["--version"])
    return {"command": command, **executable}


def parse_version(text: str) -> tuple[int, int, int] | None:
    cleaned = text.strip().lstrip("v")
    parts = cleaned.split(".")
    if len(parts) < 2:
        return None
    try:
        major = int(parts[0])
        minor = int(parts[1])
        patch = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return None
    return major, minor, patch


def version_ok(text: str) -> bool:
    version = parse_version(text)
    if not version:
        return False
    major, minor, _patch = version
    return major > OPENCLI_MIN_MAJOR or (major == OPENCLI_MIN_MAJOR and minor >= OPENCLI_MIN_MINOR)


def run_cli(path: str, args: list[str]) -> dict[str, Any]:
    command = check_command(path)
    if not command.get("exists"):
        return {"ok": False, "error": "cli not found"}
    proc = subprocess.run([str(command["resolved_path"]), *args], text=True, capture_output=True, check=False)
    payload: Any = None
    text = proc.stdout.strip()
    if text:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = text
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": payload,
        "stderr": proc.stderr.strip(),
    }


def cli_config_value(result: dict[str, Any]) -> str:
    text = result.get("stdout")
    if not isinstance(text, str):
        return ""
    if ":" not in text:
        return text.strip()
    return text.split(":", 1)[1].split("(", 1)[0].strip()


def auth_status_detail(result: dict[str, Any]) -> dict[str, Any]:
    payload = result.get("stdout")
    if isinstance(payload, dict):
        identity = payload.get("identity", "")
        token_status = payload.get("tokenStatus", "")
        return {
            "identity": identity,
            "token_status": token_status,
            "user_name": payload.get("userName", ""),
            "ready": identity == "user" and token_status == "valid",
            "needs_refresh": identity == "user" and token_status == "needs_refresh",
            "sandbox_keychain_error": False,
        }
    stderr = result.get("stderr", "")
    sandbox_keychain_error = "keychain Get failed" in stderr or "keychain not initialized" in stderr
    return {
        "identity": "",
        "token_status": "",
        "user_name": "",
        "ready": False,
        "needs_refresh": False,
        "sandbox_keychain_error": sandbox_keychain_error,
    }


def read_opencli_daemon_status(port: int) -> dict[str, Any]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/status",
        headers={"X-OpenCLI": "1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return {"ok": True, "status": payload}
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": str(exc)}


def check_opencli(opencli_command: list[str] | str, *, daemon_port: int = 19825) -> dict[str, Any]:
    command_list = opencli_command if isinstance(opencli_command, list) else [opencli_command]
    command = check_invocation(command_list)
    if not command.get("exists"):
        return {
            "ok": False,
            "status": "opencli_missing",
            "command": command,
            "daemon_status": read_opencli_daemon_status(daemon_port),
            "message": "OpenCLI 未安装或不在 PATH；请安装 @jackwener/opencli 后重试。",
        }

    version_text = str(command.get("stdout") or "")
    daemon_status = read_opencli_daemon_status(daemon_port)
    status_payload = daemon_status.get("status") if isinstance(daemon_status.get("status"), dict) else {}
    extension_connected = bool(status_payload.get("extensionConnected"))
    daemon_running = bool(daemon_status.get("ok") and status_payload.get("ok"))
    version_ready = version_ok(version_text)
    ready = bool(command.get("ok") and version_ready and daemon_running and extension_connected)
    if ready:
        status = "ready"
        message = "OpenCLI Browser Bridge 已连接，优先使用当前用户 Chrome 标签页采集 Facebook。"
    elif command.get("ok") and not version_ready:
        status = "opencli_version_too_old"
        message = "OpenCLI 版本过低；请升级到 @jackwener/opencli 1.8.0 或更新版本。"
    elif command.get("ok") and not daemon_running:
        status = "daemon_not_running"
        message = "OpenCLI daemon 未运行；运行 opencli doctor 或任一 opencli browser 命令会尝试自动启动。"
    elif command.get("ok") and not extension_connected:
        status = "browser_bridge_not_connected"
        message = "OpenCLI CLI/daemon 可用，但 Browser Bridge 扩展未连接到当前 Chrome profile；请在业务 Chrome 中安装并启用 OpenCLI 扩展。"
    else:
        status = "opencli_not_ready"
        message = "OpenCLI 命令不可用；请先修复 OpenCLI 安装。"
    return {
        "ok": ready,
        "status": status,
        "message": message,
        "command": command,
        "daemon_port": daemon_port,
        "daemon_status": daemon_status,
    }


def recommended_capture_route(report: dict[str, Any]) -> dict[str, str]:
    if report["opencli_browser_bridge"].get("ok"):
        return {
            "route": "opencli_browser_bridge",
            "message": "优先通过 OpenCLI Browser Bridge 读取业务人员当前 Chrome 已打开且肉眼可见的 Facebook 标签页，再导入/去重/同步飞书。",
        }
    return {
        "route": "blocked_until_opencli_ready",
        "message": "OpenCLI Browser Bridge 未就绪，已停止实时采集；请先安装/启用 OpenCLI Chrome 扩展并确认 opencli doctor 通过。",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    args = parser.parse_args()
    config = load_config(args.config)
    cli_path = config.get("lark_cli_path", "lark-cli")
    opencli_path = config.get("opencli_path", "opencli")
    runtime = config.get("runtime", {})
    report = {
        "runtime": runtime,
        "lark_cli": check_command(cli_path),
        "lark_auth_status": run_cli(cli_path, ["auth", "status"]),
        "lark_strict_mode": run_cli(cli_path, ["config", "strict-mode"]),
        "lark_default_as": run_cli(cli_path, ["config", "default-as"]),
        "database_path": config.get("database_path", "data/posts.sqlite"),
        "source_spreadsheet_configured": bool(deep_get(config, "feishu.source_spreadsheet_url", "")),
        "output_spreadsheet_configured": bool(deep_get(config, "feishu.output_spreadsheet_url", "")),
        "opencli_browser_bridge": check_opencli(
            config.get("opencli_command") or [opencli_path],
            daemon_port=int(config.get("opencli_daemon_port", 19825) or 19825),
        ),
    }
    auth_detail = auth_status_detail(report["lark_auth_status"])
    strict_mode = cli_config_value(report["lark_strict_mode"])
    default_as = cli_config_value(report["lark_default_as"])
    report["lark_identity_detail"] = auth_detail
    report["lark_user_identity_ready"] = auth_detail["ready"]
    report["lark_user_identity_forced"] = strict_mode == "user" and default_as == "user"
    if auth_detail["sandbox_keychain_error"]:
        report["lark_identity_message"] = "当前进程读不到飞书 keychain；请在非沙盒环境运行 lark-cli auth status 验证用户身份"
    elif auth_detail["needs_refresh"]:
        report["lark_identity_message"] = "飞书 CLI 是用户身份，但 token 需要刷新；请运行 lark-cli auth login 或执行一次可刷新 token 的飞书命令"
    elif not report["lark_user_identity_forced"]:
        report["lark_identity_message"] = "飞书 CLI 未强制用户身份；需要 default-as user 且 strict-mode user"
    else:
        report["lark_identity_message"] = "飞书 CLI 用户身份检查通过"
    report["recommended_capture_route"] = recommended_capture_route(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = bool(report["lark_cli"].get("ok"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
