"""Connect to a device addressed by MAC address (spec addendum §10.2, decision B + C).

Two strategies, tried in order:

1. **IPv6 link-local** — cheap, works only when IPv6 is up on the device (usually not on v6):
   connect API-SSL / SSH straight to ``fe80::…%<ifindex>``.
2. **MAC-Telnet bootstrap** — log in over MAC-Telnet (EC-SRP5), add a temporary management IP
   on the interface the device announced itself on (under the name the device gives it now,
   see `device_interface_name`), then connect over that IPv4 address. The temporary address
   is removed again by the caller (or wiped by a reset).

Verified end to end on 2026-09-16 against a hEX S running RouterOS 7.20.7; the
findings and the remaining unknowns are in docs/DIALECT_NOTES.md.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from ipaddress import IPv4Address

from mqs.generator.render import quote_find
from mqs.transport.base import (
    AuthError,
    CommandTimeout,
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
from mqs.transport.netifs import (
    LocalInterface,
    interface_for,
    interface_mac,
    list_ipv4_interfaces,
)

TEMP_COMMENT = "mqs-temp"
REMOVAL_REPLY_S = 5.0  # how long a removal may stay unanswered before it counts as done


class MacConnectMethod(StrEnum):
    LINK_LOCAL = "link-local"
    MAC_TELNET = "mac-telnet"


@dataclass(frozen=True)
class MacRoute:
    """How a device was reached by MAC, kept so it can be reached the same way again.

    An apply can leave the device with none of the addresses this PC reached it on: a
    reset wipes every one, the lent address included, and the new configuration may put
    the rest in another network. Its MAC address, the network card it was heard on and
    the address the user agreed to lend it are still good.
    """

    device: DiscoveredDevice
    interface: LocalInterface  # this PC's network card the device was heard on
    address: IPv4Address  # the address the user agreed to lend the device

    @property
    def mac(self) -> str:
        return self.device.mac


@dataclass
class MacConnectResult:
    outcome: ConnectOutcome
    method: MacConnectMethod
    temp_address: str | None = None  # "a.b.c.d/nn" added on the device, to remove afterwards
    device_interface: str | None = None
    host: str | None = None  # where the transport is connected: the lent or link-local address

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


def _count(session: MacTelnetSession, condition: str) -> int:
    reply = session.run(f":put [:len [/interface find {condition}]]", confirm=False).strip()
    return int(reply) if reply.isdigit() else 0


def device_interface_name(session: MacTelnetSession, name: str, *, bridge: bool,
                          mac: str) -> str:
    """The name the device gives now to the interface MNDP announced as ``name``.

    MNDP can announce an interface under an earlier name (hEX S, 2026-09-24: a bridged
    port as "bridge1/ether1" 13 hours after it had become ether1-uplink), a scan keeps an
    announcement for a while, and the device refuses an address on a name it no longer
    has. So it is asked: the name itself, then a port by its default-name, then the
    interface the announcement came from by its MAC address - the bridge for a bridged
    port, the port itself otherwise (a bridge takes the MAC address of a port). Verified on
    7.20.7 with the whole bootstrap (docs/DIALECT_NOTES.md). Whatever the console will not
    answer leaves ``name`` as it is, which is how it was used before.
    """
    try:
        if _count(session, f"name={quote_find(name)}") == 1:
            return name
        conditions = [] if bridge else [f"default-name={quote_find(name)}"]
        if mac:
            kind = "type=bridge" if bridge else "type!=bridge"
            conditions.append(f"mac-address={quote_find(mac)} {kind}")
        for condition in conditions:
            if _count(session, condition) == 1:
                found = session.run(f":put [/interface get [find {condition}] name]",
                                    confirm=False).strip()
                return found or name
    except MacTelnetError:
        pass
    return name


def local_interface_for(device: DiscoveredDevice,
                        interfaces: Sequence[LocalInterface]) -> LocalInterface | None:
    """The PC's network card the device was heard on, to reach it by MAC through."""
    if device.local_address is not None:
        match = next((i for i in interfaces if i.address == device.local_address), None)
        if match is not None:
            return match
    if device.ipv4 is not None:
        return interface_for(device.ipv4, interfaces)
    return next((i for i in interfaces if i.name == device.local_interface), None)


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
    api_port: int | None = None,
    ssh_port: int | None = None,
    device_interface: str | None = None,
    on_step: Callable[[str], None] = lambda step: None,
) -> MacConnectResult:
    """Connect to ``device`` by MAC. ``proposed_address`` is a free IP in the PC's subnet,
    already confirmed by the user; ``interface`` is the local NIC to reach the device on.

    ``device_interface`` is where the address goes on the device, when what MNDP reported
    no longer holds: after an apply the announcing port may have left its bridge.
    """
    ports = {"api_port": api_port, "ssh_port": ssh_port}
    host_ll = link_local_host(device, interface)
    if host_ll is not None:
        on_step(MacConnectMethod.LINK_LOCAL.value)
        try:
            outcome = connect(replace(params, host=host_ll), choice, **ports)
            return MacConnectResult(outcome, MacConnectMethod.LINK_LOCAL, host=host_ll)
        except AuthError:
            raise
        except (ConnectFailed, ServiceUnavailable, TransportError):
            pass  # fall back to the MAC-Telnet bootstrap

    on_step(MacConnectMethod.MAC_TELNET.value)
    prefix = interface.network.prefixlen
    address = f"{proposed_address}/{prefix}"
    # MNDP names a bridged port "<bridge>/<port>"; a name the caller gives is meant as is.
    bridged = device_interface is None and "/" in (device.interface_name or "")
    device_interface = device_interface or bootstrap_interface(device.interface_name)
    session = session_factory(device, params, str(interface.address))
    try:
        session.authenticate()
        device_interface = device_interface_name(session, device_interface, bridge=bridged,
                                                 mac=device.mac)
        # Drop leftovers from an earlier attempt first: a command we could not confirm
        # may still have run, and a stale address makes the add fail with
        # "already have such address".
        # These two write commands are not judged by how readable the console was; the
        # query below asks the device itself, which is the only answer that counts.
        session.run(f'/ip address remove [find comment="{TEMP_COMMENT}"]', confirm=False)
        session.run(
            f"/ip address add address={address} interface={device_interface} "
            f"comment={TEMP_COMMENT}",
            confirm=False,
        )
        # Ask the device whether it really has the address. A command whose output we
        # could not read is not a command we may claim ran: a device once reported the
        # bootstrap as done while the console had swallowed the input and added nothing,
        # which turned into a misleading "set but not reachable" further down.
        found = session.run(f':put [:len [/ip address find comment="{TEMP_COMMENT}"]]',
                            timeout=20.0)
        if found.strip() in ("", "0"):
            raise MacTelnetError(
                f"the device did not add the temporary address {address} on "
                f"{device_interface}"
            )
    except MacTelnetError:
        # An unconfirmed command may still have run, so take the address back rather than
        # leaving one behind for the next attempt to trip over.
        with contextlib.suppress(MacTelnetError):
            session.run(f'/ip address remove [find comment="{TEMP_COMMENT}"]')
        session.close()
        raise

    ip_params = replace(params, host=str(proposed_address))
    try:
        outcome = connect(ip_params, choice, **ports)
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
                            temp_address=address, device_interface=device_interface,
                            host=str(proposed_address))


def remove_temp_address(transport: Transport, address: str) -> None:
    """Give back the bootstrap management IP once we no longer need it.

    No answer is the normal outcome rather than a failure: we are talking to the device
    *through* this very address, so the moment RouterOS drops it the reply has nowhere to
    come from. Verified on 7.20.7 - the address is gone, only the answer never arrives.
    Waiting the whole command timeout for it held a disconnect up for 30 s (measured
    2026-09-21), while a device that does not remove the address answers at once, so a
    short silence already says it is gone.
    """
    from mqs.generator.ir import Find, cmd

    with (transport.reply_timeout(REMOVAL_REPLY_S),
          contextlib.suppress(ConnectionLost, CommandTimeout)):
        transport.execute_command(
            cmd("/ip address", "remove", selector=Find.by(address=address, comment=TEMP_COMMENT))
        )


__all__ = [
    "REMOVAL_REPLY_S",
    "TEMP_COMMENT",
    "MacConnectMethod",
    "MacConnectResult",
    "MacRoute",
    "bootstrap_interface",
    "connect_by_mac",
    "default_session_factory",
    "device_interface_name",
    "link_local_host",
    "local_interface_for",
    "remove_temp_address",
]
