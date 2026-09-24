"""Per-user application directories (%LOCALAPPDATA%\\MikrotikQuickSetPlus), and the
Update directory that travels with the exe."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

APP_DIR_NAME = "MikrotikQuickSetPlus"
UPDATE_DIR_NAME = "Update"


def app_data_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    base = env.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / APP_DIR_NAME


def logs_dir(env: Mapping[str, str] | None = None) -> Path:
    return app_data_dir(env) / "logs"


def profiles_dir(env: Mapping[str, str] | None = None) -> Path:
    return app_data_dir(env) / "profiles"


def backups_dir(env: Mapping[str, str] | None = None) -> Path:
    return app_data_dir(env) / "backups"


def update_dir(executable: str | None = None, frozen: bool | None = None) -> Path:
    """The "Update" directory with RouterOS packages, next to MikrotikQuickSetPlus.exe.

    Run from the sources there is no exe, so it is the one next to where build.ps1 puts
    it (dist\\Update): the packages are then in one place for both.
    """
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if frozen:
        return Path(executable or sys.executable).resolve().parent / UPDATE_DIR_NAME
    return Path(__file__).resolve().parents[2] / "dist" / UPDATE_DIR_NAME
