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
    # NO = the vendor does not support/recommend bridge HW offload on this chip at all.
    hw_offload: Tri = Tri.YES
    verified: bool = False
    known: bool = True  # False = not in the database, generic rules only

    def vlan_filtering(self, major: int) -> Tri:
        return self.vlan_filtering_v6 if major == 6 else self.vlan_filtering_v7


def _normalize(chip_type: str) -> str:
    """Key used for lookups; ignores case and punctuation.

    MikroTik's own documentation spells the same chip both ways: the CLI sample of
    /interface ethernet switch print shows "Atheros-8327" while the feature table
    writes "Atheros8327". Both must find the same entry.
    """
    return "".join(c for c in chip_type if c.isalnum()).casefold()


def _chip(chip_type: str, values: dict[str, Any]) -> ChipInfo:
    return ChipInfo(
        type=chip_type,
        mstp=Tri.of(values.get("mstp")),
        vlan_filtering_v6=Tri.of(values.get("vlan_filtering_v6")),
        vlan_filtering_v7=Tri.of(values.get("vlan_filtering_v7")),
        hw_offload=Tri.of(values.get("hw_offload", True)),
        verified=bool(values.get("verified", False)),
    )


@dataclass(frozen=True)
class ChipDatabase:
    chips: dict[str, ChipInfo] = field(default_factory=dict)
    families: tuple[tuple[str, dict[str, Any]], ...] = ()

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> ChipDatabase:
        chips = {
            _normalize(name): _chip(name, values)
            for name, values in (data.get("chips") or {}).items()
        }
        families = tuple(
            (_normalize(f["prefix"]), f["values"]) for f in data.get("families") or []
        )
        return cls(chips=chips, families=families)

    def lookup(self, chip_type: str) -> ChipInfo:
        key = _normalize(chip_type)
        if key in self.chips:
            return self.chips[key]
        for prefix, values in self.families:
            if key.startswith(prefix):
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
    """Notes for a board, matched on the longest normalised key prefix.

    /system resource print reports names the documentation does not use verbatim
    ("RB4011iGS+5HacQ2HnD" for "RB4011iGS+"), so an exact lookup misses. Taking the
    longest matching prefix is what keeps RB750Gr3 (MT7621) off the RB750G entry
    (Atheros8316), which a first-match rule would happily return.
    """
    key = _normalize(board_name)
    if not key:
        return []
    best_len, best_notes = 0, []
    for name, entry in _known_devices().items():
        prefix = _normalize(name)
        if prefix and key.startswith(prefix) and len(prefix) > best_len:
            best_len, best_notes = len(prefix), list((entry or {}).get("notes") or [])
    return best_notes
