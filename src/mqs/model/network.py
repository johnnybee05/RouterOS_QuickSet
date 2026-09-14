"""IPv4 helper types shared by the configuration model."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Interface, IPv4Network

from pydantic import BaseModel, ConfigDict, model_validator


class Ipv4Range(BaseModel):
    """Inclusive IPv4 address range, rendered as ``start-end``."""

    model_config = ConfigDict(frozen=True)

    start: IPv4Address
    end: IPv4Address

    @model_validator(mode="after")
    def _ordered(self) -> Ipv4Range:
        if self.start > self.end:
            raise ValueError("range start must not be greater than range end")
        return self

    def __contains__(self, address: object) -> bool:
        return isinstance(address, IPv4Address) and self.start <= address <= self.end

    def within(self, network: IPv4Network) -> bool:
        return self.start in network and self.end in network

    def __str__(self) -> str:
        return f"{self.start}-{self.end}"


def usable_host(interface: IPv4Interface) -> bool:
    """True if the address is not the network or broadcast address of its subnet."""
    net = interface.network
    if net.prefixlen >= 31:
        return True
    return interface.ip not in (net.network_address, net.broadcast_address)
