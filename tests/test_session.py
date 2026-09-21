"""The device-session wiring shared by the window and the CLI: how files get moved."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network

import pytest

from factories import FIVE_PORTS, make_inventory
from fake_transport import FakeTransport
from mqs.apply import session as session_mod
from mqs.apply.plan import ApplyMode, ApplyOptions
from mqs.apply.session import (
    DeviceSession,
    LazyConsole,
    LazySsh,
    build_uploader_factory,
    open_mac_session,
)
from mqs.transport.base import ConnectionParams, TransportError
from mqs.transport.factory import ConnectOutcome
from mqs.transport.mac_connect import MacConnectMethod, MacConnectResult
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


def by_mac(method: MacConnectMethod) -> DeviceSession:
    return make_session(mac_method=method.value, mac_device=DEVICE,
                        mac_local_address=str(LOCAL.address))


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
    assert session.mac_device is DEVICE and session.mac_local_address == "192.168.55.55"
    assert session.mac_method == method.value
    assert session.temp_address == "192.168.55.254/24"
