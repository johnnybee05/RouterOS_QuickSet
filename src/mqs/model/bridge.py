"""Bridge and LAN configuration."""

from __future__ import annotations

from enum import StrEnum
from ipaddress import IPv4Address, IPv4Interface

from pydantic import BaseModel, ConfigDict, Field

from mqs.model.network import Ipv4Range


class ProtocolMode(StrEnum):
    NONE = "none"
    STP = "stp"
    RSTP = "rstp"
    MSTP = "mstp"


class FrameTypes(StrEnum):
    ADMIT_ALL = "admit-all"
    ADMIT_ONLY_UNTAGGED_AND_PRIORITY_TAGGED = "admit-only-untagged-and-priority-tagged"
    ADMIT_ONLY_VLAN_TAGGED = "admit-only-vlan-tagged"


class HwOffloadMode(StrEnum):
    AUTO = "auto"
    FORCE_ON = "force-on"
    OFF = "off"


class BridgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="bridge", min_length=1, max_length=64)
    protocol_mode: ProtocolMode = ProtocolMode.RSTP
    priority: int | None = Field(default=None, ge=0, le=65535)
    ageing_time_s: int | None = Field(default=None, ge=1)
    vlan_filtering: bool = False
    pvid: int = Field(default=1, ge=1, le=4094)
    frame_types: FrameTypes = FrameTypes.ADMIT_ALL
    use_ip_firewall: bool = False
    hw_offload: HwOffloadMode = HwOffloadMode.AUTO
    ports: list[str] = Field(default_factory=list)


class LanAddressing(StrEnum):
    STATIC = "static"
    DHCP_CLIENT = "dhcp-client"
    NONE = "none"


class DhcpServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    name: str = Field(default="dhcp1", min_length=1)
    pool_name: str = Field(default="dhcp_pool", min_length=1)
    pool: Ipv4Range = Ipv4Range(
        start=IPv4Address("192.168.88.10"), end=IPv4Address("192.168.88.254")
    )
    gateway: IPv4Address | None = None  # None = bridge address
    dns_servers: list[IPv4Address] = Field(default_factory=list)  # empty = gateway
    lease_time_s: int = Field(default=1800, ge=60)
    comment: str = ""


class LanConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    addressing: LanAddressing = LanAddressing.STATIC
    address: IPv4Interface | None = IPv4Interface("192.168.88.1/24")
    dhcp_server: DhcpServerConfig = Field(default_factory=DhcpServerConfig)
