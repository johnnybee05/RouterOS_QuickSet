"""SSH transport against a fake paramiko client (no device needed)."""

from __future__ import annotations

import paramiko
import pytest

from mqs.generator.ir import Find, RawLine, cmd
from mqs.transport.base import (
    AuthError,
    CommandError,
    CommandTimeout,
    ConnectionLost,
    ConnectionParams,
    ConnectTimeout,
    PeerSecurity,
    ServiceUnavailable,
)
from mqs.transport.ssh import SshTransport


class FakeFile:
    def __init__(self, data: bytes | BaseException) -> None:
        self.data = data

    def read(self) -> bytes:
        if isinstance(self.data, BaseException):
            raise self.data
        return self.data


class FakeKey:
    def get_fingerprint(self) -> bytes:
        return bytes([0xAB, 0xCD, 0x01])


class FakeParamikoTransport:
    def __init__(self, client: FakeClient) -> None:
        self.client = client

    def is_active(self) -> bool:
        return not self.client.closed

    def get_remote_server_key(self) -> FakeKey:
        return FakeKey()


class FakeClient:
    def __init__(self, responses=None, connect_error: BaseException | None = None) -> None:
        self.responses: dict[str, bytes | BaseException] = responses or {}
        self.connect_error = connect_error
        self.connect_kwargs: dict = {}
        self.commands: list[tuple[str, float]] = []
        self.closed = False
        self.policy = None

    def set_missing_host_key_policy(self, policy) -> None:
        self.policy = policy

    def connect(self, **kwargs) -> None:
        self.connect_kwargs = kwargs
        if self.connect_error is not None:
            raise self.connect_error

    def exec_command(self, line: str, timeout: float):
        self.commands.append((line, timeout))
        response = self.responses.get(line, b"")
        if isinstance(response, paramiko.SSHException):
            raise response
        return None, FakeFile(response), FakeFile(b"")

    def get_transport(self) -> FakeParamikoTransport:
        return FakeParamikoTransport(self)

    def open_sftp(self) -> str:
        return "sftp"

    def close(self) -> None:
        self.closed = True


PARAMS = ConnectionParams(host="192.168.88.1", username="admin", password="pw",
                          connect_timeout=3, command_timeout=7)


def connected(responses=None, params=PARAMS) -> tuple[SshTransport, FakeClient]:
    client = FakeClient(responses)
    transport = SshTransport(params, client_factory=lambda: client)
    transport.connect()
    return transport, client


def test_connect_uses_cet_suffix_and_timeouts():
    transport, client = connected()
    kwargs = client.connect_kwargs
    assert kwargs["username"] == "admin+cet"
    assert kwargs["port"] == 22
    assert kwargs["timeout"] == kwargs["auth_timeout"] == kwargs["banner_timeout"] == 3
    assert kwargs["look_for_keys"] is False and kwargs["allow_agent"] is False
    assert isinstance(client.policy, paramiko.AutoAddPolicy)
    assert transport.connected
    assert transport.peer_security == PeerSecurity.SSH_HOST_KEY
    assert transport.host_key_fingerprint == "ab:cd:01"
    transport.close()
    transport.close()
    assert not transport.connected


def test_custom_port_after_apply():
    params = ConnectionParams(host="h", username="u", port=122)
    _, client = connected(params=params)
    assert client.connect_kwargs["port"] == 122


@pytest.mark.parametrize(("password", "key", "passed"), [
    # admin after a no-defaults reset: paramiko tries no password at all for None, and
    # failed with "No authentication methods available" (2026-09-21). "" logs in on 7.20.7.
    ("", None, ""),
    ("", "id_rsa", None),  # a key and no password: only the key is tried
    ("pw", "id_rsa", "pw"),
])
def test_an_empty_password_is_still_tried(password, key, passed):
    params = ConnectionParams(host="h", username="admin", password=password, key_filename=key)
    _, client = connected(params=params)
    assert client.connect_kwargs["password"] == passed


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (paramiko.AuthenticationException("no"), AuthError),
        (TimeoutError("slow"), ConnectTimeout),
        (ConnectionRefusedError("closed"), ServiceUnavailable),
        (paramiko.SSHException("banner"), ServiceUnavailable),
    ],
)
def test_connect_error_mapping(error, expected):
    client = FakeClient(connect_error=error)
    transport = SshTransport(PARAMS, client_factory=lambda: client)
    with pytest.raises(expected):
        transport.connect()
    assert client.closed and not transport.connected
    assert transport.peer_security == PeerSecurity.UNKNOWN


def test_print_singleton_and_list():
    transport, client = connected({
        "/system resource print": (
            b"   version: 7.15.3 (stable)\r\n\x1b[32mboard-name\x1b[0m: RB4011\r\n"
        ),
        "/interface print terse": (
            b" 0  R name=ether1 type=ether\r\n 1    name=ether2 type=ether\r\n"
        ),
        "/interface bridge port print detail terse": b" 0 name=x hw-offload=yes\r\n",
    })
    assert transport.execute("/system/resource/print") == [
        {"version": "7.15.3 (stable)", "board-name": "RB4011"}]
    ports = transport.execute("/interface/print")
    assert [p["name"] for p in ports] == ["ether1", "ether2"]
    assert transport.execute("/interface/bridge/port/print", detail=True)[0]["hw-offload"] == "yes"
    assert client.commands[0] == ("/system resource print", 7)


def test_set_command_line_and_quoting():
    transport, client = connected()
    assert transport.execute("/ip/service/set", numbers="ssh", port=122) == []
    assert transport.execute("/system/identity/set", name="PV R1") == []
    transport.execute_command(cmd("/interface bridge port", "set",
                                  selector=Find.by(interface="ether2"), hw=True))
    transport.execute_raw(RawLine("set foo=bar", "/ip proxy"))
    transport.execute("/import", file_name="flash/quickset.rsc")
    transport.execute("/export", file="pre quickset")
    assert [c for c, _ in client.commands] == [
        "/ip service set ssh port=122",
        '/system identity set name="PV R1"',
        "/interface bridge port set [ find interface=ether2 ] hw=yes",
        "/ip proxy set foo=bar",
        "/import file-name=flash/quickset.rsc",
        '/export file="pre quickset"',
    ]


def test_command_errors_and_timeouts():
    transport, _ = connected({
        "/ip dns set servers=x": b"invalid value for argument address\r\n",
        "/slow print terse": TimeoutError("t"),
        "/gone print terse": paramiko.SSHException("closed"),
    })
    with pytest.raises(CommandError) as err:
        transport.execute("/ip/dns/set", servers="x")
    assert err.value.message.startswith("invalid value")
    with pytest.raises(CommandTimeout):
        transport.execute("/slow/print")
    with pytest.raises(ConnectionLost):
        transport.execute("/gone/print")
    with pytest.raises(ValueError):
        transport.run("/a print\n/b print")


def test_not_connected():
    transport = SshTransport(PARAMS, client_factory=FakeClient)
    with pytest.raises(ConnectionLost):
        transport.run("/system identity print")
    with pytest.raises(ConnectionLost):
        transport.open_sftp()
    with pytest.raises(ConnectionLost):
        transport.paramiko_transport()
    connected_transport, _ = connected()
    assert connected_transport.open_sftp() == "sftp"
    assert connected_transport.paramiko_transport().is_active()
