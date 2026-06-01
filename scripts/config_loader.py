#!/usr/bin/env python3
"""Load and resolve this project's small JSON/YAML configuration."""

from __future__ import annotations

import copy
import json
import os
import platform
import re
import shutil
from pathlib import Path
from typing import Any


AUTO_VALUES = {"", "auto", "AUTO", "Auto", None}


def _scalar(value: str) -> Any:
    value = value.strip()
    if value == "":
        return ""
    if value in {"true", "True", "TRUE"}:
        return True
    if value in {"false", "False", "FALSE"}:
        return False
    if value in {"null", "None", "~"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    try:
        if "." not in value:
            return int(value)
        return float(value)
    except ValueError:
        return value


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]

    def parent_for(indent: int) -> Any:
        while stack and stack[-1][0] >= indent:
            stack.pop()
        return stack[-1][1]

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        parent = parent_for(indent)

        if stripped.startswith("- "):
            if not isinstance(parent, list):
                raise ValueError(f"List item without list parent: {raw}")
            item_text = stripped[2:].strip()
            if not item_text:
                item: dict[str, Any] = {}
                parent.append(item)
                stack.append((indent, item))
                continue
            if ":" in item_text:
                key, value = item_text.split(":", 1)
                item = {key.strip(): _scalar(value)}
                parent.append(item)
                stack.append((indent, item))
            else:
                parent.append(_scalar(item_text))
            continue

        if ":" not in stripped:
            raise ValueError(f"Unsupported YAML line: {raw}")
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value:
            parent[key] = _scalar(value)
            continue

        # Look ahead to decide dict vs list.
        next_content = ""
        for future in lines[i:]:
            if future.strip() and not future.lstrip().startswith("#"):
                next_content = future.strip()
                break
        child: Any = [] if next_content.startswith("- ") else {}
        parent[key] = child
        stack.append((indent, child))

    return root


def load_config(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        return resolve_runtime_config(json.loads(text))
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        return resolve_runtime_config(data or {})
    except ModuleNotFoundError:
        return resolve_runtime_config(_parse_simple_yaml(text))


def deep_get(config: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = config
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def platform_key(system: str | None = None) -> str:
    name = (system or platform.system()).lower()
    if name.startswith("darwin"):
        return "darwin"
    if name.startswith("win") or name in {"msys", "cygwin"}:
        return "windows"
    if name.startswith("linux"):
        return "linux"
    return name or "unknown"


def platform_aliases(key: str) -> list[str]:
    aliases = {
        "darwin": ["darwin", "macos", "mac"],
        "windows": ["windows", "win32", "win"],
        "linux": ["linux"],
    }
    return aliases.get(key, [key])


def platform_override(config: dict[str, Any], key: str) -> dict[str, Any]:
    overrides = config.get("platform_overrides") or {}
    if not isinstance(overrides, dict):
        return {}
    for alias in platform_aliases(key):
        value = overrides.get(alias)
        if isinstance(value, dict):
            return value
    return {}


def is_auto(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in AUTO_VALUES
    return False


def _env(environ: dict[str, str] | None = None) -> dict[str, str]:
    merged = dict(os.environ)
    if environ:
        merged.update(environ)
    return merged


def expand_config_value(value: Any, *, environ: dict[str, str] | None = None) -> str:
    text = str(value).strip()
    env = _env(environ)

    def replace_percent(match: re.Match[str]) -> str:
        return env.get(match.group(1), match.group(0))

    text = re.sub(r"%([^%]+)%", replace_percent, text)
    for name, env_value in env.items():
        text = text.replace(f"${{{name}}}", env_value)
        text = re.sub(rf"\${re.escape(name)}\b", lambda _match: env_value, text)
    if text == "~" or text.startswith("~/") or text.startswith("~\\"):
        home = env.get("HOME") or env.get("USERPROFILE") or str(Path.home())
        text = home + text[1:]
    return text


def resolve_executable(command: str, *, environ: dict[str, str] | None = None) -> str:
    expanded = expand_config_value(command, environ=environ)
    path_env = _env(environ).get("PATH")
    found = shutil.which(expanded, path=path_env)
    return found or expanded


def _first_existing_command(names: list[str], *, environ: dict[str, str] | None = None) -> str:
    path_env = _env(environ).get("PATH")
    for name in names:
        found = shutil.which(name, path=path_env)
        if found:
            return found
    return names[0]


def resolve_lark_cli_path(
    config: dict[str, Any],
    *,
    key: str,
    environ: dict[str, str] | None = None,
) -> tuple[str, str, Any]:
    raw = config.get("lark_cli_path", "auto")
    override = platform_override(config, key).get("lark_cli_path")
    if is_auto(raw):
        candidate = override
        source = f"platform_overrides.{key}.lark_cli_path" if not is_auto(candidate) else "PATH"
    else:
        candidate = raw
        source = "lark_cli_path"

    if is_auto(candidate):
        names = ["lark-cli.cmd", "lark-cli.exe", "lark-cli"] if key == "windows" else ["lark-cli"]
        return _first_existing_command(names, environ=environ), source, raw
    return resolve_executable(str(candidate), environ=environ), source, raw


def resolve_opencli_invocation(
    config: dict[str, Any],
    *,
    key: str,
    environ: dict[str, str] | None = None,
) -> tuple[str, list[str], str, Any]:
    raw = config.get("opencli_path", "auto")
    override = platform_override(config, key).get("opencli_path")
    if is_auto(raw):
        candidate = override
        source = f"platform_overrides.{key}.opencli_path" if not is_auto(candidate) else "PATH"
    else:
        candidate = raw
        source = "opencli_path"

    if is_auto(candidate):
        names = ["opencli.cmd", "opencli.exe", "opencli"] if key == "windows" else ["opencli"]
        opencli = _first_existing_command(names, environ=environ)
        if Path(opencli).exists():
            return opencli, [opencli], source, raw
        npx_names = ["npx.cmd", "npx.exe", "npx"] if key == "windows" else ["npx"]
        npx = _first_existing_command(npx_names, environ=environ)
        if Path(npx).exists():
            package = str(config.get("opencli_package") or "@jackwener/opencli")
            return opencli, [npx, "-y", package], "npx", raw
        return opencli, [opencli], source, raw
    opencli = resolve_executable(str(candidate), environ=environ)
    return opencli, [opencli], source, raw


def resolve_runtime_config(
    config: dict[str, Any],
    *,
    platform_name: str | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    resolved = copy.deepcopy(config)
    key = platform_key(platform_name)

    opencli_path, opencli_command, opencli_source, configured_opencli_path = resolve_opencli_invocation(
        resolved, key=key, environ=environ
    )
    opencli_session_raw = resolved.get("opencli_session", "fb-competitor")
    opencli_session = (
        "fb-competitor"
        if is_auto(opencli_session_raw)
        else expand_config_value(opencli_session_raw, environ=environ)
    )
    opencli_daemon_port_raw = resolved.get("opencli_daemon_port", "19825")
    opencli_daemon_port = int(opencli_daemon_port_raw or 19825)

    lark_cli_path, lark_cli_source, configured_lark_cli_path = resolve_lark_cli_path(
        resolved, key=key, environ=environ
    )

    resolved["opencli_path"] = opencli_path
    resolved["opencli_command"] = opencli_command
    resolved["opencli_session"] = opencli_session
    resolved["opencli_daemon_port"] = opencli_daemon_port
    resolved["lark_cli_path"] = lark_cli_path
    resolved["runtime"] = {
        "platform": key,
        "platform_system": platform_name or platform.system(),
        "opencli_path": opencli_path,
        "opencli_command": opencli_command,
        "opencli_source": opencli_source,
        "configured_opencli_path": configured_opencli_path,
        "opencli_session": opencli_session,
        "opencli_daemon_port": opencli_daemon_port,
        "lark_cli_path": lark_cli_path,
        "lark_cli_source": lark_cli_source,
        "configured_lark_cli_path": configured_lark_cli_path,
    }
    return resolved
