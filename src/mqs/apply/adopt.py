"""Applying on top of a running configuration: adopt what is already on the device.

The generated script builds a configuration from nothing, so it is made of `add`s. On a
device that already has those objects (an earlier QuickSet run, defconf) `/import` stops
at the first one: "failure: already have such name". For "Aplikovat na běžící
konfiguraci" the script is therefore rewritten against the device just before it is shown
and sent: an `add` of an object that exists becomes `set [ find <key> ] <the other args>`,
and one with nothing else to set is left out. The rewritten script is what the dry-run
shows, and it needs no console scripting, so it also runs line by line over the API.

The firewall block handles its own rules (they are replaced by their comment). A table
the profile no longer adds to is not in the script at all, so the NAT and MSS clamping
rules of an earlier run would stay; those are removed here when the device has them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from ipaddress import IPv4Interface

from mqs.generator.base import GenerationResult
from mqs.generator.blocks.firewall import MANGLE, NAT, OWN_REGEX, remove_own
from mqs.generator.ir import Command, Comment, Find, Item, Script, Section, SectionKey
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
ROUTE = "/ip route"
# Tables whose rules of an earlier run go once the profile adds nothing to them. The
# filter is not among them: dropping its rules would open the router.
OWN_RULE_TABLES = (NAT, MANGLE)


@dataclass(frozen=True)
class ExistingObjects:
    """Static items already on the device: per menu, the values of its key args."""

    items: Mapping[str, frozenset[tuple[str, ...]]] = field(default_factory=dict)
    bridge_ports: Mapping[str, str] = field(default_factory=dict)  # interface -> bridge
    own_rules: Mapping[str, int] = field(default_factory=dict)  # table -> our rules there
    vlan_filtering: frozenset[str] = frozenset()  # bridges with vlan-filtering on

    def has(self, path: str, key: tuple[str, ...]) -> bool:
        return key in self.items.get(path, frozenset())

    def renamed(self, renames: Mapping[str, str]) -> ExistingObjects:
        """The same objects under the port names the script gives (name now -> new name).

        RouterOS refers to an interface itself, not to its name: after
        `/interface ethernet set ... name=` a bridge port shows the new name (7.20.7), so
        the `find` of an adopted item, which runs after the renames, must use it too.
        """
        if not renames:
            return self
        items = {}
        for path, keys in self.items.items():
            names = ADOPT_KEYS.get(path, ())
            if "interface" not in names:
                items[path] = keys
                continue
            at = names.index("interface")
            items[path] = frozenset((*k[:at], renames.get(k[at], k[at]), *k[at + 1:])
                                    for k in keys)
        bridge_ports = {renames.get(port, port): bridge
                        for port, bridge in self.bridge_ports.items()}
        return replace(self, items=items, bridge_ports=bridge_ports)


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
    vlan_filtering: frozenset[str] = frozenset()
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
        elif path == "/interface bridge":
            vlan_filtering = frozenset(r["name"] for r in static
                                       if "name" in r and to_bool(r, "vlan-filtering"))
    own_rules: dict[str, int] = {}
    for table in OWN_RULE_TABLES:
        try:
            rows = transport.execute(table.replace(" ", "/") + "/print")
        except CommandError:
            continue
        count = sum(1 for r in rows if re.search(OWN_REGEX, r.get("comment", "")))
        if count:
            own_rules[table] = count
    return ExistingObjects(items=items, bridge_ports=bridge_ports, own_rules=own_rules,
                           vlan_filtering=vlan_filtering)


def _adopt_route(command: Command, args: dict[str, str], existing: ExistingObjects,
                 adopted: list[str], issues: list[Issue]) -> Command:
    """A static route to the same destination through another gateway: move it over.

    Adding the new one left both. Two static default routes of the same distance are
    ECMP, and on the hEX S (2026-09-23) the device then sent half of its traffic to the
    old gateway. With several routes to the destination (a failover pair) it is not ours
    to pick one, so they are left alone and the preflight says so.
    """
    destination = _normalise("dst-address", args["dst-address"])
    others = sorted(k for k in existing.items.get(ROUTE, ()) if k[0] == destination)
    if not others:
        return command
    if len(others) > 1:
        issues.append(Issue("apply.route_ambiguous", Severity.WARNING, "", {
            "dst": destination, "gateway": args["gateway"],
            "gateways": ", ".join(gateway for _, gateway in others)}))
        return command
    old_gateway = others[0][1]
    adopted.append(f"{ROUTE} {destination} {old_gateway} → {args['gateway']}")
    # dynamic=no: a DHCP or PPPoE client may hold a route to the same place.
    selector = Find.by({"dst-address": args["dst-address"], "gateway": old_gateway,
                        "dynamic": "no"})
    rest = {k: v for k, v in args.items() if k != "dst-address"}
    return Command(command.path, "set", tuple(rest.items()), selector)


def _adopt(command: Command, existing: ExistingObjects,
           adopted: list[str], issues: list[Issue]) -> Command | None:
    if command.verb != "add" or command.path not in ADOPT_KEYS:
        return command
    args = dict(command.args)
    key = item_key(command.path, args)
    if key is None:
        return command
    if not existing.has(command.path, key):
        if command.path == ROUTE:
            return _adopt_route(command, args, existing, adopted, issues)
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
    if (command.path == "/interface bridge" and "vlan-filtering" not in rest
            and args["name"] in existing.vlan_filtering):
        # The generator writes only what is switched on, so a profile without
        # vlan-filtering left it on the bridge, and on the v6 hEX S (2026-09-25) the
        # MT7621 then offloaded no port. The device has the property, so it can go off.
        rest["vlan-filtering"] = "no"
        issues.append(Issue("apply.vlan_filtering_off", Severity.INFO, "bridge.vlan_filtering",
                            {"bridge": args["name"]}))
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
    existing = existing.renamed(result.renames)
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
    script = Script(sections)
    dropped = _drop_own_rules(result.script, existing, script, issues)
    if not adopted and not dropped:
        return result, issues
    if adopted:
        issues.insert(0, Issue("apply.adopted", Severity.INFO, "",
                               {"count": len(adopted), "items": ", ".join(adopted)}))
    text = render_script(script, header=_header(result.text))
    return replace(result, script=script, text=text), issues


def _drop_own_rules(generated: Script, existing: ExistingObjects, script: Script,
                    issues: list[Issue]) -> bool:
    """Remove our NAT / MSS clamping rules the profile no longer adds (switched off)."""
    dropped = False
    for table in OWN_RULE_TABLES:
        count = existing.own_rules.get(table, 0)
        if not count or generated.find(table, "add"):
            continue  # nothing there, or the script replaces them itself
        script.section(SectionKey.FIREWALL).add(
            Comment("Rules of an earlier QuickSet run the profile no longer adds"),
            remove_own(table),
        )
        issues.append(Issue("apply.own_rules_removed", Severity.INFO, "",
                            {"table": table, "count": count}))
        dropped = True
    return dropped
