"""MAC-Telnet session and connect-by-MAC bootstrap against an in-memory fake device."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network, IPv6Address

import pytest

from fake_mactelnet_device import FakeChannel, FakeDevice
from fake_transport import FakeTransport
from mqs.transport.base import (
    AuthError,
    CommandTimeout,
    ConnectionLost,
    ConnectionParams,
    ServiceUnavailable,
    TransportError,
    TransportKind,
)
from mqs.transport.factory import ConnectFailed, ConnectOutcome
from mqs.transport.mac_connect import (
    REMOVAL_REPLY_S,
    TEMP_COMMENT,
    MacConnectMethod,
    bootstrap_interface,
    connect_by_mac,
    link_local_host,
    remove_temp_address,
)
from mqs.transport.mactelnet.client import (
    MacTelnetAuthError,
    MacTelnetError,
    MacTelnetSession,
)
from mqs.transport.mndp import DiscoveredDevice
from mqs.transport.netifs import LocalInterface

SRC_MAC = bytes.fromhex("aabbccddeeff")


def make_session(device: FakeDevice, password: str = "", **channel_kwargs) -> MacTelnetSession:
    channel = FakeChannel(device, **channel_kwargs)
    clock = _Clock()
    session = MacTelnetSession(channel, "dc:2c:6e:00:11:22", "admin", password,
                              src_mac=SRC_MAC, connect_timeout=5, clock=clock)
    session.channel = channel  # keep a handle for assertions
    return session


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 0.001  # time always advances so quiet-timeouts terminate
        return self.now


def test_authenticate_and_run_command():
    device = FakeDevice(password="s3cret")
    session = make_session(device, "s3cret")
    session.authenticate()
    assert device.authenticated
    output = session.run("/ip address add address=192.168.88.2/24 interface=ether1")
    assert device.commands == ["/ip address add address=192.168.88.2/24 interface=ether1"]
    assert output == "ok"  # the echoed command and the trailing prompt are stripped
    session.close()
    assert session.channel.closed


def test_ignores_own_broadcast_echo():
    device = FakeDevice(password="pw")
    session = make_session(device, "pw", echo_self=True)
    session.authenticate()
    assert device.authenticated


def test_wrong_password_reports_auth_failure_via_terminal():
    device = FakeDevice(password="right")
    session = make_session(device, "wrong")
    # The device ends auth only on a matching Cc; a wrong one never sends END_AUTHENTICATION.
    with pytest.raises(MacTelnetError):
        session.authenticate()
    assert not device.authenticated


def test_unregistered_user_salt_error():
    device = FakeDevice(password="pw")

    def bad_challenge():
        from mqs.transport.mactelnet.ecsrp import ServerChallenge
        real = FakeDevice.__dict__  # noqa: F841
        return ServerChallenge(b"\x00" * 32, 0, b"")

    device.server.challenge = bad_challenge  # type: ignore[assignment]
    session = make_session(device, "pw")
    with pytest.raises(MacTelnetAuthError):
        session.authenticate()


def test_run_requires_auth_and_detects_command_error():
    device = FakeDevice(password="",
                        command_reply=lambda line: "failure: already have such address\r\n")
    session = make_session(device)
    with pytest.raises(MacTelnetError, match="not authenticated"):
        session.run("x")
    session.authenticate()
    with pytest.raises(MacTelnetError, match="already have"):
        session.run("/ip address add address=1.2.3.4/24 interface=ether1")


def test_timeout_when_device_silent():
    device = FakeDevice()
    channel = FakeChannel(device)
    session = MacTelnetSession(channel, "dc:2c:6e:00:11:22", "admin", "",
                              src_mac=SRC_MAC, connect_timeout=0.01, clock=_Clock())
    channel.inbox.clear()
    channel.device = _SilentDevice()
    with pytest.raises(MacTelnetError, match="timed out"):
        session.authenticate()


class _SilentDevice:
    def handle(self, raw):
        return []


# ---------------------------------------------------------------- link-local


def device_with_ipv6(ipv6: str | None) -> DiscoveredDevice:
    return DiscoveredDevice(mac="DC:2C:6E:00:11:22", identity="R1", interface_name="ether5",
                            ipv6=IPv6Address(ipv6) if ipv6 else None,
                            local_interface="Ethernet")


LOCAL = LocalInterface("Ethernet", IPv4Address("192.168.88.254"),
                       IPv4Network("192.168.88.0/24"), index=12)


def test_link_local_host():
    device = device_with_ipv6("fe80::dc2c:6eff:fe00:1122")
    assert link_local_host(device, LOCAL) == "fe80::dc2c:6eff:fe00:1122%12"
    no_index = LocalInterface("Ethernet", IPv4Address("192.168.88.254"),
                              IPv4Network("192.168.88.0/24"), index=None)
    assert link_local_host(device, no_index) == "fe80::dc2c:6eff:fe00:1122%Ethernet"
    assert link_local_host(device_with_ipv6(None), LOCAL) is None
    assert link_local_host(device_with_ipv6("2001:db8::1"), LOCAL) is None  # global, not LL


# ---------------------------------------------------------------- connect_by_mac


PARAMS = ConnectionParams(host="", username="admin", password="pw", connect_timeout=5)


def fake_connect_factory(fail_hosts=(), auth_fail_hosts=()):
    calls: list[str] = []

    def connect(params, choice=None, **kwargs):
        calls.append(params.host)
        if params.host in auth_fail_hosts:
            raise AuthError("bad password")
        if params.host in fail_hosts:
            raise ConnectFailed([])
        return ConnectOutcome(FakeTransport({}))

    return connect, calls


def bootstrap_session_factory(device: FakeDevice):
    created = {}

    def factory(dev, params, local_address):
        session = make_session(device, params.password)
        created["session"] = session
        created["local"] = local_address
        return session

    return factory, created


def test_connect_uses_link_local_first():
    connect, calls = fake_connect_factory()
    result = connect_by_mac(device_with_ipv6("fe80::1"), PARAMS, IPv4Address("192.168.88.253"),
                            LOCAL, connect=connect)
    assert result.method == MacConnectMethod.LINK_LOCAL
    assert calls == ["fe80::1%12"]
    assert result.temp_address is None


def test_link_local_auth_error_is_not_retried():
    connect, _ = fake_connect_factory(auth_fail_hosts={"fe80::1%12"})
    with pytest.raises(AuthError):
        connect_by_mac(device_with_ipv6("fe80::1"), PARAMS, IPv4Address("192.168.88.253"),
                       LOCAL, connect=connect)


def test_link_local_unreachable_falls_back_to_bootstrap():
    device = FakeDevice(password="pw")
    factory, created = bootstrap_session_factory(device)
    connect, calls = fake_connect_factory(fail_hosts={"fe80::1%12"})
    steps: list[str] = []
    result = connect_by_mac(device_with_ipv6("fe80::1"), PARAMS, IPv4Address("192.168.88.253"),
                            LOCAL, connect=connect, session_factory=factory,
                            on_step=steps.append)
    assert result.method == MacConnectMethod.MAC_TELNET
    assert result.temp_address == "192.168.88.253/24"
    assert result.device_interface == "ether5"
    assert device.commands == [
        '/ip address remove [find comment="mqs-temp"]',  # leftovers from earlier attempts
        "/ip address add address=192.168.88.253/24 interface=ether5 comment=mqs-temp",
        ':put [:len [/ip address find comment="mqs-temp"]]',  # the bootstrap is confirmed
    ]
    assert calls == ["fe80::1%12", "192.168.88.253"]
    assert steps == ["link-local", "mac-telnet"]
    assert created["local"] == "192.168.88.254"
    assert result.transport.kind == TransportKind.API_SSL


def test_bootstrap_without_ipv6_goes_straight_to_mac_telnet():
    device = FakeDevice(password="pw")
    factory, _ = bootstrap_session_factory(device)
    connect, calls = fake_connect_factory()
    result = connect_by_mac(device_with_ipv6(None), PARAMS, IPv4Address("192.168.88.253"),
                            LOCAL, connect=connect, session_factory=factory)
    assert result.method == MacConnectMethod.MAC_TELNET
    assert calls == ["192.168.88.253"]


def test_the_ports_asked_for_are_used_on_every_path():
    """After an apply SSH is on 122, not 22: a reconnect by MAC has to know."""
    seen = []

    def connect(params, choice=None, **kwargs):
        seen.append((params.host, kwargs))
        if params.host.startswith("fe80"):
            raise ConnectFailed([])
        return ConnectOutcome(FakeTransport({}))

    device = FakeDevice(password="pw")
    factory, _ = bootstrap_session_factory(device)
    connect_by_mac(device_with_ipv6("fe80::1"), PARAMS, IPv4Address("192.168.88.253"), LOCAL,
                   connect=connect, session_factory=factory, api_port=8729, ssh_port=122)
    ports = {"api_port": 8729, "ssh_port": 122}
    assert seen == [("fe80::1%12", ports), ("192.168.88.253", ports)]


def test_the_address_goes_where_the_caller_says_once_mndp_is_out_of_date():
    device = FakeDevice(password="pw")
    factory, _ = bootstrap_session_factory(device)
    connect, _ = fake_connect_factory()
    result = connect_by_mac(device_with_ipv6(None), PARAMS, IPv4Address("192.168.88.253"),
                            LOCAL, connect=connect, session_factory=factory,
                            device_interface="bridge")
    assert result.device_interface == "bridge"
    assert "/ip address add address=192.168.88.253/24 interface=bridge comment=mqs-temp" \
        in device.commands


def test_bootstrap_auth_failure_propagates_and_closes():
    device = FakeDevice(password="right")
    session_holder = {}

    def factory(dev, params, local_address):
        session = make_session(device, "wrong")
        session_holder["s"] = session
        return session

    connect, _ = fake_connect_factory()
    with pytest.raises(MacTelnetError):
        connect_by_mac(device_with_ipv6(None), PARAMS, IPv4Address("192.168.88.253"),
                       LOCAL, connect=connect, session_factory=factory)
    assert session_holder["s"].channel.closed


def test_bootstrap_ip_set_but_device_unreachable():
    device = FakeDevice(password="pw")
    factory, _ = bootstrap_session_factory(device)
    connect, _ = fake_connect_factory(fail_hosts={"192.168.88.253"})
    with pytest.raises(MacTelnetError, match="not reachable"):
        connect_by_mac(device_with_ipv6(None), PARAMS, IPv4Address("192.168.88.253"),
                       LOCAL, connect=connect, session_factory=factory)


def test_remove_temp_address():
    transport = FakeTransport({})
    remove_temp_address(transport, "192.168.88.253/24")
    command = transport.commands[0]
    assert command.path == "/ip address" and command.verb == "remove"
    assert command.selector.get("comment") == TEMP_COMMENT
    assert command.selector.get("address") == "192.168.88.253/24"


def test_bootstrap_default_interface_when_unknown():
    device = FakeDevice(password="pw")
    factory, _ = bootstrap_session_factory(device)
    connect, _ = fake_connect_factory()
    plain = DiscoveredDevice(mac="DC:2C:6E:00:11:22", identity="R1")
    result = connect_by_mac(plain, PARAMS, IPv4Address("192.168.88.253"), LOCAL,
                            connect=connect, session_factory=factory)
    assert result.device_interface == "ether1"  # fallback
    added = next(c for c in device.commands if c.startswith("/ip address add"))
    assert "interface=ether1" in added


def test_service_unavailable_link_local_falls_back():
    device = FakeDevice(password="pw")
    factory, _ = bootstrap_session_factory(device)

    def connect(params, choice=None, **kwargs):
        if params.host.startswith("fe80"):
            raise ServiceUnavailable("no route")
        return ConnectOutcome(FakeTransport({}))

    result = connect_by_mac(device_with_ipv6("fe80::1"), PARAMS, IPv4Address("192.168.88.253"),
                            LOCAL, connect=connect, session_factory=factory)
    assert result.method == MacConnectMethod.MAC_TELNET


def test_bootstrap_interface_targets_the_bridge_not_the_port():
    """MNDP reports a bridged port as "<bridge>/<port>"; verified on a hEX S 2026-09-16.

    The temporary address belongs on the bridge: the reported name is not a valid
    interface name, and a slave port would not carry the address anyway.
    """
    assert bootstrap_interface("bridge/ether1") == "bridge"
    assert bootstrap_interface("bridge1/ether2-BEE-PC") == "bridge1"
    assert bootstrap_interface("ether5") == "ether5"
    assert bootstrap_interface(None) == "ether1"
    assert bootstrap_interface("") == "ether1"


@pytest.mark.parametrize("silence", [
    ConnectionLost("api-ssl timed out during /ip/address/remove"),
    CommandTimeout("SSH command timed out: /ip address remove ..."),
])
def test_remove_temp_address_tolerates_the_connection_dying(silence):
    """We remove the address we are connected through, so no answer is success: api-ssl
    reports the silence as a lost connection, SSH as a command timeout."""

    class DyingTransport(FakeTransport):
        def execute_command(self, command):
            super().execute_command(command)
            raise silence

    transport = DyingTransport()
    remove_temp_address(transport, "192.168.55.254/24")
    assert transport.commands


def test_remove_temp_address_does_not_wait_the_command_timeout():
    """The answer cannot come, and waiting 30 s for it held a disconnect up (2026-09-21)."""
    transport = FakeTransport({})
    remove_temp_address(transport, "192.168.55.254/24")
    assert transport.reply_timeouts == [REMOVAL_REPLY_S]
    assert transport.params.command_timeout / 3 > REMOVAL_REPLY_S


def test_prime_answers_the_licence_question():
    """RouterOS 7.13+ asks this on the first console login after a reset, and waits.

    Verified on a device 2026-09-20: until it is answered the console never shows a
    prompt, so every command sent afterwards is swallowed without a trace.
    """
    asked = []

    def reply(line: str) -> str:
        if not asked:
            asked.append(line)
            return "\r\n\r\nDo you want to see the software license? [Y/n]: "
        return (line + "\nok\n" if line else "") + "[admin@MikroTik] > "

    device = FakeDevice(password="pw", command_reply=reply)
    session = make_session(device, "pw")
    session.authenticate()
    assert session.run("/system identity print") == "ok"


# What 7.20.7 sends to a user without a password, captured 2026-09-21.
PASSWORD_CHANGE = ("  MikroTik RouterOS 7.20.7 (c) 1999-2026       https://www.mikrotik.com/\r\n"
                   "\r\n\r\nPress F1 for help\r\n\r\n\r\n\r\nChange your password\r\n"
                   "\r\r\r\r**************" + " " * 65 + "\rnew password> ")
AFTER_CTRL_C = ["\rnew password> \r\n",
                "\r\r\r\r\r[admin@MikroTik] >" + " " * 61 + "\r[admin@MikroTik] > "]


def empty_password_console(*, licence: bool = False):
    """A console that asks for a new password until Ctrl-C, and records what it was told."""
    state = {"asked": "licence" if licence else "password", "typed_as_password": []}

    def reply(line: str) -> str | list[str]:
        if state["asked"] == "licence":
            if line == "":
                return "\r\n\r\nDo you want to see the software license? [Y/n]: "
            state["asked"] = "password"
            return line + "\r\n" + PASSWORD_CHANGE
        if state["asked"] == "password":
            if line == "\x03":
                state["asked"] = None
                return AFTER_CTRL_C
            if line:
                state["typed_as_password"].append(line)
                return "*" * len(line) + "\r\nrepeat new password> "
            return PASSWORD_CHANGE
        return (line + "\nok\n" if line else "") + "[admin@MikroTik] > "

    return FakeDevice(password="", command_reply=reply), state


@pytest.mark.parametrize("licence", [False, True])
def test_the_password_change_is_skipped_and_nothing_is_typed_into_it(licence):
    """7.20.7 asks a user without a password for a new one at every console login.

    The question ends in ">" like the prompt. On 2026-09-21 the console took it for
    the prompt and typed two file-transfer commands in as "new password" and "repeat";
    only their being different kept the admin password from changing.
    """
    device, state = empty_password_console(licence=licence)
    session = make_session(device, "")
    session.authenticate()
    assert session.run(':put [/file get [find name="x.rsc"] size]') == "ok"
    assert state["typed_as_password"] == [], "a command went in as the new password"
    assert "\x03" in device.commands, "skipped with Ctrl-C, the password left as it was"


@pytest.mark.parametrize(("text", "asking"), [
    ("Change your password\r\n\rnew password> ", True),
    ("\r\nrepeat new password> ", True),
    ("\rnew password> \r\n", False),  # repeated after Ctrl-C, the console moved on
    ("[admin@MikroTik] > ", False),
    ("[admin@MikroTik] /password> ", False),  # a prompt, even inside /password
])
def test_a_password_question_is_told_from_the_prompt(text, asking):
    from mqs.transport.mactelnet.client import _password_question
    assert _password_question(text.rstrip(" ")) is asking


def test_wrapped_echo_is_still_recognised():
    """A command wider than the terminal comes back split across lines."""

    def reply(line: str) -> str:
        half = len(line) // 2
        return line[:half] + "\r\n" + line[half:] + "\nok\n[admin@MikroTik] > "

    device = FakeDevice(password="pw", command_reply=reply)
    session = make_session(device, "pw")
    session.authenticate()
    long_command = "/ip address add address=192.168.55.254/24 interface=ether1 comment=mqs-temp"
    assert session.run(long_command) == "ok"


SCROLLED_COMMAND = ':put [:typeof [:parse "/file read file=x offset=0 chunk-size=10"]]'
# What RouterOS 7.20.7 sent back for SCROLLED_COMMAND, captured over MAC-Telnet: an
# 80-column console scrolls the line sideways and never shows it whole.
SCROLLED_REPLY = (
    ':put [:typeof [:parse "/file read file=x offset=0 chunk-size\r[admin@MikroTik] > '
    ':put [:typeof [:parse "/file read file=x offset=0 chunk-siz>\r< "/file read file=x '
    'offset=0 chunk-size=                                      \r< "/file read file=x '
    'offset=0 chunk-size=10"]]\r< "/file read file=x offset=0 chunk-size=10"]]\r\n\rcode'
    '\r\n\r\r\r\r[admin@MikroTik] > ' + " " * 61 + '\r[admin@MikroTik] > '
)


def test_scrolled_echo_is_still_recognised():
    device = FakeDevice(password="pw", command_reply=lambda line: (
        SCROLLED_REPLY if line == SCROLLED_COMMAND else "[admin@MikroTik] > "))
    session = make_session(device, "pw")
    session.authenticate()
    assert session.run(SCROLLED_COMMAND) == "code"


def test_repeated_echo_lines_are_not_output():
    """Seen on 7.20.7: the finished line was redrawn twice, each on a line of its own."""
    command = ":put [/file get mqs-probe.txt size]"
    device = FakeDevice(password="pw", command_reply=lambda line: (
        f"[admin@MikroTik] > {line}\r\n[admin@MikroTik] > {line}\r\n25\r\n[admin@MikroTik] > "
        if line else "[admin@MikroTik] > "))
    session = make_session(device, "pw")
    session.authenticate()
    assert session.run(command) == "25"


def test_a_resent_packet_is_not_taken_as_new_output():
    """A device resends data whose ACK it missed; the copy is not more output."""

    def reply(line: str) -> str | list[str]:
        if not line:
            return "[admin@MikroTik] > "
        return [line + "\r\nok\r\n", "[admin@MikroTik] > "]

    device = FakeDevice(password="pw", command_reply=reply)
    session = make_session(device, "pw", resend_data=True)
    session.authenticate()
    assert session.run("/system identity print") == "ok"
    assert session.run("/system clock print") == "ok"


def test_run_rejects_a_command_it_could_not_confirm():
    """Silence is not success.

    The console swallows input while it is still printing its banner and log backlog.
    Treating that as a completed command is how a device once reported a bootstrap
    address as set while it had added nothing at all.
    """
    device = FakeDevice(password="pw", command_reply=lambda line: "")
    session = make_session(device, "pw")
    session.authenticate()
    with pytest.raises(MacTelnetError, match="cannot be confirmed"):
        session.run("/ip address add address=1.2.3.4/24 interface=ether1")


def test_bootstrap_fails_when_the_device_did_not_add_the_address():
    def reply(line: str) -> str:
        found = "0" if line.startswith(":put") else "ok"  # the device added nothing
        return line + "\n" + found + "\n[admin@MikroTik] > "

    device = FakeDevice(password="pw", command_reply=reply)
    factory, _ = bootstrap_session_factory(device)
    connect, calls = fake_connect_factory()
    with pytest.raises(MacTelnetError, match="did not add the temporary address"):
        connect_by_mac(device_with_ipv6(None), PARAMS, IPv4Address("192.168.88.253"),
                       LOCAL, connect=connect, session_factory=factory)
    assert calls == []  # never claims reachability for an address that was not added


def test_temp_address_is_taken_back_when_the_device_is_unreachable_on_it():
    device = FakeDevice(password="pw")
    factory, _ = bootstrap_session_factory(device)

    def connect(params, choice=None, **kwargs):
        raise TransportError("no transport available")

    with pytest.raises(MacTelnetError, match="removed again"):
        connect_by_mac(device_with_ipv6(None), PARAMS, IPv4Address("192.168.88.253"),
                       LOCAL, connect=connect, session_factory=factory)
    assert any("remove" in command for command in device.commands)
