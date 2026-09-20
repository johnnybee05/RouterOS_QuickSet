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
from typing import Any

from mqs.apply.orchestrator import ApplyContext, ApplyEvent
from mqs.apply.plan import ApplyMode, ApplyOptions
from mqs.apply.reconnect import reconnect_with_retry
from mqs.generator.base import GenerationResult
from mqs.model.inventory import DeviceInventory
from mqs.model.profile import DeviceProfile
from mqs.transport.base import ConnectionParams, Transport, TransportError
from mqs.transport.discovery import collect_inventory, probe_features, session_interface
from mqs.transport.factory import TransportChoice, connect_transport
from mqs.transport.netifs import route_source_address
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


class LazySsh:
    """SSH connection opened only when a file strategy needs it (main transport is API)."""

    def __init__(self, params: ConnectionParams) -> None:
        self.params = params
        self._transport: SshTransport | None = None

    def _connected(self) -> SshTransport:
        if self._transport is None:
            transport = SshTransport(self.params)
            transport.connect()
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
        with contextlib.suppress(OSError):  # no route to the device: skip HTTP + fetch
            strategies.append(HttpFetchStrategy(transport,
                                                route_source_address(session.params.host)))
        return FileUploader(strategies, allow_line_by_line=options.mode == ApplyMode.IMPORT,
                            release=owned.close if owned is not None else None)

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
