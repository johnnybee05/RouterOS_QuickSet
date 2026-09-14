"""Device inventory: pure data collected by discovery, consumed by capabilities/generator."""

from __future__ import annotations

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
    free_memory: int = 0
    total_memory: int = 0
    cpu: str = ""


class RouterboardInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routerboard: bool = False
    model: str = ""
    serial_number: str = ""
    current_firmware: str = ""
    upgrade_firmware: str = ""


class WirelessStack(StrEnum):
    NONE = "none"
    WIRELESS = "wireless"  # /interface wireless (v6, older v7 packages)
    WIFI = "wifi"  # /interface wifi (v7)


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

    def interface(self, name: str) -> InterfaceInfo | None:
        return next((i for i in self.interfaces if i.name == name), None)

    def switch_type(self, switch_name: str | None) -> str | None:
        if switch_name is None:
            return None
        chip = next((s for s in self.switches if s.name == switch_name), None)
        return chip.type if chip else None

    @property
    def ipv6_package_state(self) -> Ipv6PackageState:
        if self.version.major >= 7:
            return Ipv6PackageState.BUILTIN
        package = next((p for p in self.packages if p.name == "ipv6"), None)
        if package is None:
            return Ipv6PackageState.MISSING
        return Ipv6PackageState.DISABLED if package.disabled else Ipv6PackageState.ENABLED
