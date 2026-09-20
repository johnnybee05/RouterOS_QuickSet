"""Connect to a device addressed by MAC address (spec addendum §10.2, decision B + C).

Two strategies, tried in order:

1. **IPv6 link-local** — cheap, works only when IPv6 is up on the device (usually not on v6):
   connect API-SSL / SSH straight to ``fe80::…%<ifindex>``.
2. **MAC-Telnet bootstrap** — log in over MAC-Telnet (EC-SRP5), add a temporary management IP
   on the interface the device announced itself on, then connect over that IPv4 address. The
   temporary address is removed again by the caller (or wiped by a reset).

Verified end to end on 2026-09-16 against a hEX S running RouterOS 7.20.7; the
findings and the remaining unknowns are in docs/DIALECT_NOTES.md.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from ipaddress import IPv4Address

from mqs.transport.base import (
    AuthError,
    ConnectionLost,
    ConnectionParams,
    ServiceUnavailable,
    Transport,
    TransportError,
)
from mqs.transport.factory import (
    ConnectFailed,
    ConnectOutcome,
    TransportChoice,
    connect_transport,
)
from mqs.transport.mactelnet.client import MacTelnetError, MacTelnetSession, UdpBroadcastChannel
from mqs.transport.mndp import DiscoveredDevice
from mqs.transport.netifs import LocalInterface, interface_mac, list_ipv4_interfaces

TEMP_COMMENT = "mqs-temp"


class MacConnectMethod(StrEnum):
    LINK_LOCAL = "link-local"
    MAC_TELNET = "mac-telnet"


@dataclass
class MacConnectResult:
    outcome: ConnectOutcome
    method: MacConnectMethod
    temp_address: str | None = None  # "a.b.c.d/nn" added on the device, to remove afterwards
    device_interface: str | None = None

    @property
    def transport(self) -> Transport:
        return self.outcome.transport


def bootstrap_interface(reported: str | None) -> str:
    """Device interface the temporary address belongs on.

    MNDP reports the announcing port as "<bridge>/<port>" while the port is bridged:
    verified 2026-09-16, a hEX S announces "bridge/ether1" and carries its address on
    "bridge". The address has to go on the bridge, not on its slave port, and passing
    the reported name verbatim is not even a valid interface name.
    """
    if not reported:
        return "ether1"
    return reported.split("/", 1)[0]


def link_local_host(device: DiscoveredDevice, interface: LocalInterface | None) -> str | None:
    """Scoped IPv6 link-local host string, or None when the device has no link-local IPv6."""
    if device.ipv6 is None or not device.ipv6.is_link_local:
        return None
    if interface is not None and interface.index is not None:
        return f"{device.ipv6}%{interface.index}"
    if device.local_interface:
        return f"{device.ipv6}%{device.local_interface}"
    return None


def default_session_factory(device: DiscoveredDevice, params: ConnectionParams,
                            local_address: str) -> MacTelnetSession:
    local = next((i for i in list_ipv4_interfaces() if str(i.address) == local_address), None)
    channel = UdpBroadcastChannel(
        local_address=local_address,
        broadcast=str(local.broadcast) if local else "255.255.255.255",
    )
    return MacTelnetSession(channel, device.mac, params.username, params.password,
                            src_mac=interface_mac(local.index) if local else None,
                            connect_timeout=params.connect_timeout)


def connect_by_mac(
    device: DiscoveredDevice,
    params: ConnectionParams,
    proposed_address: IPv4Address,
    interface: LocalInterface,
    *,
    connect: Callable[..., ConnectOutcome] = connect_transport,
    session_factory: Callable[..., MacTelnetSession] = default_session_factory,
    choice: TransportChoice = TransportChoice.AUTO,
    on_step: Callable[[str], None] = lambda step: None,
) -> MacConnectResult:
    """Connect to ``device`` by MAC. ``proposed_address`` is a free IP in the PC's subnet,
    already confirmed by the user; ``interface`` is the local NIC to reach the device on."""
    host_ll = link_local_host(device, interface)
    if host_ll is not None:
        on_step(MacConnectMethod.LINK_LOCAL.value)
        try:
            outcome = connect(replace(params, host=host_ll), choice)
            return MacConnectResult(outcome, MacConnectMethod.LINK_LOCAL)
        except AuthError:
            raise
        except (ConnectFailed, ServiceUnavailable, TransportError):
            pass  # fall back to the MAC-Telnet bootstrap

    on_step(MacConnectMethod.MAC_TELNET.value)
    prefix = interface.network.prefixlen
    address = f"{proposed_address}/{prefix}"
    device_interface = bootstrap_interface(device.interface_name)
    session = session_factory(device, params, str(interface.address))
    try:
        session.authenticate()
        session.run(
            f"/ip address add address={address} interface={device_interface} "
            f"comment={TEMP_COMMENT}"
        )
        # Ask the device whether it really has the address. A command whose output we
        # could not read is not a command we may claim ran: a device once reported the
        # bootstrap as done while the console had swallowed the input and added nothing,
        # which turned into a misleading "set but not reachable" further down.
        if session.run(f':put [:len [/ip address find comment="{TEMP_COMMENT}"]]').strip() \
                in ("", "0"):
            raise MacTelnetError(
                f"the device did not add the temporary address {address} on "
                f"{device_interface}"
            )
    except MacTelnetError:
        session.close()
        raise

    ip_params = replace(params, host=str(proposed_address))
    try:
        outcome = connect(ip_params, choice)
    except TransportError as exc:
        # Take the address back over the session we still hold, so a failed attempt does
        # not leave the device carrying an address nobody can reach it on.
        with contextlib.suppress(MacTelnetError):
            session.run(f'/ip address remove [find comment="{TEMP_COMMENT}"]')
        session.close()
        raise MacTelnetError(
            f"temporary address {address} was added on {device_interface} but the device "
            f"is not reachable on it, so it was removed again: {exc}"
        ) from exc
    session.close()
    return MacConnectResult(outcome, MacConnectMethod.MAC_TELNET,
                            temp_address=address, device_interface=device_interface)


def remove_temp_address(transport: Transport, address: str) -> None:
    """Remove the bootstrap management IP once we no longer need it (import mode).

    Losing the connection here is the normal outcome rather than a failure: we are talking
    to the device *through* this very address, so the moment RouterOS drops it our session
    dies before the reply can come back. Verified on 7.20.7 - the address is gone, only the
    answer never arrives.
    """
    from mqs.generator.ir import Find, cmd

    with contextlib.suppress(ConnectionLost):
        transport.execute_command(
            cmd("/ip address", "remove", selector=Find.by(address=address, comment=TEMP_COMMENT))
        )


__all__ = [
    "TEMP_COMMENT",
    "MacConnectMethod",
    "MacConnectResult",
    "bootstrap_interface",
    "connect_by_mac",
    "default_session_factory",
    "link_local_host",
    "remove_temp_address",
]
