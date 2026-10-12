"""Device inventory: pure data collected by discovery, consumed by capabilities/generator."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from mqs.model.version import RosVersion


class InterfaceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str  # RouterOS `type`: ether, wlan, wifi, bond, vlan, bridge, pppoe-out, ...
    default_name: str = ""
    mac_address: str = ""
    running: bool = False
    disabled: bool = False
    switch: str | None = None  # switch chip name from /interface ethernet switch port
    comment: str = ""


class SwitchChip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str  # e.g. switch1
    type: str  # e.g. RTL8367, QCA8337, Atheros8327, MT7621, Marvell-98DX3236


class PackageInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str = ""
    disabled: bool = False
    available: bool = False  # offered for download, not on the device (v7 flag "A")
    bundle: str = ""  # v6: the bundle package this one came in, e.g. routeros-mmips
    # `/system package print` `scheduled`: "enable", "disable", "uninstall", "downgrade" —
    # a change the next reboot will make. After an update every package has to be plain
    # installed and nothing scheduled, or the device is one reboot away from somewhere else.
    scheduled: str = ""

    @property
    def installed(self) -> bool:
        """On the device, as opposed to one RouterOS only offers to download.

        7.23 lists every package MikroTik publishes for the board, the ones that are
        not installed with the flags `XA` (`available=true`, `disabled=true`) and no
        version; older versions list the installed packages alone.
        """
        return not self.available and bool(self.version)


class ServiceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    port: int
    disabled: bool = False
    address: str = ""
    certificate: str = ""


class BridgePortState(BaseModel):
    """Actual bridge port state from `/interface bridge port print detail`."""

    model_config = ConfigDict(extra="forbid")

    interface: str
    bridge: str
    hw: bool | None = None  # requested (hw=yes/no)
    hw_offload: bool | None = None  # actually running
    disabled: bool = False


class ResourceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: RosVersion
    board_name: str = ""
    architecture: str = ""
    uptime: str = ""
    # The RouterOS version the board was shipped with, and the floor of any downgrade.
    # `/system resource factory-software` (6.42.4 on the mmips RB760iGS, 7.20 on the arm
    # hEX S E60iUGS, 2026-09-27). The RouterBOOT side of it is `factory-firmware`, which
    # carries the patch number the resource field leaves out on v7. Since 7.24 the field
    # is `minimum-version` (and the RouterBOOT one `minimum-firmware`); discovery reads
    # either name into this one field.
    factory_software: str = ""
    free_memory: int = 0
    total_memory: int = 0
    free_hdd_space: int = 0
    cpu: str = ""


class RouterboardInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routerboard: bool = False
    model: str = ""
    serial_number: str = ""
    # The firmware the board left the factory with: no RouterOS older than this goes on
    # it (6.42.4 on the mmips RB760iGS, 7.20.7 on the arm hEX S, 2026-09-27).
    # `minimum-firmware` since 7.24, `factory-firmware` before it.
    factory_firmware: str = ""
    current_firmware: str = ""
    upgrade_firmware: str = ""


class WirelessStack(StrEnum):
    """Which configuration menu the device answers on — the coarse answer, for the window.

    Which *driver package* carries that menu is `WirelessDriver`, and the two are not the
    same question: `/interface wifi` is served by `wifi-qcom`, `wifi-qcom-ac` or, before
    7.13, `wifiwave2`, and a device can answer on `/interface wireless` and
    `/interface wifi` at once.
    """

    NONE = "none"
    WIRELESS = "wireless"  # /interface wireless (v6, older v7 packages)
    WIFI = "wifi"  # /interface wifi (v7)


class WirelessMenu(StrEnum):
    """The menus a radio can live under; the snapshot keeps them apart by name."""

    WIRELESS = "wireless"  # /interface wireless — legacy a/b/g/n/ac
    WIFI = "wifi"  # /interface wifi — the new driver
    W60G = "w60g"  # /interface w60g — 60 GHz, driven by the `wireless` package


class WirelessDriver(StrEnum):
    """The package that drives the radios. The value is the package name, where there is
    one: `NONE` has no package and v6 carries `WIRELESS` inside its bundle.

    Never changed by an update. Switching between the legacy driver and a `wifi` one wipes
    the interface configuration, so the set a device runs is copied, not chosen.
    """

    NONE = "none"
    WIRELESS = "wireless"  # legacy /interface wireless, and all 60 GHz
    WIFIWAVE2 = "wifiwave2"  # v7 < 7.13, split into the two below by 7.13
    WIFI_QCOM = "wifi-qcom"  # 802.11ax
    WIFI_QCOM_AC = "wifi-qcom-ac"  # 802.11ac Wave2 on the new driver

    @property
    def is_wifi(self) -> bool:
        """One of the `/interface wifi` drivers, as opposed to the legacy one."""
        return self in (WirelessDriver.WIFIWAVE2, WirelessDriver.WIFI_QCOM,
                        WirelessDriver.WIFI_QCOM_AC)


class WirelessRole(StrEnum):
    """What the radio does, read from the interface mode — not from the model name."""

    AP = "ap"  # ap-bridge, ap, bridge (w60g)
    STATION = "station"  # station, station-bridge, station-pseudobridge, station-wds
    OTHER = "other"  # alignment-only, nstreme-dual-slave, a mode not recognised here
    UNKNOWN = "unknown"  # the device did not say


class WirelessInterface(BaseModel):
    """One radio as the device reports it, for the snapshot an update is checked against."""

    model_config = ConfigDict(extra="forbid")

    name: str
    menu: WirelessMenu
    mode: str = ""  # as reported: ap-bridge, station-bridge, bridge, ...
    role: WirelessRole = WirelessRole.UNKNOWN
    running: bool = False
    disabled: bool = False
    mac_address: str = ""
    band: str = ""
    ssid: str = ""
    remote_address: str = ""  # w60g: the MAC of the far end of the link


class WirelessSnapshot(BaseModel):
    """Every radio on the device, taken before an update and compared with after it.

    `menus` is the menus the device *has*, which is not the same as the menus that hold
    something: a device with the `wireless` package and no radio configured still answers
    on `/interface wireless`, and a menu that is missing says the driver is not installed.
    """

    model_config = ConfigDict(extra="forbid")

    # Whether the device was actually asked. False is NOT "no radios" — it is "nobody
    # looked", and an update planned on it would quietly leave a driver package out and
    # take the radio with it. Everything that plans an update refuses on a snapshot that
    # was never taken, which is why this defaults to False rather than True.
    collected: bool = False
    menus: list[WirelessMenu] = Field(default_factory=list)
    interfaces: list[WirelessInterface] = Field(default_factory=list)
    # Interface name -> how many stations were registered on it when the snapshot was taken.
    registrations: dict[str, int] = Field(default_factory=dict)

    @property
    def has_radios(self) -> bool:
        """The one criterion that makes a device a wireless device: it has radios."""
        return bool(self.interfaces)

    @property
    def names(self) -> list[str]:
        return sorted(i.name for i in self.interfaces)

    @property
    def running_names(self) -> list[str]:
        return sorted(i.name for i in self.interfaces if i.running)

    @property
    def roles(self) -> set[WirelessRole]:
        return {i.role for i in self.interfaces}

    @property
    def serves_stations(self) -> bool:
        """An AP: after an update its registration table has to fill up again."""
        return WirelessRole.AP in self.roles

    def in_menu(self, menu: WirelessMenu) -> list[WirelessInterface]:
        return [i for i in self.interfaces if i.menu == menu]


class Ipv6PackageState(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    MISSING = "missing"
    BUILTIN = "builtin"  # v7: part of the routeros package
    UNKNOWN = "unknown"  # offline, nothing detected


class DeviceInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource: ResourceInfo
    identity: str = ""
    routerboard: RouterboardInfo | None = None
    interfaces: list[InterfaceInfo] = Field(default_factory=list)
    switches: list[SwitchChip] = Field(default_factory=list)
    packages: list[PackageInfo] = Field(default_factory=list)
    services: list[ServiceInfo] = Field(default_factory=list)
    certificates: list[str] = Field(default_factory=list)
    wireless: WirelessStack = WirelessStack.NONE
    radios: WirelessSnapshot = Field(default_factory=WirelessSnapshot)
    has_flash_dir: bool = False
    bridge_ports: list[BridgePortState] = Field(default_factory=list)

    @property
    def version(self) -> RosVersion:
        return self.resource.version

    @property
    def port_names(self) -> list[str]:
        """Physical-ish ports selectable as WAN or bridge member."""
        selectable = {"ether", "wlan", "wifi", "bond"}
        return [i.name for i in self.interfaces if i.type in selectable]

    def renamed(self, renames: Mapping[str, str]) -> DeviceInventory:
        """The device as it will be once its interfaces are renamed (name now -> new name)."""
        if not renames:
            return self
        return self.model_copy(update={
            "interfaces": [i.model_copy(update={"name": renames.get(i.name, i.name)})
                           for i in self.interfaces],
            "bridge_ports": [p.model_copy(update={"interface": renames.get(p.interface,
                                                                            p.interface)})
                             for p in self.bridge_ports],
        })

    def interface(self, name: str) -> InterfaceInfo | None:
        return next((i for i in self.interfaces if i.name == name), None)

    def switch_type(self, switch_name: str | None) -> str | None:
        if switch_name is None:
            return None
        chip = next((s for s in self.switches if s.name == switch_name), None)
        return chip.type if chip else None

    @property
    def package_space(self) -> int:
        """Room for a package uploaded to the file root, where a reboot installs it from.

        A board that keeps its files under `flash/` has a RAM disk for a root, and takes
        the package into memory: the flash is written only when the reboot installs it.
        OVĚŘENO on the hEX S 6.49.10 (2026-09-27), where a 10.1 MB package went up with
        4.6 MB free flash, `free-hdd-space` did not move and `free-memory` fell by the
        same 10.3 MB. Where there is no `flash/`, the root is the flash itself.
        """
        return self.resource.free_memory if self.has_flash_dir else self.resource.free_hdd_space

    @property
    def version_floor(self) -> RosVersion | None:
        """The oldest RouterOS this board takes, or None when it does not say.

        MikroTik allows no downgrade below the version the board was shipped with. Two
        fields carry it and they agree where both are complete: `factory-firmware` from
        `/system routerboard print` (6.42.4 on the mmips RB760iGS) and `factory-software`
        from `/system resource print` (the same 6.42.4, but 7.20 next to a
        `factory-firmware` of 7.20.7 on the arm hEX S, 2026-09-27). The firmware field
        wins for its patch number; a board with neither, such as CHR, has no floor here.
        """
        board = self.routerboard
        for text in (board.factory_firmware if board else "", self.resource.factory_software):
            if text:
                try:
                    return RosVersion.parse(text)
                except ValueError:
                    continue
        return None

    @property
    def ipv6_package_state(self) -> Ipv6PackageState:
        if self.version.major >= 7:
            return Ipv6PackageState.BUILTIN
        package = next((p for p in self.packages if p.name == "ipv6"), None)
        if package is None:
            return Ipv6PackageState.MISSING
        return Ipv6PackageState.DISABLED if package.disabled else Ipv6PackageState.ENABLED
