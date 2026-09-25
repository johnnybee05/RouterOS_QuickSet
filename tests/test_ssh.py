"""SSH transport against a fake paramiko client (no device needed)."""

from __future__ import annotations

from typing import ClassVar

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
    TransportKind,
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


def rsa_key() -> paramiko.RSAKey:
    return paramiko.RSAKey.generate(1024)


def connected(responses=None, params=PARAMS) -> tuple[SshTransport, FakeClient]:
    client = FakeClient(responses)
    transport = SshTransport(params, client_factory=lambda: client,
                             key_loader=lambda path: f"key from {path}")
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


def test_the_key_is_loaded_by_the_transport_not_by_paramiko():
    # Given key_filename, paramiko reports a refused key as what its last key class said
    # about the file: "encountered RSA key, expected OPENSSH key" (RouterOS 6.49.10).
    params = ConnectionParams(host="h", username="bee", key_filename="id_rsa")
    _, client = connected(params=params)
    assert client.connect_kwargs["pkey"] == "key from id_rsa"
    assert "key_filename" not in client.connect_kwargs
    assert client.connect_kwargs["disabled_algorithms"] is None


def test_an_unreadable_key_is_an_auth_error():
    def missing(path):
        raise FileNotFoundError(2, "No such file or directory", path)

    params = ConnectionParams(host="h", username="bee", key_filename="C:/nowhere/id_rsa")
    transport = SshTransport(params, client_factory=FakeClient, key_loader=missing)
    with pytest.raises(AuthError, match="SSH key C:/nowhere/id_rsa"):
        transport.connect()


class RefusesSha512(FakeClient):
    """RouterOS 6.49.10: no server-sig-algs, rsa-sha2-512 refused, rsa-sha2-256 accepted."""

    made: ClassVar[list[RefusesSha512]] = []

    def __init__(self) -> None:
        super().__init__()
        RefusesSha512.made.append(self)

    def connect(self, **kwargs) -> None:
        self.connect_kwargs = kwargs
        disabled = (kwargs.get("disabled_algorithms") or {}).get("pubkeys", [])
        if "rsa-sha2-512" not in disabled:
            raise paramiko.AuthenticationException("Authentication failed.")


def test_a_refused_rsa_key_is_tried_again_without_sha512():
    RefusesSha512.made = []
    params = ConnectionParams(host="h", username="bee", key_filename="id_rsa")
    transport = SshTransport(params, client_factory=RefusesSha512,
                             key_loader=lambda path: rsa_key())
    transport.connect()
    first, second = RefusesSha512.made
    assert first.closed and first.connect_kwargs["disabled_algorithms"] is None
    assert second.connect_kwargs["disabled_algorithms"] == {"pubkeys": ["rsa-sha2-512"]}
    assert transport.connected


def test_a_key_refused_both_ways_is_an_auth_error():
    client = FakeClient(connect_error=paramiko.AuthenticationException("no"))
    params = ConnectionParams(host="h", username="bee", key_filename="id_rsa")
    transport = SshTransport(params, client_factory=lambda: client,
                             key_loader=lambda path: rsa_key())
    with pytest.raises(AuthError):
        transport.connect()
    assert client.connect_kwargs["disabled_algorithms"] == {"pubkeys": ["rsa-sha2-512"]}


@pytest.mark.parametrize("pkey", [None, "an ed25519 key"])
def test_only_an_rsa_key_is_tried_twice(pkey):
    calls = []

    def factory():
        calls.append(1)
        return FakeClient(connect_error=paramiko.AuthenticationException("no"))

    key = "id_ed25519" if pkey else None
    params = ConnectionParams(host="h", username="bee", password="pw", key_filename=key)
    transport = SshTransport(params, client_factory=factory, key_loader=lambda path: pkey)
    with pytest.raises(AuthError):
        transport.connect()
    assert len(calls) == 1


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
    with pytest.raises(expected) as err:
        transport.connect()
    assert client.closed and not transport.connected
    if expected is AuthError:
        assert err.value.kind == TransportKind.SSH
    assert transport.peer_security == PeerSecurity.UNKNOWN


def test_the_log_is_read_with_its_messages():
    transport, _ = connected({
        "/log print terse": (
            b"20:04:17 system,info system identity changed by bee+cet \r\n"
            b"20:08:06 script,error script error: failure: mstp requires vlan-filtering \r\n"),
    })
    rows = transport.execute("/log/print")
    assert rows[-1]["message"] == "script error: failure: mstp requires vlan-filtering"
    keyed, _ = connected({"/log print terse": b" 0 time=20:04:17 topics=system,info "
                                              b"message=identity changed\r\n"})
    assert keyed.execute("/log/print")[0]["message"] == "identity changed"


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


def test_console_inspect_column_table():
    # Shortened from 7.20.7 (hEX S); the empty NODE-TYPE cell is made up.
    transport, _ = connected({
        "/console inspect request=child path=ip,firewall,filter,add": (
            b"Columns: TYPE, NAME, NODE-TYPE\r\n"
            b"TYPE   NAME                       NODE-TYPE\r\n"
            b"self   add                        cmd      \r\n"
            b"child  chain                      \r\n"
            b"child  hw-offload                 arg      \r\n\r\n"
        ),
    })
    rows = transport.execute("/console/inspect", request="child", path="ip,firewall,filter,add")
    assert rows == [
        {"type": "self", "name": "add", "node-type": "cmd"},
        {"type": "child", "name": "chain", "node-type": ""},
        {"type": "child", "name": "hw-offload", "node-type": "arg"},
    ]
    # An unknown path prints a blank line: no rows, so the probe says "not confirmed".
    assert transport.execute("/console/inspect", request="child", path="ip,nonsense") == []


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
