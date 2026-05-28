#!/usr/bin/env python3
"""Read FB competitor/internal account URLs from the configured Feishu sheet."""

from __future__ import annotations

import argparse
import json
from typing import Any

from config_loader import deep_get, load_config
from lark_io import read_source_range


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            if isinstance(item, dict):
                chunks.append(str(item.get("link") or item.get("text") or ""))
            else:
                chunks.append(cell_text(item))
        return "".join(chunks).strip()
    if isinstance(value, dict):
        return str(value.get("link") or value.get("text") or "").strip()
    return str(value).strip()


def read_accounts(config: dict[str, Any]) -> list[dict[str, Any]]:
    sheet_id = deep_get(config, "feishu.sheets.accounts", "账号配置")
    result = read_source_range(config, f"{sheet_id}!A1:B200")
    if result.returncode != 0:
        raise RuntimeError(result.stdout or result.stderr)
    payload = json.loads(result.stdout)
    values = (((payload.get("data") or {}).get("valueRange") or {}).get("values") or [])
    accounts: list[dict[str, Any]] = []
    for row in values[1:]:
        competitor = cell_text(row[0] if len(row) > 0 else "")
        internal = cell_text(row[1] if len(row) > 1 else "")
        if competitor:
            accounts.append(
                {
                    "account_name": "",
                    "account_url": competitor,
                    "account_type": "competitor",
                    "enabled": True,
                    "note": "飞书账号配置：竞品fb账户",
                }
            )
        if internal:
            accounts.append(
                {
                    "account_name": "",
                    "account_url": internal,
                    "account_type": "internal",
                    "enabled": True,
                    "note": "飞书账号配置：内部FB账户",
                }
            )
    return accounts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    args = parser.parse_args()
    config = load_config(args.config)
    accounts = read_accounts(config)
    print(json.dumps({"count": len(accounts), "accounts": accounts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
