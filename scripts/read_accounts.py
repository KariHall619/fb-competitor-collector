#!/usr/bin/env python3
"""Read FB competitor/internal account URLs from the configured Feishu sheet."""

from __future__ import annotations

import argparse
import json
from typing import Any

from config_loader import deep_get, load_config
from field_schema import (
    ACCOUNT_NAME_LOOKUP,
    ACCOUNT_TYPE_LOOKUP,
    GENERIC_ACCOUNT_LOOKUP,
    account_column_roles,
    cell_text,
    first_column,
    normalize_account_type,
)
from lark_io import read_source_range


def read_accounts(config: dict[str, Any]) -> list[dict[str, Any]]:
    sheet_id = deep_get(config, "feishu.sheets.accounts", "账号配置")
    result = read_source_range(config, f"{sheet_id}!A1:B200")
    if result.returncode != 0:
        raise RuntimeError(result.stdout or result.stderr)
    payload = json.loads(result.stdout)
    values = (((payload.get("data") or {}).get("valueRange") or {}).get("values") or [])
    if not values:
        return []
    headers = values[0]
    account_roles = account_column_roles(headers)
    generic_account_col = first_column(headers, GENERIC_ACCOUNT_LOOKUP)
    account_type_col = first_column(headers, ACCOUNT_TYPE_LOOKUP)
    account_name_col = first_column(headers, ACCOUNT_NAME_LOOKUP)
    accounts: list[dict[str, Any]] = []
    if account_roles:
        for row in values[1:]:
            for index, account_type in account_roles.items():
                account_url = cell_text(row[index] if len(row) > index else "")
                if not account_url:
                    continue
                accounts.append(
                    {
                        "account_name": cell_text(row[account_name_col]) if account_name_col is not None and len(row) > account_name_col else "",
                        "account_url": account_url,
                        "account_type": account_type,
                        "enabled": True,
                        "note": f"飞书账号配置：{cell_text(headers[index])}",
                    }
                )
        return accounts

    if generic_account_col is not None:
        for row in values[1:]:
            account_url = cell_text(row[generic_account_col] if len(row) > generic_account_col else "")
            if not account_url:
                continue
            account_type = (
                normalize_account_type(row[account_type_col])
                if account_type_col is not None and len(row) > account_type_col
                else "competitor"
            ) or "competitor"
            accounts.append(
                {
                    "account_name": cell_text(row[account_name_col]) if account_name_col is not None and len(row) > account_name_col else "",
                    "account_url": account_url,
                    "account_type": account_type,
                    "enabled": True,
                    "note": "飞书账号配置：通用账号列",
                }
            )
        return accounts

    # Backward compatible fallback for the original two-column account sheet.
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
