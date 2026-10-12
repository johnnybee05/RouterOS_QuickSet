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
from pathlib import Path
from typing import Any

from mqs.apply.orchestrator import ApplyContext, ApplyEvent, ApplyReport
from mqs.apply.plan import DEFAULT_KEEP_BACKUPS, ApplyMode, ApplyOptions
from mqs.apply.reconnect import reconnect_hosts, reconnect_with_retry
from mqs.apply.version_change import VersionContext, VersionPlan, VersionReport
from mqs.generator.base import GenerationResult
from mqs.model.inventory import DeviceInventory
from mqs.model.profile import DeviceProfile
from mqs.transport.base import AuthError, ConnectionParams, Transport, TransportError
from mqs.transport.discovery import (
    collect_inventory,
    probe_features,
    session_interface,
    to_bool,
)
from mqs.transport.factory import TransportChoice, connect_transport
from mqs.transport.mac_connect import (
    MacConnectMethod,
    MacConnectResult,
    MacRoute,
    answers_for_mac,
    connect_by_mac,
    default_session_factory,
    lend_address,
    remove_temp_address,
    withdraw_address,
)
from mqs.transport.mactelnet.client import MacTelnetSession
from mqs.transport.mactelnet.files import MacTelnetStrategy
from mqs.transport.mndp import DiscoveredDevice
from mqs.transport.netifs import LocalInterface, route_source_address
from mqs.transport.ssh import SshTransport
from mqs.transport.upload import (
    FileUploader,
    HttpFetchStrategy,
    ScpStrategy,
    SftpStrategy,
    UploadStrategy,
)


@dataclass
class DeviceSession:
    params: ConnectionParams
    transport: Transport
    inventory: DeviceInventory
    probes: dict[str, bool] = field(default_factory=dict)
    session_port: str | None = None
    fell_back: bool = False
    temp_address: str | None = None  # bootstrap IP added by connect-by-MAC, to remove later
    mac_method: str | None = None  # how the connection in use got in by MAC, if it did
    # How the device was reached by MAC: to get back in the same way after an apply, and
    # to open a MAC-Telnet console for moving files without an IP path.
    mac_route: MacRoute | None = None

    @property
    def reached_by_mac(self) -> MacRoute | None:
        """The route the connection in use came in by, None when it came to an address."""
        return self.mac_route if self.mac_method is not None else None


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


def session_after_version_change(session: DeviceSession, report: VersionReport) -> None:
    """Carry over what the version change left: the connection it came back on, the
    address it had to borrow to get there, and the version the device now runs.

    The inventory is not patched up from the report: the caller reads the device again,
    because a device that has changed its major version is a different device.
    """
    if report.transport is not session.transport:
        session.transport = report.transport
        session.params = report.transport.params
        if report.mac_method is not None:
            session.mac_method = report.mac_method
    if report.temp_address is not None:
        session.temp_address = report.temp_address


def session_after_apply(session: DeviceSession, report: ApplyReport) -> None:
    """Carry over what an apply changed: the connection it ended on (a new SSH port, a
    reboot, a reset's new admin password), whether it came back by MAC, and which lent
    address is on the device now."""
    if report.transport is not session.transport:
        session.transport = report.transport
        session.params = report.transport.params
        session.mac_method = report.mac_method
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
    mac_route: MacRoute | None = None,
    discover: Callable[..., DeviceInventory] = collect_inventory,
    probe: Callable[..., dict[str, bool]] = probe_features,
    session_port: Callable[[Transport, str], str | None] = find_session_port,
) -> DeviceSession:
    """Describe a device that is already connected."""
    inventory = discover(transport, on_step=on_step)
    return DeviceSession(
        params, transport, inventory, probe(transport, inventory.version),
        session_port(transport, params.host), fell_back,
        temp_address=temp_address, mac_method=mac_method, mac_route=mac_route,
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
    api_port: int | None = None,
    ssh_port: int | None = None,
    on_step: Callable[[Any], None] = lambda step: None,
    on_connected: Callable[[Transport], None] = lambda transport: None,
    mac_connect: Callable[..., MacConnectResult] = connect_by_mac,
    **discover_fns: Any,
) -> DeviceSession:
    """Connect by MAC and describe the device: "Připojit přes MAC" in the window.

    ``address`` is the free address in this PC's subnet the user agreed to lend the
    device; ``on_connected`` gets the transport before the slower inventory runs.
    """
    result = mac_connect(device, params, address, interface, choice=choice,
                         api_port=api_port, ssh_port=ssh_port, on_step=on_step)
    reached = replace(params, host=result.host or str(address))
    try:
        on_connected(result.transport)
        return discover_session(
            result.transport, reached, on_step=on_step,
            temp_address=result.temp_address, mac_method=result.method.value,
            mac_route=MacRoute(device, interface, address), **discover_fns,
        )
    except BaseException:
        # Nobody else holds the session yet, so nobody else would give the address back:
        # on 2026-09-25 a connect cancelled in the window left 192.168.55.254 on a v6
        # hEX S for good. It can only go over the connection that is still open.
        if result.temp_address is not None and result.transport.connected:
            with contextlib.suppress(TransportError):
                remove_temp_address(result.transport, result.temp_address)
        with contextlib.suppress(OSError, TransportError):
            result.transport.close()
        raise


def ssh_password_refusal(transport: Transport, params: ConnectionParams) -> str | None:
    """Why the device turns this account's password away over SSH, as far as it says.

    RouterOS 7 by default takes no password over SSH from a user that has an SSH key
    (`/ip ssh password-authentication=yes-if-no-key`), while api-ssl knows no keys and
    takes it. A session connected with the password alone was therefore in, and only the
    backup download failed, with nothing but "SSH authentication failed" to show for it
    (RB4011, 7.23.8, 2026-10-12). Older versions are documented to say the same with
    `always-allow-password-login=no` (NUTNO OVĚŘIT). Asked over the connection the session
    already has;
    None when it cannot say, or when a key was given and it was the key that was refused.
    """
    if params.key_filename:
        return None
    try:
        settings = next(iter(transport.execute("/ip/ssh/print")), {})
        has_key = any(row.get("user") == params.username
                      for row in transport.execute("/user/ssh-keys/print"))
    except TransportError:
        return None
    policy = settings.get("password-authentication")
    if policy == "no":
        return ("the device takes no password over SSH (/ip ssh "
                "password-authentication=no): give the SSH key in the connection")
    if not has_key:
        return None
    if policy == "yes-if-no-key":
        setting = "password-authentication=yes-if-no-key"
    elif policy is None and "always-allow-password-login" in settings \
            and not to_bool(settings, "always-allow-password-login"):
        setting = "always-allow-password-login=no"
    else:
        return None
    return (f"{params.username} has an SSH key on the device, which then takes no password "
            f"over SSH (/ip ssh {setting}): give that key in the connection")


class LazySsh:
    """SSH connection opened only when a file strategy needs it (main transport is API).

    A connection that failed is not tried again: SFTP and SCP ask for it for every file,
    and each retry cost a full connect timeout while nothing had changed (on 2026-09-21
    a MAC session spent a minute timing out on an SSH port that never answered).

    ``check`` runs once on the fresh connection and may refuse it: over a lent address
    that is the one place to ask whether the device answering is really ours.

    ``refusal`` says why a refused login was refused (`ssh_password_refusal`), which then
    goes into the error: "authentication failed" alone does not tell what to change.
    """

    def __init__(self, params: ConnectionParams,
                 check: Callable[[SshTransport], None] | None = None,
                 refusal: Callable[[], str | None] | None = None) -> None:
        self.params = params
        self.check = check
        self.refusal = refusal
        self._transport: SshTransport | None = None
        self._error: Exception | None = None

    def _connected(self) -> SshTransport:
        if self._error is not None:
            raise self._error
        if self._transport is None:
            transport = SshTransport(self.params)
            try:
                transport.connect()
            except AuthError as exc:
                reason = self.refusal() if self.refusal is not None else None
                if reason is None:
                    self._error = exc
                    raise
                self._error = AuthError(f"{exc}: {reason}", exc.kind)
                raise self._error from exc
            except (TransportError, OSError) as exc:
                self._error = exc
                raise
            try:
                if self.check is not None:
                    self.check(transport)
            except (TransportError, OSError) as exc:
                # Connected, but not to whom we meant: hang up on the stranger.
                with contextlib.suppress(OSError, TransportError):
                    transport.close()
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


class LentAddressStrategy:
    """Move a file over an IPv4 address lent to the device just for the transfer.

    A device reached over IPv6 link-local has no IPv4 path of ours: its SSH is often
    limited to a management subnet and drops the link-local connection before the
    greeting (hEX S, 2026-09-28: "No existing session"), `/tool fetch` has no address of
    this PC to fetch from, and the console moves at most `MAX_UPLOAD` - so a 10 MB
    RouterOS package had nowhere to go and the update failed.

    The address the user already agreed to lend for the MAC connection is put on the
    device over MAC-Telnet the first time a file needs it, the ordinary IP strategies run
    over it, and `close` takes it back. Nothing is lent while the cheaper paths work.
    """

    name = UploadStrategy.LENT_ADDRESS

    def __init__(self, transport: Transport, console: LazyConsole, route: MacRoute,
                 params: ConnectionParams, ssh_port: int) -> None:
        self.transport = transport
        self.console = console
        self.route = route
        self.params = params
        self.ssh_port = ssh_port
        self.device_interface: str | None = None  # where the address went, for the log
        self._uploader: FileUploader | None = None
        self._ssh: LazySsh | None = None
        self._lent: str | None = None

    def lend(self) -> str:
        """The lent address as "a.b.c.d/nn", put on the device on the first call."""
        if self._lent is None:
            address = f"{self.route.address}/{self.route.interface.network.prefixlen}"
            self.device_interface = lend_address(self.console(), self.route.device, address)
            self._lent = address
        return self._lent

    def _ours(self, transport: SshTransport) -> None:
        """Refuse an SSH login to whoever else holds the address we lent.

        The address is free only as far as the user knows; on 2026-09-28 a stranger on
        192.168.55.254 took an apply's files while the device itself got none of them.
        """
        if not answers_for_mac(transport, self.route.mac):
            raise TransportError(
                f"{self.route.address} answers for another device than {self.route.mac}: "
                f"lend an address that nothing else uses")

    def uploader(self) -> FileUploader:
        if self._uploader is None:
            self.lend()
            self._ssh = LazySsh(replace(self.params, host=str(self.route.address),
                                        port=self.ssh_port), check=self._ours)
            # The fetch is ordered over the connection we came in on, so it reaches the
            # right device whoever else may hold the address; it downloads from this PC's
            # own address on the network the device was heard on.
            self._uploader = FileUploader([
                SftpStrategy(self._ssh), ScpStrategy(self._ssh),
                HttpFetchStrategy(self.transport, str(self.route.interface.address)),
            ])
        return self._uploader

    def upload(self, data: bytes, remote_path: str) -> None:
        self.uploader().upload(data, remote_path)

    def download(self, remote_path: str, local_path: Path) -> None:
        self.uploader().download(remote_path, local_path)

    def close(self) -> None:
        if self._ssh is not None:
            self._ssh.close()
            self._ssh = None
        self._uploader = None
        if self._lent is not None:
            # Over the console, not over the address itself: it is going away.
            with contextlib.suppress(TransportError, OSError):
                withdraw_address(self.console())
            self._lent = None


def build_uploader_factory(session: DeviceSession,
                           options: ApplyOptions) -> Callable[[Transport], FileUploader]:
    ssh_service = next((s for s in session.inventory.services if s.name == "ssh"), None)
    ssh_port = ssh_service.port if ssh_service else 22

    def factory(transport: Transport) -> FileUploader:
        owned: LazySsh | None = None
        if isinstance(transport, SshTransport):
            ssh: Any = transport  # the application's own session, not ours to close
        else:
            owned = LazySsh(replace(session.params, port=ssh_port),
                            refusal=lambda: ssh_password_refusal(transport, session.params))
            ssh = owned
        strategies: list[Any] = [SftpStrategy(ssh), ScpStrategy(ssh)]
        console: LazyConsole | None = None
        lent: LentAddressStrategy | None = None
        if session.mac_route is not None:
            route = session.mac_route
            console = LazyConsole(route.device, session.params, str(route.interface.address))
            # RouterOS 6 has neither `/file add`, `/file read` nor `:convert` (6.49.10, v6
            # hEX S, 2026-09-25), so its console moves no file; trying it cost a login and
            # typed the whole script, admin password included, into the console for
            # nothing. Lending an address over that same console does work on v6.
            if session.inventory.version.major >= 7:
                # Reached over MAC-Telnet, the console is the one path known to work: the
                # IP address is only lent to the device, and SSH may be firewalled,
                # limited to other addresses or off. Reached over IPv6 link-local, IP
                # works: SSH first.
                if session.mac_method == MacConnectMethod.MAC_TELNET.value:
                    strategies.insert(0, MacTelnetStrategy(console))
                else:
                    strategies.append(MacTelnetStrategy(console))
        with contextlib.suppress(OSError):  # no route to the device: skip HTTP + fetch
            strategies.append(HttpFetchStrategy(transport,
                                                route_source_address(session.params.host)))
        # Reached over IPv6 link-local, nothing above carries a RouterOS package: the
        # console is limited to MAX_UPLOAD, SSH is often closed to an address outside the
        # device's management subnet, and this PC has no IPv4 address the device could
        # fetch from. Lending the address the MAC connection had ready gives all three.
        if (console is not None and session.mac_route is not None
                and session.mac_method == MacConnectMethod.LINK_LOCAL.value):
            lent = LentAddressStrategy(transport, console, session.mac_route,
                                       session.params, ssh_port)
            strategies.append(lent)

        def release() -> None:
            if lent is not None:
                lent.close()
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
        mac_route=session.mac_route,
        uploader_factory=uploader_factory_builder(session, options),
        reconnect=lambda target: reconnect_fn(
            target, timeout_s=options.reconnect_budget_s, cancelled=cancelled),
        now=now,
        sleep=lambda seconds: None if cancelled() else sleep(seconds),
        cancelled=cancelled,
        on_event=on_event,
    )


# A package is a file: the line-by-line fallback cannot carry one, and a version change
# has no `/import` to fall back to. RESET_AND_IMPORT is how that is said in ApplyOptions.
PACKAGE_UPLOAD = ApplyOptions(mode=ApplyMode.RESET_AND_IMPORT)


def build_version_context(
    plan: VersionPlan,
    session: DeviceSession,
    *,
    on_event: Callable[[ApplyEvent], None],
    profile: DeviceProfile | None = None,
    sleep: Callable[[float], None] = time.sleep,
    cancelled: Callable[[], bool] = lambda: False,
    reconnect_timeout_s: float = 300.0,
    upgrade_routerboot: bool = False,
    routerboot_downgrade: bool = False,
    download_dir: Path | None = None,
    keep_backups: int = DEFAULT_KEEP_BACKUPS,
    now: Callable[[], datetime] = datetime.now,
    uploader_factory_builder: Callable[..., Any] = build_uploader_factory,
    reconnect_fn: Callable[..., Transport] = reconnect_with_retry,
) -> VersionContext:
    """Everything a version change needs, assembled the one way the application does it.

    Nothing resets here, so the device keeps its addresses and the one it answers on is
    the first place to look afterwards. A move from v7 to v6 converts no configuration
    back, though, so where a profile is at hand its addresses come next, and a device
    reached by MAC is looked for by MAC first — that path needs no address at all.

    The configuration itself is backed up first, exactly as an apply backs it up, and
    ``download_dir`` is where that copy lands on this PC — the same directory and the
    same switch the apply uses, because it is the same safety net.
    """
    services = {s.name: s for s in session.inventory.services}
    api = services.get("api-ssl")
    ssh = services.get("ssh")
    alternatives = tuple(reconnect_hosts(profile, current_host=session.params.host,
                                         session_port=session.session_port)
                         if profile is not None else (session.params.host,))
    return VersionContext(
        plan=plan,
        inventory=session.inventory,
        transport=session.transport,
        uploader_factory=uploader_factory_builder(session, PACKAGE_UPLOAD),
        reconnect=lambda target: reconnect_fn(target, timeout_s=reconnect_timeout_s,
                                              cancelled=cancelled),
        on_event=on_event,
        sleep=lambda seconds: None if cancelled() else sleep(seconds),
        cancelled=cancelled,
        alternatives=alternatives,
        mac_route=session.reached_by_mac,
        api_port=api.port if api else 8729,
        ssh_port=ssh.port if ssh else 22,
        reconnect_timeout_s=reconnect_timeout_s,
        upgrade_routerboot=upgrade_routerboot,
        routerboot_downgrade=routerboot_downgrade,
        download_dir=download_dir,
        keep_backups=keep_backups,
        now=now,
    )


def probe_summary(probes: Mapping[str, bool]) -> str:
    return ", ".join(f"{name}={'yes' if value else 'no'}" for name, value in sorted(probes.items()))
