#!/usr/bin/env python3
"""Run OpenCLI with project-local adapter sources.

OpenCLI 1.8.x discovers custom adapters from ``$HOME/.opencli/clis``.  This
wrapper keeps the editable adapter source inside this repository, then runs
OpenCLI with HOME pointed at an ignored project runtime directory.
"""

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
PROJECT_CLIS = ROOT / "opencli" / "clis"
PROJECT_OPENCLI_HOME = ROOT / "data" / "opencli-home"


def opencli_command(config: dict[str, Any]) -> list[str]:
    command = config.get("opencli_command")
    if isinstance(command, list) and command:
        return [str(item) for item in command]
    return [str(config.get("opencli_path") or "opencli")]


def sync_project_adapters(home_dir: Path) -> None:
    if not PROJECT_CLIS.exists():
        raise FileNotFoundError(f"Project OpenCLI adapters not found: {PROJECT_CLIS}")
    target = home_dir / ".opencli" / "clis"
    target.mkdir(parents=True, exist_ok=True)
    for site_dir in PROJECT_CLIS.iterdir():
        if not site_dir.is_dir():
            continue
        target_site = target / site_dir.name
        target_site.mkdir(parents=True, exist_ok=True)
        for source_file in site_dir.glob("*.js"):
            shutil.copy2(source_file, target_site / source_file.name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/settings.yaml")
    parser.add_argument("opencli_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    opencli_args = list(args.opencli_args)
    if opencli_args and opencli_args[0] == "--":
        opencli_args = opencli_args[1:]
    if not opencli_args:
        parser.error("missing OpenCLI arguments after --")

    config = load_config(args.config)
    home_dir = PROJECT_OPENCLI_HOME
    sync_project_adapters(home_dir)

    env = dict(os.environ)
    env["HOME"] = str(home_dir)
    env["USERPROFILE"] = str(home_dir)
    env["FB_COLLECTOR_PROJECT_ROOT"] = str(ROOT)
    env.setdefault("OPENCLI_CACHE_DIR", str(home_dir / ".opencli" / "cache"))

    proc = subprocess.run(
        [*opencli_command(config), *opencli_args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
