"""Intermediate representation (IR) of a RouterOS script.

Blocks build dialect-neutral IR, dialect passes transform it, `render` serialises it.
The same IR is used to execute commands one by one over a transport.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import IntEnum

Scalar = str | int | bool
ArgValue = Scalar | Sequence[str]


class SectionKey(IntEnum):
    """Mandatory order of generated blocks."""

    IDENTITY = 1
    INTERFACES = 2
    ADDRESSING = 3
    FIREWALL = 4
    SYSTEM = 5
    SERVICES = 6  # always last: changing the SSH port drops the running session


SECTION_TITLES: dict[SectionKey, str] = {
    SectionKey.IDENTITY: "IDENTITY",
    SectionKey.INTERFACES: "BRIDGE / INTERFACE LISTS",
    SectionKey.ADDRESSING: "IP ADDRESSING / WAN / DHCP",
    SectionKey.FIREWALL: "FIREWALL / NAT",
    SectionKey.SYSTEM: "DNS / NTP / CLOCK / SNMP / DISCOVERY / IPV6 / LOGGING",
    SectionKey.SERVICES: "IP SERVICES",
}


def normalize_path(path: str) -> str:
    """'/ip/firewall/filter' or '/ip firewall filter' -> '/ip firewall filter'."""
    parts = path.replace("/", " ").split()
    if not parts:
        raise ValueError("empty menu path")
    return "/" + " ".join(parts)


def normalize_value(value: ArgValue) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    return ",".join(str(v) for v in value)


def _arg_items(args: Mapping[str, ArgValue] | None, kwargs: Mapping[str, ArgValue]):
    items: list[tuple[str, str]] = []
    for key, value in (args or {}).items():
        items.append((key, normalize_value(value)))
    for key, value in kwargs.items():
        items.append((key.replace("_", "-"), normalize_value(value)))
    return tuple(items)


@dataclass(frozen=True)
class Find:
    """`[ find key=value ... ]` selector, or `[ find <raw> ]` for complex expressions."""

    conditions: tuple[tuple[str, str], ...] = ()
    raw: str | None = None

    @classmethod
    def by(cls, args: Mapping[str, ArgValue] | None = None, **kwargs: ArgValue) -> Find:
        return cls(conditions=_arg_items(args, kwargs))

    def get(self, key: str) -> str | None:
        return next((v for k, v in self.conditions if k == key), None)


Selector = Find | int | str | None  # int = numeric index, str = item name (`set ssh ...`)


@dataclass(frozen=True)
class Command:
    path: str
    verb: str
    args: tuple[tuple[str, str], ...] = ()
    selector: Selector = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", normalize_path(self.path))

    @property
    def api_path(self) -> str:
        return self.path.replace(" ", "/")

    def arg(self, key: str) -> str | None:
        return next((v for k, v in self.args if k == key), None)

    def has_arg(self, key: str) -> bool:
        return any(k == key for k, _ in self.args)

    def with_args(self, args: Mapping[str, ArgValue] | None = None, **kwargs: ArgValue) -> Command:
        """Replace existing args in place, append new ones at the end."""
        updates = dict(_arg_items(args, kwargs))
        merged = [(k, updates.pop(k, v)) for k, v in self.args]
        merged.extend(updates.items())
        return replace(self, args=tuple(merged))

    def without_args(self, *keys: str) -> Command:
        return replace(self, args=tuple((k, v) for k, v in self.args if k not in keys))


def cmd(
    path: str,
    verb: str,
    *,
    selector: Selector = None,
    args: Mapping[str, ArgValue] | None = None,
    **kwargs: ArgValue,
) -> Command:
    """Build a command. Keyword names use '_' for '-': `cmd(p, "set", time_zone_name=...)`."""
    return Command(path=path, verb=verb, args=_arg_items(args, kwargs), selector=selector)


@dataclass(frozen=True)
class Comment:
    text: str


@dataclass(frozen=True)
class RawLine:
    """A line passed through verbatim (from the editable hardening block)."""

    text: str
    path: str | None = None  # menu context the line appeared under

    def __post_init__(self) -> None:
        if self.path is not None:
            object.__setattr__(self, "path", normalize_path(self.path))


Item = Command | Comment | RawLine


@dataclass
class Section:
    key: SectionKey
    items: list[Item] = field(default_factory=list)

    @property
    def title(self) -> str:
        return SECTION_TITLES[self.key]

    def add(self, *items: Item) -> Section:
        self.items.extend(items)
        return self

    def commands(self) -> Iterator[Command]:
        return (i for i in self.items if isinstance(i, Command))


@dataclass
class Script:
    sections: list[Section] = field(default_factory=list)

    def section(self, key: SectionKey) -> Section:
        """Return the section for `key`, creating it in the mandatory order."""
        for existing in self.sections:
            if existing.key == key:
                return existing
        new = Section(key)
        self.sections.append(new)
        self.sections.sort(key=lambda s: s.key)
        return new

    def items(self) -> Iterator[Item]:
        for section in self.sections:
            yield from section.items

    def commands(self) -> Iterator[Command]:
        return (i for i in self.items() if isinstance(i, Command))

    def find(self, path: str, verb: str | None = None, **conditions: str) -> list[Command]:
        """Commands under `path` (and `verb`) whose args include all `conditions`."""
        wanted = normalize_path(path)
        result = []
        for command in self.commands():
            if command.path != wanted or (verb is not None and command.verb != verb):
                continue
            if all(command.arg(k.replace("_", "-")) == v for k, v in conditions.items()):
                result.append(command)
        return result
