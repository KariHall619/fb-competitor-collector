#!/usr/bin/env python3
"""Validate local MVP prerequisites on Mac or Windows."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from config_loader import deep_get, load_config


DEFAULT_CHROME_PLUGIN_BASE = Path.home() / ".codex" / "plugins" / "cache" / "openai-bundled" / "chrome"
CHROME_PLUGIN_BASE = DEFAULT_CHROME_PLUGIN_BASE


def check_command(path: str) -> dict[str, Any]:
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
        proc = subprocess.run([str(p), "--version"], text=True, capture_output=True, check=False)
        result.update({"ok": proc.returncode == 0, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()})
    return result


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


def run_node_json(script: Path, args: list[str] | None = None) -> dict[str, Any]:
    args = args or []
    if not script.exists():
        return {"ok": False, "missing": True, "path": str(script)}
    proc = subprocess.run(
        ["node", str(script), *args],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    payload: Any = proc.stdout.strip()
    if payload:
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            pass
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": payload,
        "stderr": proc.stderr.strip(),
    }


def find_chrome_plugin_root(base: Path | str | None = None) -> Path | None:
    plugin_base = Path(base) if base is not None else CHROME_PLUGIN_BASE
    if not plugin_base.exists():
        return None
    candidates = [
        path.parent.parent
        for path in plugin_base.glob("*/scripts/browser-client.mjs")
        if (path.parent / "check-extension-installed.js").exists()
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: path.name, reverse=True)[0]


def check_chrome_extension(plugin_base: str | Path | None = None) -> dict[str, Any]:
    base = Path(plugin_base) if plugin_base else CHROME_PLUGIN_BASE
    plugin_root = find_chrome_plugin_root(base)
    if not plugin_root:
        return {
            "ok": False,
            "status": "chrome_plugin_missing",
            "plugin_base": str(base),
            "message": "Codex Chrome 插件包不存在，无法走当前用户 Chrome 标签页采集路线",
        }
    native_host = run_node_json(plugin_root / "scripts" / "check-native-host-manifest.js", ["--json"])
    extension = run_node_json(plugin_root / "scripts" / "check-extension-installed.js", ["--json"])
    installed = False
    enabled = False
    profile_path = ""
    extension_payload = extension.get("stdout")
    if isinstance(extension_payload, dict):
        installed = bool(extension_payload.get("installed"))
        enabled = bool(extension_payload.get("enabled"))
        profile_path = extension_payload.get("profilePath", "")
    native_payload = native_host.get("stdout")
    native_ok = bool(native_host.get("ok"))
    if isinstance(native_payload, dict):
        native_ok = bool(native_payload.get("correct"))
    ready = native_ok and installed and enabled
    if ready:
        status = "ready"
        message = "Codex Chrome Extension 可用，优先使用当前用户 Chrome 标签页采集"
    elif native_ok and not installed:
        status = "extension_not_installed"
        message = "native host 正常，但当前 Chrome profile 未安装 Codex Chrome Extension；需要先在业务使用的 Chrome profile 安装并启用扩展"
    elif native_ok and installed and not enabled:
        status = "extension_disabled"
        message = "Codex Chrome Extension 已安装但未启用；需要在 Chrome 扩展管理页启用"
    else:
        status = "native_host_invalid"
        message = "Codex Chrome Extension native host 未就绪；需要从 Codex 插件 UI 重新安装/修复 Chrome 插件"
    return {
        "ok": ready,
        "status": status,
        "message": message,
        "plugin_base": str(base),
        "plugin_root": str(plugin_root),
        "profile_path": profile_path,
        "native_host": native_host,
        "extension": extension,
    }


def recommended_capture_route(report: dict[str, Any]) -> dict[str, str]:
    if report["codex_chrome_extension"].get("ok"):
        return {
            "route": "codex_chrome_extension",
            "message": "优先读取业务人员当前 Chrome 已打开且肉眼可见的 Facebook 标签页，再导入/去重/同步飞书",
        }
    return {
        "route": "blocked_until_chrome_extension_ready",
        "message": "Codex Chrome Extension 未就绪，已停止采集；请先在业务使用的 Chrome profile 安装并启用扩展",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    args = parser.parse_args()
    config = load_config(args.config)
    cli_path = config.get("lark_cli_path", "lark-cli")
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
        "codex_chrome_extension": check_chrome_extension(config.get("codex_chrome_plugin_base")),
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
