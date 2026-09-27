"""Device detection and inventory (spec 4.2), transport-agnostic.

API replies and SSH `print terse` output differ (booleans, flags, units); the helpers
here normalise both into the pure `DeviceInventory` model.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from mqs.model.inventory import (
    BridgePortState,
    DeviceInventory,
    InterfaceInfo,
    PackageInfo,
    ResourceInfo,
    RouterboardInfo,
    ServiceInfo,
    SwitchChip,
    WirelessStack,
)
from mqs.model.version import RosVersion
from mqs.transport.base import CommandError, Reply, Transport

_TRUE = {"yes", "true"}
_SIZE_RE = re.compile(r"^\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[KMG]iB|B)?\s*$")
_UNITS = {None: 1, "B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}

# /console inspect is used to confirm parameters at runtime; NUTNO OVĚŘIT output format.
FASTTRACK_PROBE_PATH = "ip,firewall,filter,add"


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
            factory_firmware=rb.get("factory-firmware", ""),
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
    wireless = detect_wireless(transport, version)

    on_step("packages")
    packages = [
        PackageInfo(
            name=p["name"], version=p.get("version", ""), disabled=to_bool(p, "disabled", "X"),
            available=to_bool(p, "available", "A"), bundle=p.get("bundle", ""),
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
            factory_software=resource.get("factory-software", ""),
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
        wireless=wireless,
        has_flash_dir=has_flash_dir,
        bridge_ports=bridge_ports,
    )


def detect_wireless(transport: Transport, version: RosVersion) -> WirelessStack:
    """Presence of the wireless menus only; Wi-Fi configuration is out of scope."""
    candidates = (
        [("/interface/wifi/print", WirelessStack.WIFI),
         ("/interface/wireless/print", WirelessStack.WIRELESS)]
        if version.major >= 7
        else [("/interface/wireless/print", WirelessStack.WIRELESS)]
    )
    for path, stack in candidates:
        if _optional(transport, path) is not None:
            return stack
    return WirelessStack.NONE


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
    """Port our management session arrives on (lockout guard), via ARP and bridge hosts."""
    arp = _optional(transport, "/ip/arp/print") or []
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
