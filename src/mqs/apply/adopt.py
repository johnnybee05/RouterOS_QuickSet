"""Applying on top of a running configuration: adopt what is already on the device.

The generated script builds a configuration from nothing, so it is made of `add`s. On a
device that already has those objects (an earlier QuickSet run, defconf) `/import` stops
at the first one: "failure: already have such name". For "Aplikovat na běžící
konfiguraci" the script is therefore rewritten against the device just before it is shown
and sent: an `add` of an object that exists becomes `set [ find <key> ] <the other args>`,
and one with nothing else to set is left out. The rewritten script is what the dry-run
shows, and it needs no console scripting, so it also runs line by line over the API.

The firewall block handles its own rules (they are replaced by their comment).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from ipaddress import IPv4Interface

from mqs.generator.base import GenerationResult
from mqs.generator.ir import Command, Find, Item, Script, Section
from mqs.generator.render import render_script
from mqs.issues import Issue, Severity
from mqs.transport.base import CommandError, Transport
from mqs.transport.discovery import to_bool

# Menu -> the args that identify an item in it. RouterOS refuses a second item with the
# same name, and a second list member, bridge port, address or NTP server with the same
# key is either refused or a duplicate nobody wants.
ADOPT_KEYS: dict[str, tuple[str, ...]] = {
    "/interface bridge": ("name",),
    "/interface bridge port": ("interface",),
    "/interface list": ("name",),
    "/interface list member": ("list", "interface"),
    "/interface pppoe-client": ("name",),
    "/ip address": ("address",),
    "/ip route": ("dst-address", "gateway"),
    "/ip pool": ("name",),
    "/ip dhcp-server": ("name",),
    "/ip dhcp-server network": ("address",),
    "/ip dhcp-client": ("interface",),
    "/snmp community": ("name",),
    "/system ntp client servers": ("address",),
}
_ADDRESS_KEYS = {"address", "dst-address"}


@dataclass(frozen=True)
class ExistingObjects:
    """Static items already on the device: per menu, the values of its key args."""

    items: Mapping[str, frozenset[tuple[str, ...]]] = field(default_factory=dict)
    bridge_ports: Mapping[str, str] = field(default_factory=dict)  # interface -> bridge

    def has(self, path: str, key: tuple[str, ...]) -> bool:
        return key in self.items.get(path, frozenset())


def _normalise(name: str, value: str) -> str:
    if name in _ADDRESS_KEYS:
        try:
            return str(IPv4Interface(value))
        except ValueError:
            return value
    return value


def item_key(path: str, values: Mapping[str, str]) -> tuple[str, ...] | None:
    keys = ADOPT_KEYS[path]
    if not all(values.get(k) for k in keys):
        return None
    return tuple(_normalise(k, values[k]) for k in keys)


def collect_existing(transport: Transport) -> ExistingObjects:
    """What the script could collide with. Dynamic items (a DHCP address, the list members
    RouterOS adds itself) cannot be changed and are left out; a menu the device does not
    have is simply empty."""
    items: dict[str, frozenset[tuple[str, ...]]] = {}
    bridge_ports: dict[str, str] = {}
    for path in ADOPT_KEYS:
        try:
            rows = transport.execute(path.replace(" ", "/") + "/print")
        except CommandError:
            continue
        static = [r for r in rows if not to_bool(r, "dynamic", "D")]
        items[path] = frozenset(k for r in static if (k := item_key(path, r)) is not None)
        if path == "/interface bridge port":
            bridge_ports = {r["interface"]: r.get("bridge", "") for r in static
                            if "interface" in r}
    return ExistingObjects(items=items, bridge_ports=bridge_ports)


def _adopt(command: Command, existing: ExistingObjects,
           adopted: list[str], issues: list[Issue]) -> Command | None:
    if command.verb != "add" or command.path not in ADOPT_KEYS:
        return command
    args = dict(command.args)
    key = item_key(command.path, args)
    if key is None or not existing.has(command.path, key):
        return command
    names = ADOPT_KEYS[command.path]
    adopted.append(f"{command.path} {' '.join(key)}")
    if command.path == "/interface bridge port":
        interface, bridge = args["interface"], args.get("bridge", "")
        current = existing.bridge_ports.get(interface, "")
        if bridge and current and current != bridge:
            issues.append(Issue("apply.bridge_port_moved", Severity.WARNING, "bridge.ports",
                                {"port": interface, "old": current, "new": bridge}))
    rest = {k: v for k, v in args.items() if k not in names}
    if not rest:
        return None
    # The key as the script wrote it: that is what the console's find compares with.
    selector = Find.by({k: args[k] for k in names})
    return Command(command.path, "set", tuple(rest.items()), selector)


def _header(text: str) -> str | None:
    lines = []
    for line in text.splitlines():
        if line.startswith("# ---- ") or not line.startswith("#"):
            break
        lines.append(line[2:])
    return "\n".join(lines) or None


def adopt_existing(
    result: GenerationResult, existing: ExistingObjects | None
) -> tuple[GenerationResult, list[Issue]]:
    """``result`` rewritten to change the objects the device already has, not add them."""
    if existing is None:
        return result, []
    adopted: list[str] = []
    issues: list[Issue] = []
    sections = []
    for section in result.script.sections:
        items: list[Item] = []
        for item in section.items:
            if isinstance(item, Command):
                kept = _adopt(item, existing, adopted, issues)
                if kept is not None:
                    items.append(kept)
            else:
                items.append(item)
        sections.append(Section(section.key, items))
    if not adopted:
        return result, issues
    issues.insert(0, Issue("apply.adopted", Severity.INFO, "",
                           {"count": len(adopted), "items": ", ".join(adopted)}))
    script = Script(sections)
    text = render_script(script, header=_header(result.text))
    return replace(result, script=script, text=text), issues
