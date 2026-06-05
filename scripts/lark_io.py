#!/usr/bin/env python3
"""Small lark-cli wrapper for Feishu sheet reads/writes."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from config_loader import deep_get, load_config
from field_audit import is_system_audit_marker
from field_schema import cell_text, output_field_for_header
from models import facebook_content_key


def run_lark(config: dict[str, Any], args: list[str], *, input_json: Any = None) -> subprocess.CompletedProcess[str]:
    cli = config.get("lark_cli_path") or "lark-cli"
    command = [cli, *args]
    return subprocess.run(
        command,
        input=json.dumps(input_json, ensure_ascii=False) if input_json is not None else None,
        text=True,
        capture_output=True,
        check=False,
    )


def require_sheet_url(config: dict[str, Any]) -> str:
    url = deep_get(config, "feishu.output_spreadsheet_url", "") or deep_get(config, "feishu.spreadsheet_url", "")
    if not url:
        raise ValueError("feishu.output_spreadsheet_url is not configured")
    return url


def require_source_sheet_url(config: dict[str, Any]) -> str:
    url = deep_get(config, "feishu.source_spreadsheet_url", "") or deep_get(config, "feishu.spreadsheet_url", "")
    if not url:
        raise ValueError("feishu.source_spreadsheet_url is not configured")
    return url


def sheet_name(config: dict[str, Any], key: str) -> str:
    return deep_get(config, f"feishu.sheets.{key}", key)


def parse_lark_output(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    if not result.stdout.strip():
        return {}
    return json.loads(result.stdout)


def _short_text(value: Any, limit: int = 2000) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def _result_summary(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    payload: Any = None
    stdout = result.stdout.strip()
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = _short_text(stdout)
    return {
        "returncode": result.returncode,
        "stdout": payload if payload is not None else "",
        "stderr": _short_text(result.stderr),
    }


def _cli_config_value(result: subprocess.CompletedProcess[str]) -> str:
    text = result.stdout.strip()
    if ":" not in text:
        return text.strip()
    return text.split(":", 1)[1].split("(", 1)[0].strip()


def _auth_status(config: dict[str, Any]) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    result = run_lark(config, ["auth", "status"])
    if result.returncode != 0:
        raise RuntimeError(
            "飞书 CLI 用户身份检查失败，已拒绝写入。"
            f"stdout={result.stdout.strip()} stderr={result.stderr.strip()}"
        )
    try:
        payload = parse_lark_output(result)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "飞书 CLI 用户身份检查返回了无法解析的 JSON，已拒绝写入。"
            f"stdout={result.stdout.strip()} stderr={result.stderr.strip()}"
        ) from exc
    return result, payload


def _ensure_user_cli_config(config: dict[str, Any], steps: list[dict[str, Any]]) -> None:
    for name in ("default-as", "strict-mode"):
        current = run_lark(config, ["config", name])
        steps.append({"step": f"check_config_{name}", **_result_summary(current)})
        if current.returncode != 0:
            raise RuntimeError(
                f"飞书 CLI 配置检查失败：config {name}。"
                f"stdout={current.stdout.strip()} stderr={current.stderr.strip()}"
            )
        if _cli_config_value(current) == "user":
            continue
        fixed = run_lark(config, ["config", name, "user"])
        steps.append({"step": f"set_config_{name}_user", **_result_summary(fixed)})
        if fixed.returncode != 0:
            raise RuntimeError(
                f"飞书 CLI 配置自动修正失败：config {name} user。"
                f"stdout={fixed.stdout.strip()} stderr={fixed.stderr.strip()}"
            )


def _start_device_login(config: dict[str, Any], status_payload: dict[str, Any]) -> dict[str, Any]:
    scope = str(status_payload.get("scope") or "").strip()
    args = ["auth", "login", "--json", "--no-wait"]
    if scope:
        args.extend(["--scope", scope])
    else:
        args.extend(["--domain", "sheets,drive,wiki"])
    result = run_lark(config, args)
    summary = {"step": "start_device_login", **_result_summary(result)}
    if result.returncode != 0:
        raise RuntimeError(
            "飞书 CLI 用户登录已尝试自动发起，但启动失败。"
            f"stdout={result.stdout.strip()} stderr={result.stderr.strip()}"
        )
    return summary


def ensure_user_identity(config: dict[str, Any], *, allow_device_login: bool = True) -> dict[str, Any]:
    """Ensure lark-cli can write as a valid user, auto-refreshing when possible."""
    steps: list[dict[str, Any]] = []
    recovery = {
        "attempted": False,
        "ok": False,
        "steps": steps,
    }
    try:
        _ensure_user_cli_config(config, steps)
        status_result, payload = _auth_status(config)
        steps.append({"step": "auth_status", **_result_summary(status_result)})
    except RuntimeError as exc:
        recovery["error"] = str(exc)
        raise RuntimeError(f"{exc} 已在写入前停止，未执行飞书写入。") from exc

    identity = payload.get("identity")
    token_status = payload.get("tokenStatus")

    if identity == "user" and token_status == "valid":
        recovery["ok"] = True
        return {**payload, "_auth_recovery": recovery}

    if identity and identity != "user":
        raise RuntimeError(
            "飞书 CLI 必须以有效用户身份写入。"
            f" 当前 identity={identity!r}, tokenStatus={token_status!r}。"
            " 已在写入前停止，未执行飞书写入。请确认 lark-cli 当前配置使用用户身份。"
        )

    if identity == "user" and token_status == "needs_refresh":
        recovery["attempted"] = True
        doctor = run_lark(config, ["doctor"])
        steps.append({"step": "doctor_refresh_probe", **_result_summary(doctor)})
        status_result, refreshed = _auth_status(config)
        steps.append({"step": "auth_status_after_refresh_probe", **_result_summary(status_result)})
        if refreshed.get("identity") == "user" and refreshed.get("tokenStatus") == "valid":
            recovery["ok"] = True
            return {**refreshed, "_auth_recovery": recovery}
        payload = refreshed
        identity = payload.get("identity")
        token_status = payload.get("tokenStatus")

    if allow_device_login:
        recovery["attempted"] = True
        try:
            login_step = _start_device_login(config, payload)
            steps.append(login_step)
        except RuntimeError as exc:
            recovery["error"] = str(exc)
            raise RuntimeError(
                "飞书 CLI 用户身份自动恢复失败，已在写入前停止，未执行飞书写入。"
                f" 当前 identity={identity!r}, tokenStatus={token_status!r}。{exc}"
            ) from exc
        raise RuntimeError(
            "飞书 CLI token 不能自动静默恢复，已自动发起设备登录，"
            "但仍需要用户在浏览器完成授权；授权完成后重试当前命令。"
            f" device_login={json.dumps(login_step, ensure_ascii=False)}"
        )

    raise RuntimeError(
        "飞书 CLI 必须以有效用户身份写入。"
        f" 当前 identity={identity!r}, tokenStatus={token_status!r}。"
        " 自动刷新后仍未恢复，已在写入前停止，未执行飞书写入。"
    )


def require_user_identity(config: dict[str, Any]) -> dict[str, Any]:
    return ensure_user_identity(config)


def read_range(config: dict[str, Any], range_expr: str) -> subprocess.CompletedProcess[str]:
    return run_lark(config, ["sheets", "+read", "--url", require_sheet_url(config), "--range", range_expr])


def read_source_range(config: dict[str, Any], range_expr: str) -> subprocess.CompletedProcess[str]:
    return run_lark(config, ["sheets", "+read", "--url", require_source_sheet_url(config), "--range", range_expr])


def ensure_sheet(config: dict[str, Any], title: str) -> dict[str, Any]:
    info = run_lark(config, ["sheets", "+info", "--url", require_sheet_url(config)])
    if info.returncode != 0:
        return {"ok": False, "stage": "info", "stdout": info.stdout, "stderr": info.stderr}
    payload = parse_lark_output(info)
    sheets = (((payload.get("data") or {}).get("sheets") or {}).get("sheets") or [])
    for sheet in sheets:
        if sheet.get("title") == title or sheet.get("sheet_id") == title:
            return {"ok": True, "created": False, "sheet": sheet}
    created = run_lark(config, ["sheets", "+create-sheet", "--url", require_sheet_url(config), "--title", title])
    if created.returncode != 0:
        return {
            "ok": False,
            "created": False,
            "stdout": created.stdout,
            "stderr": created.stderr,
        }
    # Re-read metadata because create responses can vary by CLI version.
    refreshed = run_lark(config, ["sheets", "+info", "--url", require_sheet_url(config)])
    if refreshed.returncode != 0:
        return {"ok": False, "stage": "info_after_create", "stdout": refreshed.stdout, "stderr": refreshed.stderr}
    payload = parse_lark_output(refreshed)
    sheets = (((payload.get("data") or {}).get("sheets") or {}).get("sheets") or [])
    for sheet in sheets:
        if sheet.get("title") == title:
            return {"ok": True, "created": True, "sheet": sheet}
    return {"ok": False, "stage": "created_but_not_found", "stdout": created.stdout, "stderr": created.stderr}


def write_range(config: dict[str, Any], range_expr: str, values: list[list[Any]]) -> subprocess.CompletedProcess[str]:
    return run_lark(
        config,
        [
            "sheets",
            "+write",
            "--url",
            require_sheet_url(config),
            "--range",
            range_expr,
            "--values",
            json.dumps(values, ensure_ascii=False),
        ],
    )


def append_range(config: dict[str, Any], range_expr: str, values: list[list[Any]]) -> subprocess.CompletedProcess[str]:
    return run_lark(
        config,
        [
            "sheets",
            "+append",
            "--url",
            require_sheet_url(config),
            "--range",
            range_expr,
            "--values",
            json.dumps(values, ensure_ascii=False),
        ],
    )


def values_from_lark_payload(payload: dict[str, Any]) -> list[list[Any]]:
    data = payload.get("data") if isinstance(payload, dict) else {}
    value_range = (data or {}).get("valueRange") or (data or {}).get("value_range") or {}
    values = value_range.get("values") or (data or {}).get("values") or payload.get("values") or []
    return values if isinstance(values, list) else []


def key_column_index(headers: list[Any], key_field: str) -> int | None:
    for index, header in enumerate(headers):
        if output_field_for_header(header) == key_field:
            return index
    return None


def normalized_upsert_key(value: Any, key_field: str) -> str:
    text = cell_text(value)
    if not text:
        return ""
    if key_field != "post_url":
        return text
    return facebook_content_key(text) or text


def cell_has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def merge_upsert_row(existing: list[Any], incoming: list[Any], headers: list[str]) -> list[Any]:
    width = max(len(headers), len(existing), len(incoming))
    merged = list(existing) + [""] * (width - len(existing))
    incoming_full = list(incoming) + [""] * (width - len(incoming))
    adoption_index = next(
        (index for index, header in enumerate(headers) if output_field_for_header(header) == "adoption_status"),
        None,
    )
    incoming_marker = (
        incoming_full[adoption_index]
        if adoption_index is not None and adoption_index < len(incoming_full)
        else ""
    )
    marker_text = str(incoming_marker or "")

    def incoming_marks_business_field_missing(field: str) -> bool:
        if not is_system_audit_marker(marker_text):
            return False
        if field == "post_type":
            return "帖子类型" in marker_text
        if field == "story_summary":
            return "文章概要" in marker_text or "故事概要" in marker_text
        return False

    for index in range(width):
        field = output_field_for_header(headers[index]) if index < len(headers) else ""
        current = merged[index]
        new_value = incoming_full[index]
        if field == "adoption_status" and current and not is_system_audit_marker(current):
            continue
        if field != "adoption_status" and not cell_has_value(new_value):
            continue
        if (
            field in {"post_type", "story_summary"}
            and cell_has_value(current)
            and incoming_marks_business_field_missing(field)
        ):
            continue
        merged[index] = new_value
    return merged[:width]


def upsert_rows(
    config: dict[str, Any],
    sheet_key: str,
    rows: list[list[Any]],
    *,
    headers: list[str],
    key_field: str = "post_url",
    dry_run: bool = False,
) -> dict[str, Any]:
    sheet = sheet_name(config, sheet_key)
    key_index = key_column_index(headers, key_field)
    if key_index is None:
        return {"ok": False, "stage": "upsert_key", "error": f"header for {key_field} not found", "sheet": sheet}
    if dry_run:
        keys = [row[key_index] for row in rows if len(row) > key_index and row[key_index]]
        return {
            "ok": True,
            "dry_run": True,
            "mode": "upsert",
            "sheet": sheet,
            "rows": len(rows),
            "key_field": key_field,
            "keys": keys[:10],
        }
    if not rows:
        return {"ok": True, "sheet": sheet, "rows": 0, "mode": "upsert"}
    try:
        auth_payload = require_user_identity(config)
    except RuntimeError as exc:
        return {"ok": False, "stage": "auth_status", "error": str(exc), "sheet": sheet, "rows": len(rows)}
    ensured = ensure_sheet(config, sheet)
    if not ensured.get("ok"):
        return ensured
    sheet_ref = ensured["sheet"].get("sheet_id") or sheet
    width = max(len(headers), *(len(row) for row in rows))
    end_col = chr(ord("A") + min(width, 26) - 1)
    read = read_range(config, f"{sheet_ref}!A:{end_col}")
    if read.returncode != 0:
        return {"ok": False, "stage": "read_existing", "stdout": read.stdout, "stderr": read.stderr, "sheet": sheet}
    existing_values = values_from_lark_payload(parse_lark_output(read))
    current_headers = existing_values[0] if existing_values else headers
    if [str(item) for item in current_headers] != [str(item) for item in headers]:
        current_headers = headers
    existing_rows = existing_values[1:] if existing_values else []
    by_key: dict[str, int] = {}
    duplicate_indexes: set[int] = set()
    for index, row in enumerate(existing_rows):
        if len(row) > key_index and row[key_index]:
            key = normalized_upsert_key(row[key_index], key_field)
            if not key:
                continue
            if key in by_key:
                primary_index = by_key[key]
                existing_rows[primary_index] = merge_upsert_row(existing_rows[primary_index], row, headers)
                duplicate_indexes.add(index)
            else:
                by_key[key] = index
    updated = 0
    inserted = 0
    merged_rows = [list(row) for index, row in enumerate(existing_rows) if index not in duplicate_indexes]
    if duplicate_indexes:
        by_key = {}
        for index, row in enumerate(merged_rows):
            if len(row) > key_index and row[key_index]:
                key = normalized_upsert_key(row[key_index], key_field)
                if key:
                    by_key[key] = index
    for row in rows:
        key = normalized_upsert_key(row[key_index], key_field) if len(row) > key_index and row[key_index] else ""
        if key and key in by_key:
            row_index = by_key[key]
            merged_rows[row_index] = merge_upsert_row(merged_rows[row_index], row, headers)
            updated += 1
        else:
            merged_rows.append(row)
            inserted += 1
            if key:
                by_key[key] = len(merged_rows) - 1
    values = [headers] + merged_rows
    write_values = list(values)
    if len(existing_values) > len(values):
        blank_row = [""] * width
        write_values.extend([list(blank_row) for _ in range(len(existing_values) - len(values))])
    result = write_range(config, f"{sheet_ref}!A1:{end_col}{len(write_values)}", write_values)
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "sheet": sheet,
        "rows": len(rows),
        "updated": updated,
        "inserted": inserted,
        "cleared_trailing_rows": max(0, len(existing_values) - len(values)),
        "mode": "upsert",
        "identity": auth_payload.get("identity"),
        "tokenStatus": auth_payload.get("tokenStatus"),
        "userName": auth_payload.get("userName"),
    }


def write_rows(
    config: dict[str, Any],
    sheet_key: str,
    rows: list[list[Any]],
    *,
    headers: list[str] | None = None,
    mode: str = "append",
    dry_run: bool = False,
) -> dict[str, Any]:
    sheet = sheet_name(config, sheet_key)
    values = ([headers] if headers else []) + rows
    if mode == "upsert":
        if not headers:
            return {"ok": False, "stage": "upsert_headers", "error": "headers are required for upsert", "sheet": sheet}
        key_field = str(deep_get(config, "quality_audit.update_existing_rows_by", "post_url") or "post_url")
        return upsert_rows(config, sheet_key, rows, headers=headers, key_field=key_field, dry_run=dry_run)
    if dry_run:
        return {"ok": True, "dry_run": True, "sheet": sheet, "rows": len(rows), "headers": bool(headers)}
    if not values:
        return {"ok": True, "sheet": sheet, "rows": 0}
    try:
        auth_payload = require_user_identity(config)
    except RuntimeError as exc:
        return {"ok": False, "stage": "auth_status", "error": str(exc), "sheet": sheet, "rows": len(rows)}
    width = max(len(row) for row in values)
    end_col = chr(ord("A") + min(width, 26) - 1)
    if mode == "overwrite":
        ensured = ensure_sheet(config, sheet)
        if not ensured.get("ok"):
            return ensured
        sheet_ref = ensured["sheet"].get("sheet_id") or sheet
        result = write_range(config, f"{sheet_ref}!A1:{end_col}{len(values)}", values)
    else:
        ensured = ensure_sheet(config, sheet)
        if not ensured.get("ok"):
            return ensured
        sheet_ref = ensured["sheet"].get("sheet_id") or sheet
        result = append_range(config, f"{sheet_ref}!A:{end_col}", values)
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "sheet": sheet,
        "rows": len(rows),
        "identity": auth_payload.get("identity"),
        "tokenStatus": auth_payload.get("tokenStatus"),
        "userName": auth_payload.get("userName"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--read", default="")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.read:
        result = read_range(config, args.read)
        print(result.stdout or result.stderr)
        return result.returncode
    parser.error("Provide --read for direct CLI usage")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
