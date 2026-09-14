"""Switch chip capability database and known-device enrichment (data/*.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache
from importlib.resources import files
from typing import Any

import yaml


class Tri(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"

    @classmethod
    def of(cls, value: Any) -> Tri:
        if value is True:
            return cls.YES
        if value is False:
            return cls.NO
        return cls.UNKNOWN


@dataclass(frozen=True)
class ChipInfo:
    type: str
    mstp: Tri = Tri.UNKNOWN
    vlan_filtering_v6: Tri = Tri.UNKNOWN
    vlan_filtering_v7: Tri = Tri.UNKNOWN
    verified: bool = False
    known: bool = True  # False = not in the database, generic rules only

    def vlan_filtering(self, major: int) -> Tri:
        return self.vlan_filtering_v6 if major == 6 else self.vlan_filtering_v7


def _chip(chip_type: str, values: dict[str, Any]) -> ChipInfo:
    return ChipInfo(
        type=chip_type,
        mstp=Tri.of(values.get("mstp")),
        vlan_filtering_v6=Tri.of(values.get("vlan_filtering_v6")),
        vlan_filtering_v7=Tri.of(values.get("vlan_filtering_v7")),
        verified=bool(values.get("verified", False)),
    )


@dataclass(frozen=True)
class ChipDatabase:
    chips: dict[str, ChipInfo] = field(default_factory=dict)
    families: tuple[tuple[str, dict[str, Any]], ...] = ()

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> ChipDatabase:
        chips = {name: _chip(name, values) for name, values in (data.get("chips") or {}).items()}
        families = tuple((f["prefix"], f["values"]) for f in data.get("families") or [])
        return cls(chips=chips, families=families)

    def lookup(self, chip_type: str) -> ChipInfo:
        if chip_type in self.chips:
            return self.chips[chip_type]
        for prefix, values in self.families:
            if chip_type.startswith(prefix):
                return _chip(chip_type, values)
        return ChipInfo(type=chip_type, known=False)


def _read_yaml(name: str) -> dict[str, Any]:
    text = files("mqs.data").joinpath(name).read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


@cache
def default_chip_database() -> ChipDatabase:
    return ChipDatabase.from_mapping(_read_yaml("switch_chips.yaml"))


@cache
def _known_devices() -> dict[str, Any]:
    return _read_yaml("known_devices.yaml").get("devices") or {}


def device_notes(board_name: str) -> list[str]:
    entry = _known_devices().get(board_name) or {}
    return list(entry.get("notes") or [])
