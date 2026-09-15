"""Per-user application directories (%LOCALAPPDATA%\\MikrotikQuickSetPlus)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

APP_DIR_NAME = "MikrotikQuickSetPlus"


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
