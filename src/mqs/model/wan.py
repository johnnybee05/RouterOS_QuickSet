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


class WanConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    interface: str | None = None
    settings: WanSettings = Field(default_factory=DhcpWan)
    add_to_wan_list: bool = True
    mss_clamping: bool = False
    nat_masquerade: bool = True

    @property
    def type(self) -> WanType:
        return self.settings.type

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
