"""What the window remembers between runs: its own switches, and the login form.

The switches (show passwords, remember the login) are plain JSON: nothing in them is a
secret. The login form is not — it holds the password and the key passphrase — so it is
kept encrypted with Windows DPAPI under the current Windows user, like the credentials of
the *Hromadný update* page. Where there is no DPAPI it is not kept at all: a password is
never written in clear.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from mqs.fleet.credentials import dpapi_protect, dpapi_unprotect

SETTINGS_NAME = "settings.json"
LOGIN_NAME = "login.bin"
FORMAT = 1

Cipher = Callable[[bytes], bytes]


@dataclass
class WindowSettings:
    show_passwords: bool = False
    remember_login: bool = False


@dataclass
class SavedLogin:
    """The *Připojení* form as it was left."""

    host: str = ""
    username: str = ""
    password: str = field(default="", repr=False)
    key_file: str = ""
    key_passphrase: str = field(default="", repr=False)
    transport: str = ""
    api_port: int = 0
    ssh_port: int = 0
    ca_file: str = ""


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _known(cls: type, data: object) -> dict[str, object]:
    """The fields of ``cls`` that ``data`` has with the right type; the rest is dropped."""
    if not isinstance(data, dict):
        raise ValueError("not an object")
    defaults = cls()
    return {f.name: data[f.name] for f in fields(cls)
            if f.name in data and type(data[f.name]) is type(getattr(defaults, f.name))}


class SettingsStore:
    """Both files, in one directory (normally %LOCALAPPDATA%\\MikrotikQuickSetPlus)."""

    def __init__(self, directory: Path | None, *, protect: Cipher | None = None,
                 unprotect: Cipher | None = None) -> None:
        self.directory = directory
        if protect is None and unprotect is None and sys.platform == "win32":
            protect, unprotect = dpapi_protect, dpapi_unprotect
        self._protect, self._unprotect = protect, unprotect

    @property
    def can_remember_login(self) -> bool:
        return self.directory is not None and self._protect is not None

    def load_settings(self) -> WindowSettings:
        if self.directory is None:
            return WindowSettings()
        try:
            data = json.loads((self.directory / SETTINGS_NAME).read_text(encoding="utf-8"))
            return WindowSettings(**_known(WindowSettings, data))  # type: ignore[arg-type]
        except (OSError, ValueError):
            return WindowSettings()  # none yet, or damaged: start from the defaults

    def save_settings(self, settings: WindowSettings) -> None:
        if self.directory is not None:
            _write(self.directory / SETTINGS_NAME,
                   json.dumps(asdict(settings), indent=2).encode("utf-8"))

    def load_login(self) -> SavedLogin | None:
        """None when there is nothing to restore, or it cannot be read (another Windows
        user's file, a damaged one)."""
        if self.directory is None or self._unprotect is None:
            return None
        path = self.directory / LOGIN_NAME
        try:
            data = json.loads(self._unprotect(path.read_bytes()))
            if data.get("format") != FORMAT:
                return None
            return SavedLogin(**_known(SavedLogin, data.get("login")))  # type: ignore[arg-type]
        except (OSError, ValueError, AttributeError):
            return None

    def save_login(self, login: SavedLogin) -> None:
        if not self.can_remember_login:
            return
        assert self.directory is not None and self._protect is not None
        data = json.dumps({"format": FORMAT, "login": asdict(login)}).encode("utf-8")
        _write(self.directory / LOGIN_NAME, self._protect(data))

    def forget_login(self) -> None:
        if self.directory is not None:
            with contextlib.suppress(FileNotFoundError):
                (self.directory / LOGIN_NAME).unlink()
