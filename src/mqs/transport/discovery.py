"""Device detection and inventory (spec 4.2), transport-agnostic.

API replies and SSH `print terse` output differ (booleans, flags, units); the helpers
here normalise both into the pure `DeviceInventory` model.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from mqs.model.inventory import (
    BridgePortState,
    DeviceInventory,
    InterfaceInfo,
    PackageInfo,
    ResourceInfo,
    RouterboardInfo,
    ServiceInfo,
    SwitchChip,
    WirelessInterface,
    WirelessMenu,
    WirelessRole,
    WirelessSnapshot,
    WirelessStack,
)
from mqs.model.version import RosVersion
from mqs.transport.base import CommandError, Reply, Transport

_TRUE = {"yes", "true"}
_SIZE_RE = re.compile(r"^\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[KMG]iB|B)?\s*$")
_UNITS = {None: 1, "B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}

# /console inspect is used to confirm parameters at runtime; NUTNO OVĚŘIT output format.
FASTTRACK_PROBE_PATH = "ip,firewall,filter,add"

# The floor of a downgrade, under each name RouterOS has given it: 7.24 renamed
# `factory-firmware` (/system routerboard) to `minimum-firmware` and `factory-software`
# (/system resource) to `minimum-version` — both 6.42.4 on the mmips RB760iGS on 7.24.4,
# where the old names are gone (OVĚŘENO 2026-10-12).
MINIMUM_FIRMWARE_KEYS = ("minimum-firmware", "factory-firmware")
MINIMUM_VERSION_KEYS = ("minimum-version", "factory-software")


def to_bool(item: dict[str, str], key: str, flag: str | None = None) -> bool:
    """Boolean property from an API reply (`yes`/`no`) or a terse line flag letter."""
    if key in item:
        return item[key].lower() in _TRUE
    return bool(flag and flag in item.get("_flags", ""))


def configured_services(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """The services themselves, without the rows 7.x lists among them.

    7.20.7 prints the listeners of other features (resolver, dhcp, ntp, ...) and every
    open connection as dynamic rows of /ip service, the connections under the name of
    their service: `ssh port=122 local=... remote=...`. Read by name, such a row stood in
    for the service it belongs to.
    """
    return [row for row in rows if not to_bool(row, "dynamic", "D")]


def to_optional_bool(item: dict[str, str], key: str, flag: str | None = None) -> bool | None:
    if key in item:
        return item[key].lower() in _TRUE
    if flag and "_flags" in item:
        return flag in item["_flags"]
    return None


def to_bytes(value: str | None) -> int:
    if not value:
        return 0
    match = _SIZE_RE.match(value)
    if not match:
        return 0
    return int(float(match["num"]) * _UNITS[match["unit"]])


def _first(reply: Reply) -> dict[str, str]:
    return reply[0] if reply else {}


def _any_of(item: dict[str, str], keys: Sequence[str]) -> str:
    """The first of ``keys`` the row carries a value for, "" when none."""
    return next((item[key] for key in keys if item.get(key)), "")


def _optional(transport: Transport, command_path: str, /, **args: object) -> Reply | None:
    """None when the menu does not exist on this device (e.g. no switch chip, CHR)."""
    try:
        return transport.execute(command_path, **args)
    except CommandError:
        return None


def collect_inventory(
    transport: Transport, on_step: Callable[[str], None] = lambda step: None
) -> DeviceInventory:
    on_step("resource")
    resource = _first(transport.execute("/system/resource/print"))
    version = RosVersion.parse(resource["version"])

    on_step("identity")
    identity = _first(transport.execute("/system/identity/print")).get("name", "")

    on_step("routerboard")
    rb_reply = _optional(transport, "/system/routerboard/print")
    routerboard = None
    if rb_reply:
        rb = rb_reply[0]
        routerboard = RouterboardInfo(
            routerboard=to_bool(rb, "routerboard"),
            model=rb.get("model", ""),
            serial_number=rb.get("serial-number", ""),
            factory_firmware=_any_of(rb, MINIMUM_FIRMWARE_KEYS),
            current_firmware=rb.get("current-firmware", ""),
            upgrade_firmware=rb.get("upgrade-firmware", ""),
        )

    on_step("interfaces")
    interfaces = transport.execute("/interface/print")
    ethernet = {e["name"]: e for e in transport.execute("/interface/ethernet/print")}

    on_step("switch")
    switches = _optional(transport, "/interface/ethernet/switch/print") or []
    switch_ports = _optional(transport, "/interface/ethernet/switch/port/print") or []
    port_switch = {p["name"]: p["switch"] for p in switch_ports if "name" in p and "switch" in p}

    on_step("wireless")
    radios = collect_radios(transport, version)

    on_step("packages")
    packages = [
        PackageInfo(
            name=p["name"], version=p.get("version", ""), disabled=to_bool(p, "disabled", "X"),
            available=to_bool(p, "available", "A"), bundle=p.get("bundle", ""),
            scheduled=p.get("scheduled", ""),
        )
        for p in transport.execute("/system/package/print")
    ]

    on_step("services")
    services = [
        ServiceInfo(
            name=s["name"],
            port=int(s.get("port", "0") or 0),
            disabled=to_bool(s, "disabled", "X"),
            address=s.get("address", ""),
            certificate="" if s.get("certificate") in (None, "none") else s["certificate"],
        )
        for s in configured_services(transport.execute("/ip/service/print"))
    ]
    certificates = [c["name"] for c in (_optional(transport, "/certificate/print") or [])
                    if "name" in c]

    on_step("files")
    files = _optional(transport, "/file/print") or []
    has_flash_dir = any(f.get("name") == "flash" and f.get("type") in ("disk", "directory")
                        for f in files)

    on_step("bridge")
    bridge_ports = [
        BridgePortState(
            interface=p.get("interface", ""),
            bridge=p.get("bridge", ""),
            hw=to_optional_bool(p, "hw"),
            hw_offload=to_optional_bool(p, "hw-offload", "H"),
            disabled=to_bool(p, "disabled", "X"),
        )
        for p in (_optional(transport, "/interface/bridge/port/print", detail=True) or [])
    ]

    return DeviceInventory(
        resource=ResourceInfo(
            version=version,
            board_name=resource.get("board-name", ""),
            architecture=resource.get("architecture-name", ""),
            uptime=resource.get("uptime", ""),
            factory_software=_any_of(resource, MINIMUM_VERSION_KEYS),
            free_memory=to_bytes(resource.get("free-memory")),
            total_memory=to_bytes(resource.get("total-memory")),
            free_hdd_space=to_bytes(resource.get("free-hdd-space")),
            cpu=resource.get("cpu", ""),
        ),
        identity=identity,
        routerboard=routerboard,
        interfaces=[
            InterfaceInfo(
                name=i["name"],
                type=i.get("type", ""),
                default_name=i.get("default-name") or ethernet.get(i["name"], {}).get(
                    "default-name", ""),
                mac_address=i.get("mac-address", ""),
                running=to_bool(i, "running", "R"),
                disabled=to_bool(i, "disabled", "X"),
                switch=port_switch.get(i["name"]),
                comment=i.get("comment", ""),
            )
            for i in interfaces
            if "name" in i
        ],
        switches=[SwitchChip(name=s["name"], type=s.get("type", "")) for s in switches
                  if "name" in s],
        packages=packages,
        services=services,
        certificates=certificates,
        wireless=stack_of(radios),
        radios=radios,
        has_flash_dir=has_flash_dir,
        bridge_ports=bridge_ports,
    )


# The menus a radio can live under, and where to find them. `/interface wifi` is v7 only;
# the other two answer on both major versions when their package is installed.
RADIO_MENUS: tuple[tuple[WirelessMenu, str, int], ...] = (
    (WirelessMenu.WIRELESS, "/interface/wireless/print", 6),
    (WirelessMenu.WIFI, "/interface/wifi/print", 7),
    (WirelessMenu.W60G, "/interface/w60g/print", 6),
)
# `/interface wireless` and `/interface w60g` call it `mode`. On the new driver the mode
# lives in the configuration attached to the interface and comes back dotted
# (`configuration.mode=ap`); which spelling the API uses is NUTNO OVĚŘIT, so every
# plausible one is read and the first that answers wins. A mode that is not found leaves
# the role UNKNOWN, which is reported rather than treated as a station.
MODE_KEYS = ("mode", "configuration.mode", "configuration-mode")
AP_MODES = {"ap-bridge", "ap", "bridge", "wds-slave"}
# Where the stations show up, per menu. 60 GHz has no registration table: a w60g link has
# exactly one peer and names it on the interface itself (`remote-address`).
REGISTRATION_MENUS: dict[WirelessMenu, str] = {
    WirelessMenu.WIRELESS: "/interface/wireless/registration-table/print",
    WirelessMenu.WIFI: "/interface/wifi/registration-table/print",
}


def _mode(item: dict[str, str]) -> str:
    return next((item[key] for key in MODE_KEYS if item.get(key)), "")


def role_of(mode: str) -> WirelessRole:
    """What a radio does, from the mode it reports — never from the model name.

    An unknown mode is OTHER rather than a guess: the role decides whether a registration
    table has to fill up again after a reboot, and inventing an answer there either waits
    for stations that were never going to come or fails to notice that they did not.
    """
    if not mode:
        return WirelessRole.UNKNOWN
    if mode.startswith("station"):
        return WirelessRole.STATION
    return WirelessRole.AP if mode in AP_MODES else WirelessRole.OTHER


def collect_radios(transport: Transport, version: RosVersion) -> WirelessSnapshot:
    """Every radio the device has, and how many stations are on each.

    This is what an update is checked against afterwards: same menus, same interfaces,
    same ones running. It is also what says whether the device is wireless at all — the
    package list cannot, because a v6 bundle ships `wireless` to every board, radio or no
    radio.

    A menu that does not exist and one that is empty are different answers and are kept
    apart: the first says the driver is not installed, the second that it is and has
    nothing configured. Neither is an error, and nothing here raises on a device that
    lacks any of it.
    """
    menus: list[WirelessMenu] = []
    radios: list[WirelessInterface] = []
    for menu, path, since_major in RADIO_MENUS:
        if version.major < since_major:
            continue
        reply = _optional(transport, path)
        if reply is None:
            continue
        menus.append(menu)
        for item in reply:
            if "name" not in item:
                continue
            mode = _mode(item)
            radios.append(WirelessInterface(
                name=item["name"],
                menu=menu,
                mode=mode,
                role=role_of(mode),
                running=to_bool(item, "running", "R"),
                disabled=to_bool(item, "disabled", "X"),
                mac_address=item.get("mac-address", ""),
                band=item.get("band", ""),
                ssid=item.get("ssid") or item.get("configuration.ssid", ""),
                remote_address=item.get("remote-address", ""),
            ))
    return WirelessSnapshot(collected=True, menus=menus, interfaces=radios,
                            registrations=_registrations(transport, menus, radios))


def _registrations(transport: Transport, menus: Sequence[WirelessMenu],
                   radios: Sequence[WirelessInterface]) -> dict[str, int]:
    """Stations per interface. Only asked for where there is an interface to ask about."""
    counts: dict[str, int] = {}
    for menu in menus:
        path = REGISTRATION_MENUS.get(menu)
        if path is None or not any(r.menu == menu for r in radios):
            continue
        for row in _optional(transport, path) or []:
            name = row.get("interface", "")
            if name:
                counts[name] = counts.get(name, 0) + 1
    # A 60 GHz link has one peer and no table; a filled `remote-address` is that peer.
    for radio in radios:
        if radio.menu == WirelessMenu.W60G and radio.remote_address:
            counts[radio.name] = 1
    return counts


def stack_of(snapshot: WirelessSnapshot) -> WirelessStack:
    """The coarse answer the window shows. `/interface wifi` wins when both are there,
    which is only about which label to print — the update copies the whole set."""
    if WirelessMenu.WIFI in snapshot.menus:
        return WirelessStack.WIFI
    if WirelessMenu.WIRELESS in snapshot.menus or WirelessMenu.W60G in snapshot.menus:
        return WirelessStack.WIRELESS
    return WirelessStack.NONE


def detect_wireless(transport: Transport, version: RosVersion) -> WirelessStack:
    """Which menu the device answers on. One code path with `collect_radios`, so the label
    in the window and the set the update uploads can never disagree."""
    return stack_of(collect_radios(transport, version))


def probe_features(transport: Transport, version: RosVersion) -> dict[str, bool]:
    """Runtime confirmation of parameters the feature table marks `runtime_probe`.

    Anything that cannot be confirmed is reported as False, so the generator omits it.
    """
    probes = {"fasttrack_hw_offload": False}
    if version.major < 7:
        return probes
    reply = _optional(transport, "/console/inspect", request="child", path=FASTTRACK_PROBE_PATH)
    if reply:
        probes["fasttrack_hw_offload"] = any(r.get("name") == "hw-offload" for r in reply)
    return probes


def session_interface(transport: Transport, client_ip: str) -> str | None:
    """Port our management session arrives on (lockout guard), via ARP and bridge hosts.

    Over IPv6 (a link-local session) the neighbor table stands in for ARP; it lists
    address, interface and mac-address the same way (7.24.4, 2026-10-12).
    """
    table = "/ipv6/neighbor/print" if ":" in client_ip else "/ip/arp/print"
    arp = _optional(transport, table) or []
    entry = next((a for a in arp if a.get("address") == client_ip), None)
    if entry is None:
        return None
    interface = entry.get("interface")
    mac = entry.get("mac-address")
    hosts = _optional(transport, "/interface/bridge/host/print") or []
    host = next((h for h in hosts if mac and h.get("mac-address") == mac
                 and h.get("bridge") == interface), None)
    if host is not None:
        return host.get("on-interface") or host.get("interface") or interface
    return interface
