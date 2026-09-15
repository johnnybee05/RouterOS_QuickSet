"""Named JSON profiles (spec 4.7). Passwords are never written."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from mqs import __version__
from mqs.model.profile import DeviceProfile

FORMAT = "mqs-profile"
FORMAT_VERSION = 1
SUFFIX = ".json"
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


class ProfileError(ValueError):
    """`code` is a message code for the UI; `str()` carries technical detail."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def safe_filename(name: str) -> str:
    cleaned = _INVALID_CHARS.sub("_", name).strip(" .") or "profil"
    if cleaned.split(".")[0].upper() in _RESERVED:
        cleaned = "_" + cleaned
    return cleaned[:120]


def profile_to_json(profile: DeviceProfile, now: datetime | None = None) -> str:
    envelope = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "app_version": __version__,
        "saved_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        # SecretStr fields are declared with exclude=True, so passwords never appear here.
        "profile": profile.model_dump(mode="json"),
    }
    return json.dumps(envelope, ensure_ascii=False, indent=2) + "\n"


def profile_from_json(text: str) -> DeviceProfile:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProfileError("profile.invalid_json", str(exc)) from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT or "profile" not in data:
        raise ProfileError("profile.wrong_format")
    if data.get("format_version") != FORMAT_VERSION:
        raise ProfileError("profile.unsupported_version", str(data.get("format_version")))
    try:
        return DeviceProfile.model_validate(data["profile"])
    except ValidationError as exc:
        raise ProfileError("profile.invalid", str(exc)) from exc


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


def export_profile(profile: DeviceProfile, path: Path) -> None:
    _write_atomic(path, profile_to_json(profile))


def import_profile(path: Path) -> DeviceProfile:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProfileError("profile.unreadable", str(exc)) from exc
    return profile_from_json(text)


class ProfileStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path_for(self, name: str) -> Path:
        return self.directory / (safe_filename(name) + SUFFIX)

    def list(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        return sorted((p.stem for p in self.directory.glob(f"*{SUFFIX}")), key=str.lower)

    def save(self, profile: DeviceProfile, name: str | None = None) -> Path:
        name = name or profile.name
        if not name.strip():
            raise ProfileError("profile.name_required")
        stored = profile.model_copy(update={"name": name})
        path = self.path_for(name)
        _write_atomic(path, profile_to_json(stored))
        return path

    def load(self, name: str) -> DeviceProfile:
        path = self.path_for(name)
        if not path.is_file():
            raise ProfileError("profile.not_found", name)
        return import_profile(path)

    def delete(self, name: str) -> None:
        self.path_for(name).unlink(missing_ok=True)
