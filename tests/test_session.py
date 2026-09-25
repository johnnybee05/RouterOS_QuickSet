"""The device-session wiring shared by the window and the CLI: how files get moved."""

from __future__ import annotations

from datetime import datetime
from ipaddress import IPv4Address, IPv4Network

import pytest

from factories import FIVE_PORTS, make_inventory
from fake_transport import FakeTransport
from mqs.apply import session as session_mod
from mqs.apply.orchestrator import ApplyReport
from mqs.apply.plan import ApplyMode, ApplyOptions, make_names
from mqs.apply.session import (
    DeviceSession,
    LazyConsole,
    LazySsh,
    TempAddressLeft,
    build_apply_context,
    build_uploader_factory,
    end_session,
    open_mac_session,
    session_after_apply,
)
from mqs.model.profile import DeviceProfile
from mqs.transport.base import CommandError, ConnectionParams, TransportError
from mqs.transport.factory import ConnectOutcome
from mqs.transport.mac_connect import MacConnectMethod, MacConnectResult, MacRoute
from mqs.transport.mactelnet.client import MacTelnetAuthError
from mqs.transport.mactelnet.files import MacTelnetStrategy
from mqs.transport.mndp import DiscoveredDevice
from mqs.transport.netifs import LocalInterface
from mqs.transport.upload import UploadStrategy

DEVICE = DiscoveredDevice(mac="D0:EA:11:5D:65:E2", identity="MikroTik", interface_name="ether1")
LOCAL = LocalInterface("Ethernet", IPv4Address("192.168.55.55"),
                       IPv4Network("192.168.55.0/24"), index=7)
PARAMS = ConnectionParams(host="192.168.55.254", username="admin", password="pw")
IMPORT = ApplyOptions(ApplyMode.IMPORT)


def make_session(**fields) -> DeviceSession:
    return DeviceSession(PARAMS, FakeTransport(), make_inventory("7.20.7", FIVE_PORTS), **fields)


ROUTE = MacRoute(DEVICE, LOCAL, IPv4Address("192.168.55.254"))


def by_mac(method: MacConnectMethod) -> DeviceSession:
    return make_session(mac_method=method.value, mac_route=ROUTE)


def order(session: DeviceSession) -> list[UploadStrategy]:
    uploader = build_uploader_factory(session, IMPORT)(session.transport)
    # HTTP + fetch depends on this PC's routing table; it is always last anyway.
    return [s.name for s in uploader.strategies if s.name != UploadStrategy.HTTP_FETCH]


def test_over_mac_telnet_files_go_through_the_console_first():
    """The console is the one path known to work; SSH over the lent address may not.

    On 2026-09-21 a MAC session failed its upload because SSH never answered on the
    address lent to the device, and /tool fetch is off under device-mode.
    """
    assert order(by_mac(MacConnectMethod.MAC_TELNET)) == [
        UploadStrategy.MAC_TELNET, UploadStrategy.SFTP, UploadStrategy.SCP]


def test_over_link_local_ssh_goes_first_and_the_console_is_a_fallback():
    assert order(by_mac(MacConnectMethod.LINK_LOCAL)) == [
        UploadStrategy.SFTP, UploadStrategy.SCP, UploadStrategy.MAC_TELNET]


def test_a_session_by_address_has_no_console():
    assert order(make_session()) == [UploadStrategy.SFTP, UploadStrategy.SCP]


class FakeMacSession:
    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail
        self.closed = False

    def authenticate(self) -> None:
        if self.fail is not None:
            raise self.fail

    def close(self) -> None:
        self.closed = True


def test_release_closes_the_console():
    session = by_mac(MacConnectMethod.MAC_TELNET)
    uploader = build_uploader_factory(session, IMPORT)(session.transport)
    strategy = next(s for s in uploader.strategies if isinstance(s, MacTelnetStrategy))
    opened = FakeMacSession()
    strategy.console.factory = lambda device, params, local: opened  # type: ignore[attr-defined]
    assert strategy.console() is opened
    uploader.release()
    assert opened.closed


def test_a_console_that_refused_the_login_is_not_tried_again():
    made = []

    def factory(device, params, local_address):
        made.append(FakeMacSession(MacTelnetAuthError("login failed")))
        return made[-1]

    console = LazyConsole(DEVICE, PARAMS, "192.168.55.55", factory=factory)
    for _ in range(2):
        with pytest.raises(MacTelnetAuthError):
            console()
    assert len(made) == 1 and made[0].closed


def test_an_ssh_port_that_did_not_answer_is_not_tried_for_every_file(monkeypatch):
    tried = []

    class DeadSsh:
        def __init__(self, params):
            self.params = params

        def connect(self):
            tried.append(self.params.port)
            raise TransportError("SSH connect to 192.168.55.254:22 timed out")

    monkeypatch.setattr(session_mod, "SshTransport", DeadSsh)
    ssh = LazySsh(PARAMS)
    for _ in range(3):  # SFTP and SCP, for each file
        with pytest.raises(TransportError):
            ssh.open_sftp()
    assert len(tried) == 1


@pytest.mark.parametrize(("method", "host", "expected_host"), [
    (MacConnectMethod.MAC_TELNET, "192.168.55.254", "192.168.55.254"),
    (MacConnectMethod.LINK_LOCAL, "fe80::1%7", "fe80::1%7"),
    (MacConnectMethod.MAC_TELNET, None, "192.168.55.254"),
])
def test_open_mac_session_keeps_the_way_back_to_the_console(method, host, expected_host):
    transport = FakeTransport()
    seen = []

    def mac_connect(device, params, address, interface, **kwargs):
        return MacConnectResult(ConnectOutcome(transport), method,
                                temp_address="192.168.55.254/24", host=host)

    session = open_mac_session(
        DEVICE, PARAMS, IPv4Address("192.168.55.254"), LOCAL,
        on_connected=seen.append, mac_connect=mac_connect,
        discover=lambda t, on_step: make_inventory("7.20.7", FIVE_PORTS),
        probe=lambda t, version: {}, session_port=lambda t, h: "ether1",
    )
    assert seen == [transport]
    assert session.params.host == expected_host
    assert session.mac_route == ROUTE, "the way back in by MAC, and to the console"
    assert session.reached_by_mac == ROUTE
    assert session.mac_method == method.value
    assert session.temp_address == "192.168.55.254/24"


class Stop(Exception):
    pass


@pytest.mark.parametrize("fails_in", ["on_connected", "discover"])
def test_a_mac_session_that_fails_to_open_gives_the_lent_address_back(fails_in):
    """A connect cancelled in the window left 192.168.55.254 on a v6 hEX S (2026-09-25)."""
    transport = FakeTransport()
    transport.connect()

    def mac_connect(device, params, address, interface, **kwargs):
        return MacConnectResult(ConnectOutcome(transport), MacConnectMethod.MAC_TELNET,
                                temp_address="192.168.55.254/24", host="192.168.55.254")

    def fail(*args, **kwargs):
        raise Stop

    ok = lambda t, on_step: make_inventory("7.20.7", FIVE_PORTS)  # noqa: E731
    with pytest.raises(Stop):
        open_mac_session(
            DEVICE, PARAMS, IPv4Address("192.168.55.254"), LOCAL, mac_connect=mac_connect,
            on_connected=fail if fails_in == "on_connected" else lambda t: None,
            discover=fail if fails_in == "discover" else ok,
            probe=lambda t, version: {}, session_port=lambda t, h: "ether1",
        )
    removed = transport.commands[0]
    assert (removed.path, removed.verb) == ("/ip address", "remove")
    assert removed.selector.get("address") == "192.168.55.254/24"
    assert not transport.connected


def test_a_mac_session_whose_connection_is_gone_is_only_closed():
    transport = FakeTransport()  # never connected: nothing to give the address back over

    def mac_connect(device, params, address, interface, **kwargs):
        return MacConnectResult(ConnectOutcome(transport), MacConnectMethod.MAC_TELNET,
                                temp_address="192.168.55.254/24", host="192.168.55.254")

    def lost(t, on_step):
        raise TransportError("api-ssl transport is not connected")

    with pytest.raises(TransportError):
        open_mac_session(DEVICE, PARAMS, IPv4Address("192.168.55.254"), LOCAL,
                         mac_connect=mac_connect, discover=lost)
    assert transport.commands == []


def connected(session: DeviceSession) -> DeviceSession:
    session.transport.connect()
    return session


def test_hanging_up_gives_the_lent_address_back():
    """Nothing did before: after an import the device kept the address for good."""
    session = connected(make_session(temp_address="192.168.55.254/24"))
    end_session(session)
    removed = session.transport.commands[0]
    assert removed.path == "/ip address" and removed.verb == "remove"
    assert removed.selector.get("address") == "192.168.55.254/24"
    assert session.temp_address is None
    assert not session.transport.connected


def test_hanging_up_with_nothing_lent_only_closes():
    session = connected(make_session())
    end_session(session)
    assert session.transport.commands == [] and not session.transport.connected


class RefusingTransport(FakeTransport):
    def execute_command(self, command):
        raise CommandError("/ip/address/remove", "failure: not allowed")


@pytest.mark.parametrize("still_connected", [True, False])
def test_an_address_that_cannot_be_given_back_is_reported(still_connected):
    session = DeviceSession(PARAMS, RefusingTransport(), make_inventory("7.20.7", FIVE_PORTS),
                            temp_address="192.168.55.254/24")
    if still_connected:
        session.transport.connect()
    with pytest.raises(TempAddressLeft) as err:
        end_session(session)
    assert err.value.address == "192.168.55.254/24"
    assert session.temp_address == "192.168.55.254/24"
    assert not session.transport.connected


def report(transport, temp_address):
    return ApplyReport(names=make_names(datetime(2026, 9, 21), False), transport=transport,
                       temp_address=temp_address)


def test_after_a_reset_the_session_carries_on_without_the_lent_address():
    session = make_session(temp_address="192.168.55.254/24")
    new = FakeTransport()
    new.params = ConnectionParams(host="192.168.55.68", username="admin", password="N3w")
    session_after_apply(session, report(new, None))
    assert session.transport is new
    assert session.params.password == "N3w"  # a later console login needs the new one
    assert session.temp_address is None


@pytest.mark.parametrize(("mac_method", "reached"), [
    ("mac-telnet", ROUTE),  # back in by MAC: still named by its MAC
    (None, None),  # back in by address: named by the address, the route kept for files
])
def test_after_an_apply_the_session_says_how_it_came_back(mac_method, reached):
    session = by_mac(MacConnectMethod.MAC_TELNET)
    new = FakeTransport()
    applied = report(new, "192.168.55.254/24" if mac_method else None)
    applied.mac_method = mac_method
    session_after_apply(session, applied)
    assert session.reached_by_mac == reached
    assert session.mac_route == ROUTE


def test_the_apply_knows_the_way_back_in_by_mac():
    ctx = build_apply_context(DeviceProfile(), None, IMPORT, by_mac(MacConnectMethod.MAC_TELNET),
                              on_event=lambda event: None,
                              uploader_factory_builder=lambda session, options: None)
    assert ctx.mac_route == ROUTE


def test_after_an_import_the_lent_address_is_still_to_give_back():
    session = make_session(temp_address="192.168.55.254/24")
    transport = session.transport
    session_after_apply(session, report(transport, "192.168.55.254/24"))
    assert session.transport is transport and session.params is PARAMS
    assert session.temp_address == "192.168.55.254/24"
