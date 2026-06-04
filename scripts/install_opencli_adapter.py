#!/usr/bin/env python3
"""Install the project OpenCLI adapter into the real OpenCLI home."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from config_loader import load_config


ROOT = Path(__file__).resolve().parents[1]
ADAPTER_SOURCE = ROOT / "scripts" / "opencli_fb_competitor_posts.js"
ADAPTER_TARGET = Path.home() / ".opencli" / "clis" / "facebook" / "fb-competitor-posts.js"


def opencli_command(config: dict[str, Any]) -> list[str]:
    command = config.get("opencli_command")
    if isinstance(command, list) and command:
        return [str(item) for item in command]
    return [str(config.get("opencli_path") or "opencli")]


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("--force-init", action="store_true", help="Run opencli browser init even when the target exists.")
    args = parser.parse_args()

    config = load_config(args.config)
    command = opencli_command(config)
    session = str(config.get("opencli_session") or "fb-competitor")

    if not ADAPTER_SOURCE.exists():
        print(f"adapter source missing: {ADAPTER_SOURCE}", file=sys.stderr)
        return 1

    if args.force_init or not ADAPTER_TARGET.exists():
        init = run([*command, "browser", session, "init", "facebook/fb-competitor-posts"])
        if init.returncode != 0 and not ADAPTER_TARGET.exists():
            sys.stderr.write(init.stderr or init.stdout or "opencli browser init failed")
            return init.returncode or 1

    ADAPTER_TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ADAPTER_SOURCE, ADAPTER_TARGET)
    os.chmod(ADAPTER_TARGET, ADAPTER_TARGET.stat().st_mode | 0o644)
    print(str(ADAPTER_TARGET))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
