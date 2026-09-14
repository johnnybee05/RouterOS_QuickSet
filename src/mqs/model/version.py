"""RouterOS version parsing and comparison."""

from __future__ import annotations

import re
from functools import total_ordering
from typing import Any

from pydantic import BaseModel, ConfigDict, model_serializer, model_validator

# "7.15.3 (stable)", "6.49.10 (long-term)", "7.16beta2 (testing)", "7.1rc5"
_VERSION_RE = re.compile(
    r"^\s*(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?"
    r"(?P<pre>(?:alpha|beta|rc)\d*)?"
    r"(?:\s*\((?P<channel>[\w-]+)\))?\s*$"
)


@total_ordering
class RosVersion(BaseModel):
    """A RouterOS version. Ordering ignores the release channel."""

    model_config = ConfigDict(frozen=True)

    major: int
    minor: int
    patch: int = 0
    prerelease: str = ""
    channel: str = ""

    @model_validator(mode="before")
    @classmethod
    def _accept_string(cls, data: Any) -> Any:
        if isinstance(data, str):
            return cls._parts(data)
        return data

    @model_serializer
    def _serialize(self) -> str:
        return str(self)

    @staticmethod
    def _parts(text: str) -> dict[str, Any]:
        match = _VERSION_RE.match(text)
        if not match:
            raise ValueError(f"unrecognised RouterOS version: {text!r}")
        return {
            "major": int(match["major"]),
            "minor": int(match["minor"]),
            "patch": int(match["patch"] or 0),
            "prerelease": match["pre"] or "",
            "channel": match["channel"] or "",
        }

    @classmethod
    def parse(cls, text: str) -> RosVersion:
        return cls(**cls._parts(text))

    def _key(self) -> tuple[int, int, int, int, str]:
        # A prerelease sorts before the final release of the same number.
        return (self.major, self.minor, self.patch, 0 if self.prerelease else 1, self.prerelease)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, RosVersion):
            return NotImplemented
        return self._key() < other._key()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RosVersion):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    def at_least(self, major: int, minor: int = 0, patch: int = 0) -> bool:
        return (self.major, self.minor, self.patch) >= (major, minor, patch)

    def __str__(self) -> str:
        text = f"{self.major}.{self.minor}"
        if self.patch:
            text += f".{self.patch}"
        return text + self.prerelease
