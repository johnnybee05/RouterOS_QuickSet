"""WAN configuration."""

from __future__ import annotations

from enum import StrEnum
from ipaddress import IPv4Address, IPv4Interface
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class WanType(StrEnum):
    DHCP = "dhcp"
    STATIC = "static"
    PPPOE = "pppoe"


class DhcpWan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[WanType.DHCP] = WanType.DHCP
    use_peer_dns: bool = True
    add_default_route: bool = True


class StaticWan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[WanType.STATIC] = WanType.STATIC
    address: IPv4Interface = IPv4Interface("192.0.2.2/24")
    gateway: IPv4Address = IPv4Address("192.0.2.1")
    dns_servers: list[IPv4Address] = Field(default_factory=list)


class PppoeWan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[WanType.PPPOE] = WanType.PPPOE
    interface_name: str = "pppoe-out1"
    username: str = ""
    # Never written to profiles; lives only in memory for the current run.
    password: SecretStr = Field(default=SecretStr(""), exclude=True)
    use_peer_dns: bool = True
    add_default_route: bool = True


WanSettings = Annotated[DhcpWan | StaticWan | PppoeWan, Field(discriminator="type")]

# Czech incumbent Cetin delivers its PPPoE service tagged with this VLAN; the WAN port of
# a router behind their CPE therefore carries VLAN 848 and nothing untagged.
CETIN_VLAN_ID = 848


class WanConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    interface: str | None = None
    # VLAN the provider tags the WAN with (Cetin PPPoE: 848); None = untagged on the port.
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    vlan_name: str = ""  # empty = named after the id, "vlan848"
    settings: WanSettings = Field(default_factory=DhcpWan)
    add_to_wan_list: bool = True
    mss_clamping: bool = False
    nat_masquerade: bool = True

    @property
    def type(self) -> WanType:
        return self.settings.type

    @property
    def vlan_interface(self) -> str | None:
        """Name of the VLAN interface the WAN rides on, or None when it is untagged."""
        if self.vlan_id is None:
            return None
        return self.vlan_name or f"vlan{self.vlan_id}"

    def with_type(self, wan_type: WanType) -> WanConfig:
        """Switch WAN type, applying type-specific defaults (MSS clamping on for PPPoE)."""
        if wan_type == self.type:
            return self
        settings: DhcpWan | StaticWan | PppoeWan
        if wan_type == WanType.DHCP:
            settings = DhcpWan()
        elif wan_type == WanType.STATIC:
            settings = StaticWan()
        else:
            settings = PppoeWan()
        return self.model_copy(
            update={"settings": settings, "mss_clamping": wan_type == WanType.PPPOE}
        )
