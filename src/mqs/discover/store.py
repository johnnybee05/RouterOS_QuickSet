"""The inventory on disk, and what changed since the last look.

A scan is worth little on its own; a scan compared with the one before it is worth a lot.
"This antenna is on 6.48.6" is a fact to look up, but "this antenna was on 7.15.3 last
week" is the thing that gets noticed, and "this antenna had a radio last week" is the
thing that has to be noticed.

The file is JSON so it can be read by anything, and keyed by serial number where the
device gave one. A device that was never logged in to is keyed by MAC and quietly becomes
serial-keyed the first time somebody does log in — `merge_candidates` folds the two
records together rather than leaving a duplicate.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mqs.discover.candidates import Candidate, Confidence
from mqs.discover.classify import classify
from mqs.discover.merge import find_match, merge_candidates

INVENTORY_FILE = "inventory.json"
FORMAT_VERSION = 1


class InventoryError(ValueError):
    """The file is not an inventory this version can read."""


class Inventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: int = FORMAT_VERSION
    saved_at: float = 0.0
    devices: list[Candidate] = Field(default_factory=list)

    def by_key(self) -> dict[str, Candidate]:
        return {d.key: d for d in self.devices}


def load(path: Path) -> Inventory:
    """What was known last time. A file that is not there is an empty inventory, which is
    the right answer for a first run and not an error to report."""
    if not path.is_file():
        return Inventory()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InventoryError(f"{path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT_VERSION:
        raise InventoryError(f"{path}: not an inventory of format {FORMAT_VERSION}")
    try:
        return Inventory.model_validate(data)
    except ValidationError as exc:
        raise InventoryError(f"{path}: {exc}") from exc


def save(path: Path, inventory: Inventory) -> None:
    """Written beside the target and moved into place, so an interrupted save never
    leaves half an inventory where the whole one was."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_text(inventory.model_dump_json(indent=2, exclude_none=True),
                       encoding="utf-8")
    partial.replace(path)


# ------------------------------------------------------------------ what changed


class ChangeKind(StrEnum):
    """Ordered by how much somebody needs to look at it."""

    RADIO_LOST = "radio_lost"  # it had a radio and now it does not
    GONE = "gone"  # known last time, not seen now
    VERSION = "version"  # RouterOS moved
    DOWNGRADE = "downgrade"  # ... and moved backwards, which nobody asked for
    RADIO_NEW = "radio_new"
    NEW = "new"
    IDENTITY = "identity"
    ADDRESS = "address"
    CONFIDENCE = "confidence"  # it can be logged in to now, or no longer can


@dataclass(frozen=True)
class Change:
    kind: ChangeKind
    key: str
    name: str
    detail: str = ""

    @property
    def serious(self) -> bool:
        """Worth stopping over rather than reading later.

        A radio that is gone is the one this whole application is built around noticing,
        and a version that went backwards means something installed itself that nobody
        planned.
        """
        return self.kind in (ChangeKind.RADIO_LOST, ChangeKind.DOWNGRADE)


@dataclass(frozen=True)
class Diff:
    changes: tuple[Change, ...] = ()
    merged: tuple[Candidate, ...] = ()

    @property
    def serious(self) -> tuple[Change, ...]:
        return tuple(c for c in self.changes if c.serious)

    def of_kind(self, kind: ChangeKind) -> tuple[Change, ...]:
        return tuple(c for c in self.changes if c.kind is kind)


def _name(device: Candidate) -> str:
    return device.identity or device.address or device.key


def compare(before: Sequence[Candidate], after: Sequence[Candidate]) -> Diff:
    """What this scan says that the last one did not.

    A device missing from the new scan is reported as gone, but never deleted: MNDP is
    missed as often as it is heard, and a record that disappears because a laptop was on
    the wrong port is worse than a stale one. Dropping it is the engineer's call.
    """
    changes: list[Change] = []
    # Paired the way the merge pairs them — by serial, else by a shared MAC — because a
    # device's key changes the first time somebody logs in to it. Pairing on the key alone
    # would report that login as one device gone and another arrived, every time.
    matched: set[int] = set()
    for device in after:
        previous = find_match(list(before), device)
        if previous is None:
            kind = ChangeKind.RADIO_NEW if device.wireless else ChangeKind.NEW
            # `ros_version`, not the raw string: an announcement carries its build time.
            found = device.ros_version
            changes.append(Change(kind, device.key, _name(device),
                                  str(found) if found else device.confidence.value))
            continue
        matched.add(id(previous))
        changes.extend(_compare_one(previous, device))

    for device in before:
        if id(device) not in matched:
            changes.append(Change(ChangeKind.GONE, device.key, _name(device),
                                  device.address or ", ".join(device.macs[:1])))
    changes.sort(key=lambda c: (not c.serious, c.kind.value, c.name))
    return Diff(tuple(changes), tuple(merge_candidates([*before, *after])))


def _compare_one(before: Candidate, after: Candidate) -> list[Change]:
    key, name = after.key, _name(after)
    changes: list[Change] = []

    # The radio first, because it is the one that means something broke.
    if before.wireless and after.inventory is not None and not after.wireless:
        changes.append(Change(ChangeKind.RADIO_LOST, key, name,
                              f"had {', '.join(before.radios.names)}"))
    elif after.wireless and not before.wireless and before.inventory is not None:
        changes.append(Change(ChangeKind.RADIO_NEW, key, name,
                              ", ".join(after.radios.names)))

    old_version, new_version = before.ros_version, after.ros_version
    if old_version and new_version and old_version != new_version:
        kind = ChangeKind.DOWNGRADE if new_version < old_version else ChangeKind.VERSION
        changes.append(Change(kind, key, name, f"{old_version} -> {new_version}"))

    if before.identity and after.identity and before.identity != after.identity:
        changes.append(Change(ChangeKind.IDENTITY, key, name,
                              f"{before.identity} -> {after.identity}"))

    appeared = [a for a in after.addresses if a not in before.addresses]
    if appeared:
        changes.append(Change(ChangeKind.ADDRESS, key, name, ", ".join(appeared)))

    if before.confidence is not after.confidence:
        changes.append(Change(ChangeKind.CONFIDENCE, key, name,
                              f"{before.confidence.value} -> {after.confidence.value}"))
    return changes


# ------------------------------------------------------------------ exporting


CSV_COLUMNS = ("identity", "address", "mac", "model", "category", "role", "version",
               "architecture", "wireless_stack", "packages", "firmware", "confidence",
               "sources", "found_at", "last_seen")


@dataclass
class Row:
    """One line of the table, the same whether it is going to CSV or to a window."""

    values: dict[str, str] = field(default_factory=dict)

    def __getitem__(self, column: str) -> str:
        return self.values.get(column, "")


def to_rows(devices: Iterable[Candidate],
            classify_with: Callable[[Candidate], str] | None = None) -> list[Row]:
    """The inventory as a table. Every column is a string; nothing is computed twice."""
    rows: list[Row] = []
    for device in devices:
        inventory = device.inventory
        board = inventory.routerboard if inventory else None
        packages = (", ".join(f"{p.name} {p.version}" for p in inventory.packages
                              if p.installed) if inventory else "")
        rows.append(Row({
            "identity": device.identity,
            "address": device.address,
            "mac": device.macs[0] if device.macs else "",
            "model": device.model or device.board,
            "category": (classify_with(device) if classify_with
                         else classify(device).category),
            "role": classify(device).role.value if device.wireless else "",
            "version": device.version,
            "architecture": device.architecture,
            "wireless_stack": inventory.wireless.value if inventory else "",
            "packages": packages,
            "firmware": (f"{board.current_firmware} -> {board.upgrade_firmware}"
                         if board and board.upgrade_firmware != board.current_firmware
                         else (board.current_firmware if board else "")),
            "confidence": device.confidence.value,
            "sources": ", ".join(sorted(s.value for s in device.sources)),
            "found_at": ", ".join(sorted({s.found_at for s in device.sightings
                                          if s.found_at})),
            "last_seen": f"{device.last_seen:.0f}" if device.last_seen else "",
        }))
    return rows


def to_csv(rows: Sequence[Row]) -> str:
    """CSV with a header, semicolon-separated: this is opened in Czech Excel, where a
    comma is a decimal point and a comma-separated file lands in one column."""
    lines = [";".join(CSV_COLUMNS)]
    for row in rows:
        lines.append(";".join(_escape(row[column]) for column in CSV_COLUMNS))
    return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    if any(c in value for c in ';"\n'):
        return '"' + value.replace('"', '""') + '"'
    return value


def updatable(devices: Iterable[Candidate]) -> list[Candidate]:
    return [d for d in devices if d.confidence is Confidence.CONFIRMED]
