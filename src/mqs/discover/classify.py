"""Saying what a device is, in the order the evidence deserves.

The one rule: **capability first, name second.** A device is a wireless device because it
has radios, full stop — not because it is called an SXT. The model name only ever adds a
category to a row that is already wireless, so a board nobody has heard of behaves exactly
like a known one and a known name with no radio is not promoted on the strength of it.

The role comes from the mode the radio actually reports, so a dish configured as an AP is
an AP. The category table is data (`mqs/data/wireless_models.yaml`, user copy wins)
because it is a list of product names, which changes on MikroTik's schedule and not on
this application's.

Pure: a candidate in, a label out.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from mqs.discover.candidates import Candidate
from mqs.model.inventory import WirelessMenu, WirelessRole, WirelessSnapshot

MODELS_FILE = "wireless_models.yaml"


class DeviceKind(StrEnum):
    """What the device is for, as far as anything can tell."""

    PTP = "ptp"  # dish or panel, one link
    SECTOR = "sector"  # sector antenna / base station
    AP = "ap"  # indoor or outdoor access point
    SIXTY = "sixty"  # 60 GHz
    OUTDOOR = "outdoor"  # outdoor box, antenna not built in
    LTE = "lte"
    IOT = "iot"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Category:
    label: str
    kind: DeviceKind = DeviceKind.UNKNOWN


@dataclass(frozen=True)
class ModelTable:
    """Two prefix tables: product names, and the RouterBOARD designations devices report."""

    categories: Mapping[str, Category] = field(default_factory=dict)
    models: Mapping[str, Category] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ModelTable:
        return cls(_read(data.get("categories")), _read(data.get("models")))

    def lookup(self, *names: str) -> Category | None:
        """Longest normalised prefix wins, most specific name first.

        `/system routerboard model` is tried against both tables before `board-name` is
        tried against either: the model is the hardware, and the board name is marketing
        that MikroTik reuses across revisions.
        """
        for name in names:
            found = _longest(self.models, name) or _longest(self.categories, name)
            if found is not None:
                return found
        return None


def _read(data: Any) -> dict[str, Category]:
    entries: dict[str, Category] = {}
    for prefix, value in (data or {}).items():
        entry = value or {}
        try:
            kind = DeviceKind(str(entry.get("kind", "unknown")))
        except ValueError:
            kind = DeviceKind.UNKNOWN
        entries[str(prefix)] = Category(str(entry.get("label") or prefix), kind)
    return entries


def _normalise(text: str) -> str:
    return "".join(c for c in text if c.isalnum()).lower()


def _longest(table: Mapping[str, Category], name: str) -> Category | None:
    key = _normalise(name)
    if not key:
        return None
    best_len, best = 0, None
    for prefix, category in table.items():
        normalised = _normalise(prefix)
        if normalised and key.startswith(normalised) and len(normalised) > best_len:
            best_len, best = len(normalised), category
    return best


def _read_yaml(source: Path | str) -> dict[str, Any]:
    text = (source.read_text(encoding="utf-8") if isinstance(source, Path)
            else files("mqs.data").joinpath(source).read_text(encoding="utf-8"))
    return yaml.safe_load(text) or {}


@cache
def default_models() -> ModelTable:
    return ModelTable.from_mapping(_read_yaml(MODELS_FILE))


def merged_models(override: Path | None, base: ModelTable | None = None) -> ModelTable:
    """The bundled table with a user file laid over it, entry by entry, so adding one
    board does not mean copying the rest."""
    base = base or default_models()
    if override is None or not override.is_file():
        return base
    extra = ModelTable.from_mapping(_read_yaml(override))
    return ModelTable({**base.categories, **extra.categories},
                      {**base.models, **extra.models})


# ------------------------------------------------------------------ the verdict


@dataclass(frozen=True)
class Classification:
    """What a row in the inventory says about a device."""

    wireless: bool = False
    roles: frozenset[WirelessRole] = frozenset()
    category: str = ""  # "SXT Square", or empty for a board nobody listed
    kind: DeviceKind = DeviceKind.UNKNOWN
    sixty_ghz: bool = False
    # The name suggests a wireless product but no radio was found. Either nobody has
    # logged in yet, or the name is misleading — worth showing, never worth acting on.
    name_suggests_wireless: bool = False

    @property
    def role(self) -> WirelessRole:
        """One role for the row. An AP that also has a station radio is an AP: it carries
        other devices, and that is what decides where it goes in an update order."""
        for candidate in (WirelessRole.AP, WirelessRole.STATION, WirelessRole.OTHER):
            if candidate in self.roles:
                return candidate
        return WirelessRole.UNKNOWN

    @property
    def summary(self) -> str:
        if not self.wireless:
            return self.category or ("?" if self.name_suggests_wireless else "-")
        parts = [self.category or "wireless", self.role.value]
        if self.sixty_ghz:
            parts.append("60 GHz")
        return " ".join(parts)


def classify(candidate: Candidate, table: ModelTable | None = None) -> Classification:
    """What this device is. Radios decide; the name only labels."""
    table = table or default_models()
    radios = candidate.radios
    category = table.lookup(candidate.model, candidate.board)
    return Classification(
        wireless=radios.has_radios,
        roles=frozenset(radios.roles) if radios.has_radios else frozenset(),
        category=category.label if category else "",
        kind=category.kind if category else DeviceKind.UNKNOWN,
        sixty_ghz=any(i.menu is WirelessMenu.W60G for i in radios.interfaces),
        name_suggests_wireless=category is not None and not radios.has_radios,
    )


def stations_of(radios: WirelessSnapshot) -> int:
    return sum(radios.registrations.values())
