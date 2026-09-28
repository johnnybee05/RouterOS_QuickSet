"""Moving a file over an address lent to a device that has no IP path of its own.

The case is a device reached over IPv6 link-local: its SSH is closed to an address
outside the management subnet, the MAC-Telnet console moves at most 64 KiB, and a
RouterOS package is ten thousand times that (hEX S, 2026-09-28).
"""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import ClassVar

import pytest

from fake_transport import FakeTransport
from mqs.apply import session as session_mod
from mqs.apply.session import LentAddressStrategy
from mqs.transport.base import ConnectionParams, TransportError
from mqs.transport.mac_connect import TEMP_COMMENT, MacRoute
from mqs.transport.mndp import DiscoveredDevice
from mqs.transport.netifs import LocalInterface
from mqs.transport.upload import UploadStrategy

DEVICE = DiscoveredDevice(mac="B8:69:F4:83:6E:45", identity="MikroTik",
                          interface_name="bridge/ether1")
LOCAL = LocalInterface("Ethernet", IPv4Address("192.168.55.55"),
                       IPv4Network("192.168.55.0/24"), index=21)
ROUTE = MacRoute(DEVICE, LOCAL, IPv4Address("192.168.55.254"))
PARAMS = ConnectionParams(host="fe80::ba69:f4ff:fe83:6e45%21", username="admin", password="pw")
DATA = b"\xba\xd0\xf1\x1e" + bytes(4096)


class FakeConsole:
    """A MAC-Telnet console that answers the bootstrap's questions."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.opened = 0
        self.addresses = 0

    def __call__(self) -> FakeConsole:
        self.opened += 1
        return self

    def run(self, line: str, *, quiet: float = 0.0, timeout: float = 30.0,
            confirm: bool = True) -> str:
        self.lines.append(line)
        if line.startswith("/ip address add"):
            self.addresses += 1
        elif line.startswith("/ip address remove"):
            self.addresses = 0
        elif "/ip address find" in line:
            return str(self.addresses)
        elif "/interface find" in line:
            return "1"
        return ""


class FakeSsh:
    """An SSH transport whose SFTP writes into a dictionary."""

    files: ClassVar[dict[str, bytes]] = {}
    refuse: Exception | None = None
    made: ClassVar[list[ConnectionParams]] = []
    interfaces: ClassVar[list[dict[str, str]]] = [{"mac-address": DEVICE.mac}]

    def __init__(self, params: ConnectionParams) -> None:
        self.params = params
        self.closed = False
        FakeSsh.made.append(params)

    def connect(self) -> None:
        if FakeSsh.refuse is not None:
            raise FakeSsh.refuse

    def execute(self, path: str, /, **args: object) -> list[dict[str, str]]:
        assert path == "/interface/print"
        return list(FakeSsh.interfaces)

    def open_sftp(self) -> FakeSftp:
        return FakeSftp()

    def close(self) -> None:
        self.closed = True


class FakeSftp:
    def open(self, path: str, mode: str) -> FakeSftp:
        self.path = path
        return self

    def __enter__(self) -> FakeSftp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def write(self, data: bytes) -> None:
        FakeSsh.files[self.path] = data

    def stat(self, path: str) -> object:
        return type("Stat", (), {"st_size": len(FakeSsh.files[path])})()

    def get(self, remote: str, local: str) -> None:
        Path(local).write_bytes(FakeSsh.files[remote])

    def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def fake_ssh(monkeypatch):
    FakeSsh.files = {}
    FakeSsh.refuse = None
    FakeSsh.made = []
    FakeSsh.interfaces = [{"mac-address": DEVICE.mac}]
    monkeypatch.setattr(session_mod, "SshTransport", FakeSsh)
    return FakeSsh


def strategy(console: FakeConsole, transport: FakeTransport | None = None) -> LentAddressStrategy:
    return LentAddressStrategy(transport or FakeTransport(), console, ROUTE, PARAMS, 122)


def test_nothing_is_lent_until_a_file_needs_it():
    console = FakeConsole()
    strategy(console)
    assert console.opened == 0 and console.lines == []


def test_the_file_goes_over_the_lent_address():
    console = FakeConsole()
    strategy(console).upload(DATA, "routeros-7.23.7-mmips.npk")
    assert FakeSsh.files == {"routeros-7.23.7-mmips.npk": DATA}
    added = next(line for line in console.lines if line.startswith("/ip address add"))
    assert "address=192.168.55.254/24" in added and f"comment={TEMP_COMMENT}" in added
    assert [(p.host, p.port) for p in FakeSsh.made] == [("192.168.55.254", 122)]


def test_the_address_is_lent_once_for_every_file_of_an_apply():
    console = FakeConsole()
    lent = strategy(console)
    lent.upload(DATA, "routeros-7.23.7-mmips.npk")
    lent.upload(DATA, "wifi-qcom-7.23.7-mmips.npk")
    assert len([line for line in console.lines if line.startswith("/ip address add")]) == 1
    assert len(FakeSsh.made) == 1, "and one SSH connection for both"


def test_the_address_is_taken_back_when_the_uploader_is_released():
    console = FakeConsole()
    lent = strategy(console)
    lent.upload(DATA, "routeros-7.23.7-mmips.npk")
    lent.close()
    assert console.lines[-1] == f'/ip address remove [find comment="{TEMP_COMMENT}"]'
    assert console.addresses == 0
    assert FakeSsh.made[0] and FakeSsh.files  # the file went before the address came back


def test_a_device_that_will_not_take_the_address_says_so():
    class Deaf(FakeConsole):
        def run(self, line: str, **kwargs: object) -> str:
            super().run(line, **kwargs)  # type: ignore[arg-type]
            return "0" if "/ip address find" in line else ""

    with pytest.raises(TransportError, match="did not add the temporary address"):
        strategy(Deaf()).upload(DATA, "routeros-7.23.7-mmips.npk")


def test_ssh_that_answers_for_another_device_gets_no_file():
    """The lent address is free only as far as the user knows (2026-09-28)."""
    FakeSsh.interfaces = [{"mac-address": "00:0C:42:11:22:33"}]
    transport = FakeTransport()  # /tool fetch is not answered either
    with pytest.raises(TransportError) as raised:
        strategy(FakeConsole(), transport).upload(DATA, "routeros-7.23.7-mmips.npk")
    assert "answers for another device" in str(raised.value)
    assert FakeSsh.files == {}


def test_fetch_takes_over_when_ssh_stays_shut():
    FakeSsh.refuse = TransportError("SSH 192.168.55.254:122: No existing session")
    served: list[str] = []
    transport = FakeTransport()
    transport.execute = lambda path, /, **args: served.append(str(args.get("url"))) or []  # type: ignore[assignment]
    with pytest.raises(TransportError):  # the fake PC serves nothing, but it was asked
        strategy(FakeConsole(), transport).upload(DATA, "routeros-7.23.7-mmips.npk")
    assert served and served[0].startswith("http://192.168.55.55:"), \
        "the device fetches from this PC's own address on the network it was heard on"


def test_a_downloaded_file_comes_back_the_same_way(tmp_path):
    console = FakeConsole()
    lent = strategy(console)
    lent.upload(DATA, "flash/pre-quickset.backup")
    lent.download("flash/pre-quickset.backup", tmp_path / "pre-quickset.backup")
    assert (tmp_path / "pre-quickset.backup").read_bytes() == DATA


def test_the_strategy_says_how_the_file_went():
    assert LentAddressStrategy.name == UploadStrategy.LENT_ADDRESS
