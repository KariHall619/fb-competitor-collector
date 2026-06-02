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
from field_schema import output_field_for_header


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


def require_user_identity(config: dict[str, Any]) -> dict[str, Any]:
    result = run_lark(config, ["auth", "status"])
    if result.returncode != 0:
        raise RuntimeError(
            "飞书 CLI 用户身份检查失败，已拒绝写入。"
            f"stdout={result.stdout.strip()} stderr={result.stderr.strip()}"
        )
    payload = parse_lark_output(result)
    identity = payload.get("identity")
    token_status = payload.get("tokenStatus")
    if identity != "user" or token_status != "valid":
        raise RuntimeError(
            "飞书 CLI 必须以有效用户身份写入。"
            f" 当前 identity={identity!r}, tokenStatus={token_status!r}。"
            " 请先运行 lark-cli auth login，并确认 lark-cli auth status 为 identity=user、tokenStatus=valid。"
        )
    return payload


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


def merge_upsert_row(existing: list[Any], incoming: list[Any], headers: list[str]) -> list[Any]:
    width = max(len(headers), len(existing), len(incoming))
    merged = list(existing) + [""] * (width - len(existing))
    incoming_full = list(incoming) + [""] * (width - len(incoming))
    for index in range(width):
        field = output_field_for_header(headers[index]) if index < len(headers) else ""
        current = merged[index]
        new_value = incoming_full[index]
        if field == "adoption_status" and current and not is_system_audit_marker(current):
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
    for index, row in enumerate(existing_rows):
        if len(row) > key_index and row[key_index]:
            by_key[str(row[key_index])] = index
    updated = 0
    inserted = 0
    merged_rows = [list(row) for row in existing_rows]
    for row in rows:
        key = str(row[key_index]) if len(row) > key_index and row[key_index] else ""
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
    result = write_range(config, f"{sheet_ref}!A1:{end_col}{len(values)}", values)
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "sheet": sheet,
        "rows": len(rows),
        "updated": updated,
        "inserted": inserted,
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
