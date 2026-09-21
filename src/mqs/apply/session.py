"""The live device session and the wiring that drives an apply.

This used to live in `mqs.ui.pages`, which meant the flow could only ever be exercised
by clicking in the window. It sits outside the GUI so that `mqs.cli` runs the very same
code: anything tested from the command line is what the window does too.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from ipaddress import IPv4Address
from typing import Any

from mqs.apply.orchestrator import ApplyContext, ApplyEvent, ApplyReport
from mqs.apply.plan import ApplyMode, ApplyOptions
from mqs.apply.reconnect import reconnect_with_retry
from mqs.generator.base import GenerationResult
from mqs.model.inventory import DeviceInventory
from mqs.model.profile import DeviceProfile
from mqs.transport.base import ConnectionParams, Transport, TransportError
from mqs.transport.discovery import collect_inventory, probe_features, session_interface
from mqs.transport.factory import TransportChoice, connect_transport
from mqs.transport.mac_connect import (
    MacConnectMethod,
    MacConnectResult,
    connect_by_mac,
    default_session_factory,
    remove_temp_address,
)
from mqs.transport.mactelnet.client import MacTelnetSession
from mqs.transport.mactelnet.files import MacTelnetStrategy
from mqs.transport.mndp import DiscoveredDevice
from mqs.transport.netifs import LocalInterface, route_source_address
from mqs.transport.ssh import SshTransport
from mqs.transport.upload import FileUploader, HttpFetchStrategy, ScpStrategy, SftpStrategy


@dataclass
class DeviceSession:
    params: ConnectionParams
    transport: Transport
    inventory: DeviceInventory
    probes: dict[str, bool] = field(default_factory=dict)
    session_port: str | None = None
    fell_back: bool = False
    temp_address: str | None = None  # bootstrap IP added by connect-by-MAC, to remove later
    mac_method: str | None = None
    # How to open a MAC-Telnet console again, for moving files without an IP path.
    mac_device: DiscoveredDevice | None = None
    mac_local_address: str | None = None  # this PC's address on the device's network


class TempAddressLeft(TransportError):
    """The address connect-by-MAC lent the device could not be given back."""

    def __init__(self, address: str, reason: str) -> None:
        super().__init__(f"temporary address {address} is still on the device: {reason}")
        self.address = address


def end_session(session: DeviceSession) -> None:
    """Hang up, first giving back the address connect-by-MAC lent the device.

    Nothing gave it back before: after an import, or after only looking, the device kept
    an address from this PC's network for good. The connection is closed either way;
    raises TempAddressLeft when the address could not be removed.
    """
    address = session.temp_address
    try:
        if address is not None:
            if not session.transport.connected:
                raise TempAddressLeft(address, "the connection to the device is gone")
            try:
                remove_temp_address(session.transport, address)
            except TransportError as exc:
                raise TempAddressLeft(address, str(exc)) from exc
            session.temp_address = None
    finally:
        with contextlib.suppress(OSError, TransportError):
            session.transport.close()


def session_after_apply(session: DeviceSession, report: ApplyReport) -> None:
    """Carry over what an apply changed: the connection it ended on (a new SSH port, a
    reboot, a reset's new admin password) and whether the lent address survived it."""
    if report.transport is not session.transport:
        session.transport = report.transport
        session.params = report.transport.params
    session.temp_address = report.temp_address


def find_session_port(transport: Transport, host: str) -> str | None:
    try:
        return session_interface(transport, route_source_address(host))
    except (OSError, TransportError):
        return None


def discover_session(
    transport: Transport,
    params: ConnectionParams,
    *,
    on_step: Callable[[Any], None] = lambda step: None,
    fell_back: bool = False,
    temp_address: str | None = None,
    mac_method: str | None = None,
    mac_device: DiscoveredDevice | None = None,
    mac_local_address: str | None = None,
    discover: Callable[..., DeviceInventory] = collect_inventory,
    probe: Callable[..., dict[str, bool]] = probe_features,
    session_port: Callable[[Transport, str], str | None] = find_session_port,
) -> DeviceSession:
    """Describe a device that is already connected."""
    inventory = discover(transport, on_step=on_step)
    return DeviceSession(
        params, transport, inventory, probe(transport, inventory.version),
        session_port(transport, params.host), fell_back,
        temp_address=temp_address, mac_method=mac_method,
        mac_device=mac_device, mac_local_address=mac_local_address,
    )


def open_session(
    params: ConnectionParams,
    choice: TransportChoice = TransportChoice.AUTO,
    *,
    api_port: int | None = None,
    ssh_port: int | None = None,
    on_step: Callable[[Any], None] = lambda step: None,
    cancelled: Callable[[], bool] = lambda: False,
) -> DeviceSession:
    """Connect and describe the device: what the GUI does on "Připojit", minus by-MAC."""
    outcome = connect_transport(params, choice, api_port=api_port, ssh_port=ssh_port,
                                cancelled=cancelled)
    return discover_session(outcome.transport, params, on_step=on_step,
                            fell_back=outcome.fell_back)


def open_mac_session(
    device: DiscoveredDevice,
    params: ConnectionParams,
    address: IPv4Address,
    interface: LocalInterface,
    choice: TransportChoice = TransportChoice.AUTO,
    *,
    on_step: Callable[[Any], None] = lambda step: None,
    on_connected: Callable[[Transport], None] = lambda transport: None,
    mac_connect: Callable[..., MacConnectResult] = connect_by_mac,
    **discover_fns: Any,
) -> DeviceSession:
    """Connect by MAC and describe the device: "Připojit přes MAC" in the window.

    ``address`` is the free address in this PC's subnet the user agreed to lend the
    device; ``on_connected`` gets the transport before the slower inventory runs.
    """
    result = mac_connect(device, params, address, interface, choice=choice, on_step=on_step)
    on_connected(result.transport)
    reached = replace(params, host=result.host or str(address))
    return discover_session(
        result.transport, reached, on_step=on_step,
        temp_address=result.temp_address, mac_method=result.method.value,
        mac_device=device, mac_local_address=str(interface.address), **discover_fns,
    )


class LazySsh:
    """SSH connection opened only when a file strategy needs it (main transport is API).

    A connection that failed is not tried again: SFTP and SCP ask for it for every file,
    and each retry cost a full connect timeout while nothing had changed (on 2026-09-21
    a MAC session spent a minute timing out on an SSH port that never answered).
    """

    def __init__(self, params: ConnectionParams) -> None:
        self.params = params
        self._transport: SshTransport | None = None
        self._error: Exception | None = None

    def _connected(self) -> SshTransport:
        if self._error is not None:
            raise self._error
        if self._transport is None:
            transport = SshTransport(self.params)
            try:
                transport.connect()
            except (TransportError, OSError) as exc:
                self._error = exc
                raise
            self._transport = transport
        return self._transport

    def open_sftp(self) -> Any:
        return self._connected().open_sftp()

    def paramiko_transport(self) -> Any:
        return self._connected().paramiko_transport()

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None


class LazyConsole:
    """MAC-Telnet console opened only when a file strategy needs it, tried only once."""

    def __init__(self, device: DiscoveredDevice, params: ConnectionParams, local_address: str,
                 factory: Callable[..., MacTelnetSession] = default_session_factory) -> None:
        self.device = device
        self.params = params
        self.local_address = local_address
        self.factory = factory
        self._session: MacTelnetSession | None = None
        self._error: Exception | None = None

    def __call__(self) -> MacTelnetSession:
        if self._error is not None:
            raise self._error
        if self._session is None:
            session: MacTelnetSession | None = None
            try:
                session = self.factory(self.device, self.params, self.local_address)
                session.authenticate()
            except (TransportError, OSError) as exc:
                if session is not None:
                    session.close()
                self._error = exc
                raise
            self._session = session
        return self._session

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None


def build_uploader_factory(session: DeviceSession,
                           options: ApplyOptions) -> Callable[[Transport], FileUploader]:
    ssh_service = next((s for s in session.inventory.services if s.name == "ssh"), None)
    ssh_port = ssh_service.port if ssh_service else 22

    def factory(transport: Transport) -> FileUploader:
        owned: LazySsh | None = None
        if isinstance(transport, SshTransport):
            ssh: Any = transport  # the application's own session, not ours to close
        else:
            owned = LazySsh(replace(session.params, port=ssh_port))
            ssh = owned
        strategies: list[Any] = [SftpStrategy(ssh), ScpStrategy(ssh)]
        console: LazyConsole | None = None
        if session.mac_device is not None and session.mac_local_address is not None:
            console = LazyConsole(session.mac_device, session.params, session.mac_local_address)
            # Reached over MAC-Telnet, the console is the one path known to work: the IP
            # address is only lent to the device, and SSH may be firewalled, limited to
            # other addresses or off. Reached over IPv6 link-local, IP works: SSH first.
            if session.mac_method == MacConnectMethod.MAC_TELNET.value:
                strategies.insert(0, MacTelnetStrategy(console))
            else:
                strategies.append(MacTelnetStrategy(console))
        with contextlib.suppress(OSError):  # no route to the device: skip HTTP + fetch
            strategies.append(HttpFetchStrategy(transport,
                                                route_source_address(session.params.host)))

        def release() -> None:
            if owned is not None:
                owned.close()
            if console is not None:
                console.close()

        return FileUploader(strategies, allow_line_by_line=options.mode == ApplyMode.IMPORT,
                            release=release if owned is not None or console is not None
                            else None)

    return factory


def build_apply_context(
    profile: DeviceProfile,
    result: GenerationResult,
    options: ApplyOptions,
    session: DeviceSession,
    *,
    on_event: Callable[[ApplyEvent], None],
    now: Callable[[], datetime] = datetime.now,
    sleep: Callable[[float], None] = time.sleep,
    cancelled: Callable[[], bool] = lambda: False,
    uploader_factory_builder: Callable[..., Any] = build_uploader_factory,
    reconnect_fn: Callable[..., Transport] = reconnect_with_retry,
) -> ApplyContext:
    """Everything the orchestrator needs, assembled the one way the application does it."""
    return ApplyContext(
        profile=profile,
        result=result,
        options=options,
        inventory=session.inventory,
        transport=session.transport,
        # Both are needed to work out where the device will answer once the new
        # configuration is on it, which is not always where it answers now.
        session_port=session.session_port,
        temp_address=session.temp_address,
        uploader_factory=uploader_factory_builder(session, options),
        reconnect=lambda target: reconnect_fn(
            target, timeout_s=options.reconnect_timeout_s, cancelled=cancelled),
        now=now,
        sleep=lambda seconds: None if cancelled() else sleep(seconds),
        cancelled=cancelled,
        on_event=on_event,
    )


def probe_summary(probes: Mapping[str, bool]) -> str:
    return ", ".join(f"{name}={'yes' if value else 'no'}" for name, value in sorted(probes.items()))
